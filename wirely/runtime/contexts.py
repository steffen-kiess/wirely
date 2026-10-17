import contextlib
import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

import wirely
from ..types import value_types
from ..program import RunStage, Variable, VariableTable, InputSource
from ..types.time import MonotonicTime
from .time_snapshot import TimeSnapshot
from .resources import ResourceType, ResourceValue

if TYPE_CHECKING:
    from .engine import Engine


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class NativeNodeContext:
    """Information available for the lifetime of a native node."""

    name: str
    values: Mapping[str, object]
    inputs: Mapping[str, object]
    _engine: 'Engine' = field(repr=False)
    _node_id: str = field(repr=False)
    # Resource types of the outputs produced by the initializer, by key.
    _output_types: Mapping[str, ResourceType] = field(default_factory=dict, repr=False)
    _outputs: dict = field(default_factory=dict, repr=False)  # Resource outputs written by the initializer.
    _constructed: bool = field(default=False, repr=False)  # Whether the initializer has returned.

    def __post_init__(self):
        for name in ('values', 'inputs'):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))

    def set_output(self, name: str, value: object):
        if self._constructed:
            raise ValueError(f'Outputs of {self.name} can only be written by the native initializer')
        if not isinstance(name, str) or name not in self._output_types:
            raise ValueError(f'Unknown output name {name!r} in initializer on {self.name}')
        if name in self._outputs:
            raise ValueError(f'Output {name!r} on {self.name} was already written')
        try:
            self._outputs[name] = ResourceValue(self._output_types[name], value)
        except TypeError as error:
            raise ValueError(f'Invalid value for output {name!r} on {self.name}: {error}') from error

    def _finish_construction(self):
        """End the initializer's output phase and return the written outputs."""
        object.__setattr__(self, '_constructed', True)
        missing = self._output_types.keys() - self._outputs.keys()
        if missing:
            raise ValueError(f'Native initializer on {self.name} did not write outputs: {sorted(missing)!r}')
        return self._outputs


class NativeState:
    def __init__(self, *, context: NativeNodeContext):
        self._context = context

    @property
    def context(self) -> NativeNodeContext:
        return self._context

    @property
    def inputs(self) -> Mapping[str, object]:
        return self.context.inputs

    @property
    def values(self) -> Mapping[str, object]:
        return self.context.values

    @property
    def _shutdown_time(self) -> datetime.datetime | None:
        return self.context._engine.shutdown_time

    def trigger(self, output: str) -> None:
        """Schedule each procedure that depends on this state's resource `output`."""
        context = self.context
        if output not in context._output_types:
            raise ValueError(f'Unknown output name {output!r} on {context.name}')
        context._engine.trigger_dependents(context._node_id, output)

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        pass

    # TODO: Merge start_tasks and start_tasks_async?
    # TODO: Allow blocking behavior during startup?
    # TODO: Use an AsyncExitStack or an ExitStack?
    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        pass

    @staticmethod
    @contextlib.contextmanager
    def start_task_context(task_group, coroutine):
        import wirely.misc.service

        task = task_group.create_task(wirely.misc.service.should_never_return(coroutine))
        try:
            yield task
        finally:
            task.cancel()

    @staticmethod
    def start_task(task_group, async_exit_stack, coroutine):
        return async_exit_stack.enter_context(NativeState.start_task_context(task_group, coroutine))


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class ProcedureLoadContext:
    """Static information available when a native procedure is constructed."""

    name: str
    values: Mapping[str, object]
    resources: Mapping[str, ResourceValue]

    def __post_init__(self):
        for name in ('values', 'resources'):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))


class NativeProcedure:
    """Base class of native procedure implementations, constructed once per procedure at load time."""

    def __init__(self, *, context: ProcedureLoadContext):
        pass

    def __call__(self, *, context: 'ProcedureContext'):
        raise NotImplementedError


