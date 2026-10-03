import contextlib
import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

import wirely as blocks
from ..types import value_types
from ..program import RunStage, Variable, VariableBlock, InputSourceBinding
from ..types.time import MonotonicTime
from .time_snapshot import TimeSnapshot


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class NativeNodeContext:
    """Information available for the lifetime of a native node."""

    name: str
    parameters: Mapping[str, object]
    inputs: Mapping[str, object]
    configuration: object
    _engine: object = field(repr=False)
    _node_id: str = field(repr=False)

    def __post_init__(self):
        for name in ('parameters', 'inputs'):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))


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
    def parameters(self) -> Mapping[str, object]:
        return self.context.parameters

    @property
    def _shutdown_time(self) -> datetime.datetime | None:
        return self.context._engine.shutdown_time

    def trigger(self, name: str) -> None:
        context = self.context
        context._engine.trigger(context._node_id, name)

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


class VariableBlockInstance:
    def __init__(self, variable_block):
        if not isinstance(variable_block, VariableBlock):
            raise Exception('variable_block parameter is not a VariableBlock')
        self.variable_block = variable_block

        self.data = {}

    def add(self, variable, value):
        # print(f'Setting {variable}')
        if variable not in self.variable_block.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableBlock {self.variable_block.name}')
        if variable in self.data:
            raise Exception(f'Variable {variable} already set')
        self.data[variable] = variable.var_type.verify_value(value)

    def __getitem__(self, variable):
        # print(f'Getting {variable}')
        if variable not in self.variable_block.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableBlock {self.variable_block.name}')
        if variable not in self.data:
            raise Exception(f'Variable {variable} not yet set')
        return self.data[variable]


class ProcedureContext:
    __slots__ = (
        '_inputs', '_results', '_written_results', '_static',
        '_step_info', '_wakeup_time', '_wakeup_time_set',
    )

    def __init__(self, *, inputs: Mapping[str, object], results, static, step_info):
        self._inputs: Mapping[str, object] = MappingProxyType(dict(inputs))
        self._results = results
        self._written_results = set()
        self._static = static
        self._step_info = step_info
        self._wakeup_time = None
        self._wakeup_time_set = False

    @property
    def inputs(self) -> Mapping[str, object]:
        return self._inputs

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
    def parameters(self) -> Mapping[str, object]:
        return self._static.node_context.parameters

    @property
    def configuration(self):
        return self._static.node_context.configuration

    def start_time_for_stage(self, stage: 'RunStage') -> TimeSnapshot:
        if not isinstance(stage, RunStage):
            raise ValueError(f'Expected a RunStage, got {stage!r}')
        return self._step_info._stage_times[stage]

    def set_result(self, name: str, value: object):
        if not isinstance(name, str) or name not in self._static.results:
            raise ValueError(f'Unknown result name {name!r} in procedure on {self.name}')
        if name in self._written_results:
            raise ValueError(f'Result {name!r} on {self.name} was already written')
        try:
            value_type = self._static.results[name].variable.var_type
            value = value_type.verify_value(value_types._from_native(value_type, value))
        except Exception as exc:
            raise ValueError(f'Invalid value for result {name!r} on {self.name}: {exc}') from exc
        self._results[name] = value
        self._written_results.add(name)

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

        now_monotonic = blocks.MonotonicTime.current_time()
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