class VariableTableInstance:
    """Write-once runtime values stored in an aligned byte buffer."""

    def __init__(self, variable_table):
        """Allocate zeroed storage and track initialization separately."""
        if not isinstance(variable_table, VariableTable):
            raise Exception('variable_table parameter is not a VariableTable')
        self.variable_table = variable_table

        self.data = bytearray(variable_table.size)
        self._initialized = set()  # Variable identities, including zero-sized units.
        last = next(reversed(variable_table.offsets), None)
        # Final table padding follows the last slot in layout order.
        self._padding_start = 0 if last is None else variable_table.offsets[last] + last.var_type.size

    def add(self, variable, value):
        """Serialize a value into its slot, allowing one successful write."""
        if variable not in self.variable_table.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableTable {self.variable_table.name}')
        if variable in self._initialized:
            raise Exception(f'Variable {variable} already set')
        encoded = variable.var_type.serialize(value)
        offset = self.variable_table.offsets[variable]
        self.data[offset:offset + variable.var_type.size] = (
            encoded + bytes(variable.var_type.size - len(encoded))
        )
        self._initialized.add(variable)

    def __getitem__(self, variable):
        """Read an initialized value, validating its slot and table padding."""
        if variable not in self.variable_table.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableTable {self.variable_table.name}')
        if variable not in self._initialized:
            raise Exception(f'Variable {variable} not yet set')
        offset = self.variable_table.offsets[variable]
        end = offset + variable.var_type.data_size
        if any(self.data[end:offset + variable.var_type.size]) or any(self.data[self._padding_start:]):
            raise ValueError(f'Non-zero padding in VariableTable {self.variable_table.name}')
        return variable.var_type.deserialize(memoryview(self.data)[offset:end])


class ProcedureContext:
    __slots__ = (
        '_inputs', '_outputs', '_written_outputs', '_static', '_values',
        '_step_info', '_wakeup_time', '_wakeup_time_set', '_engine',
    )

    def __init__(self, *, inputs: Mapping[str, object], outputs, static, step_info, engine,
                 values: Mapping[str, object]):
        self._inputs: Mapping[str, object] = MappingProxyType(dict(inputs))
        self._values = values
        self._outputs = outputs
        self._written_outputs: set[str] = set()
        self._static = static
        self._step_info = step_info
        self._engine = engine
        self._wakeup_time = None
        self._wakeup_time_set = False

    @property
    def inputs(self) -> Mapping[str, object]:
        return self._inputs

    def trigger_self(self) -> None:
        self._engine._trigger_key(self._static.key)

    @property
    def wakeup_time(self) -> MonotonicTime | None:
        return self._wakeup_time

    def set_wakeup_time(self, deadline: MonotonicTime | None) -> None:
        if self._wakeup_time_set:
            raise ValueError(f'Wakeup time on {self.name} was already set')
        if deadline is not None and not isinstance(deadline, MonotonicTime):
            raise ValueError(f'Expected a MonotonicTime or None, got {deadline!r}')
        self._wakeup_time = deadline
        self._wakeup_time_set = True

    @property
    def name(self) -> str:
        return self._static.node_context.name

    @property
    def values(self) -> Mapping[str, object]:
        return self._values

    def start_time_for_stage(self, stage: 'RunStage') -> TimeSnapshot:
        if not isinstance(stage, RunStage):
            raise ValueError(f'Expected a RunStage, got {stage!r}')
        return self._step_info._stage_times[stage]

    def set_output(self, name: str, value: object):
        if not isinstance(name, str) or name not in self._static.outputs:
            raise ValueError(f'Unknown output name {name!r} in procedure on {self.name}')
        if name in self._written_outputs:
            raise ValueError(f'Output {name!r} on {self.name} was already written')
        try:
            value_type = self._static.outputs[name].variable.var_type
            value = value_type.verify_value(value_types._from_native(value_type, value))
        except Exception as exc:
            raise ValueError(f'Invalid value for output {name!r} on {self.name}: {exc}') from exc
        self._outputs[name] = value
        self._written_outputs.add(name)

    def __repr__(self):
        return f'ProcedureContext(name={self.name!r})'


class StepContext:
    def __init__(self):
        self._stage_times = {}
        self.stage_start_time_monotonic = {}
        self.stage_start_time_utc = {}
        self.stage_start_time = {}

    def start_stage(self, engine, stage):
        if stage in self.stage_start_time:
            raise Exception(f'Stage {stage} already started')

        now_monotonic = wirely.MonotonicTime.current_time()
        if engine.fake_monotonic_time_offset is not None:
            now_monotonic += engine.fake_monotonic_time_offset
        # print(f'StepContext for {stage} started at {now_monotonic}')

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        # Note: This should work with all python versions, even without PEP 495
        # <https://peps.python.org/pep-0495/>
        # Starting with Python 3.6 (PEP 495)
        # datetime.datetime.now().astimezone() should return the same value.
        now = now_utc.astimezone()

        self._stage_times[stage] = TimeSnapshot(
            monotonic=now_monotonic, utc=now_utc, local=now,
        )
        self.stage_start_time_monotonic[stage] = now_monotonic
        self.stage_start_time_utc[stage] = now_utc
        self.stage_start_time[stage] = now

    def __repr__(self):
        return f'StepContext({self.__dict__!r})'
