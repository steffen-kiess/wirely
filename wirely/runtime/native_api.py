"""Base classes and services of typed native implementations.

The generated contract modules in wirely.contracts derive their classes
from these; their adapters convert between the runtime and implementations.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import datetime
from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeVar

from ..program import RunStage
from ..types import InternalValue, Type
from ..types.time import MonotonicTime
from . import signatures
from .native_values import from_native, to_native
from .resources import ResourceType, ResourceValue
from .time_snapshot import TimeSnapshot

K = TypeVar('K', bound=str)  # Python names of a native state's resource outputs.
F = TypeVar('F', bound=Callable[..., object])


class StateServices(Generic[K]):
    """Runtime operations available to a native state for its lifetime."""

    __slots__ = ('_name', '_output_keys', '_trigger', '_shutdown_time')

    def __init__(self, *, name: str, output_keys: Mapping[str, str], trigger: Callable[[str], None],
                 shutdown_time: Callable[[], datetime.datetime | None]) -> None:
        self._name = name
        self._output_keys = output_keys  # Binding keys by Python name.
        self._trigger = trigger
        self._shutdown_time = shutdown_time

    @property
    def name(self) -> str:
        """Dotted instance name, or qualified type name for a singleton."""
        return self._name

    def trigger(self, output: K) -> None:
        """Schedule each procedure that depends on the resource output with this Python name."""
        key = self._output_keys.get(output)
        if key is None:
            raise ValueError(f'Unknown output {output!r} on {self._name}')
        self._trigger(key)

    @property
    def shutdown_time(self) -> datetime.datetime | None:
        """When the current shutdown started, or None while running."""
        return self._shutdown_time()

    def __repr__(self) -> str:
        return f'StateServices(name={self._name!r})'


@dataclass(frozen=True, slots=True)
class PreparationServices:
    """Information available while a native procedure is prepared."""

    name: str  # Name of the procedure's node, as for StateServices.


class ProcedureServices:
    """Runtime operations available during one invocation of a native procedure."""

    __slots__ = ('_name', '_stage_times', '_trigger_self', '_wakeup_time', '_wakeup_time_set')

    def __init__(self, *, name: str, stage_times: Mapping[RunStage, TimeSnapshot],
                 trigger_self: Callable[[], None]) -> None:
        self._name = name
        self._stage_times = stage_times
        self._trigger_self = trigger_self
        self._wakeup_time: MonotonicTime | None = None
        self._wakeup_time_set = False

    @property
    def name(self) -> str:
        return self._name

    def start_time_for_stage(self, stage: RunStage) -> TimeSnapshot:
        """Return the time at which a stage of this step started."""
        if not isinstance(stage, RunStage):
            raise ValueError(f'Expected a RunStage, got {stage!r}')
        return self._stage_times[stage]

    def trigger_self(self) -> None:
        """Schedule this procedure again in the next step."""
        self._trigger_self()

    @property
    def wakeup_time(self) -> MonotonicTime | None:
        return self._wakeup_time

    def set_wakeup_time(self, deadline: MonotonicTime | None) -> None:
        """Request another invocation at a deadline; None cancels a previous one."""
        if self._wakeup_time_set:
            raise ValueError(f'Wakeup time on {self._name} was already set')
        if deadline is not None and not isinstance(deadline, MonotonicTime):
            raise ValueError(f'Expected a MonotonicTime or None, got {deadline!r}')
        self._wakeup_time = deadline
        self._wakeup_time_set = True

    def __repr__(self) -> str:
        return f'ProcedureServices(name={self._name!r})'


class NativeState(Generic[K]):
    """Base class of native states; contracts declare their initialize() method."""

    _adapter: ClassVar[InitializerAdapter]  # Set by the contract module.
    _services: StateServices[K]

    @property
    def services(self) -> StateServices[K]:
        return self._services

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        pass

    async def start_tasks_async(self, task_group: asyncio.TaskGroup,
                                async_exit_stack: contextlib.AsyncExitStack) -> None:
        pass

    @staticmethod
    @contextlib.contextmanager
    def start_task_context(task_group: asyncio.TaskGroup,
                           coroutine: Coroutine[Any, Any, object]) -> Iterator[asyncio.Task]:
        import wirely.misc.service

        task = task_group.create_task(wirely.misc.service.should_never_return(coroutine))
        try:
            yield task
        finally:
            task.cancel()

    @staticmethod
    def start_task(task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack,
                   coroutine: Coroutine[Any, Any, object]) -> asyncio.Task:
        return async_exit_stack.enter_context(NativeState.start_task_context(task_group, coroutine))


class NativeProcedure:
    """Base class of native procedures; contracts declare their prepare() and run() methods."""

    _adapter: ClassVar[ProcedureAdapter]  # Set by the contract module.


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class InterfaceBinding:
    """The resolved interface of one native initializer or procedure of one node."""

    where: str  # Describes the interface and node in errors.
    signature: signatures.Initializer | signatures.Procedure
    use_types: Mapping[str, Type]
    use_values: Mapping[str, InternalValue]
    resources: Mapping[str, ResourceValue]  # Resource dependencies by key.
    port_types: Mapping[str, Type]  # Value and event ports by key; events as Option.
    output_resource_types: Mapping[str, ResourceType]  # Resource outputs by key.

    def _port_signature(self, key: str) -> signatures.SignatureType:
        port = self.signature.depends.get(key) or self.signature.produces[key]
        assert not isinstance(port.type, signatures.ResourceType)
        return signatures.Option(port.type) if port.kind == 'event' else port.type

    def use(self, key: str) -> object:
        """Return a static value in native representation."""
        try:
            return to_native(self.signature.uses[key], self.use_types[key], self.use_values[key])
        except ValueError as error:
            raise ValueError(f'{self.where}: invalid uses binding {key!r}: {error}') from error

    def resource(self, key: str) -> object:
        return self.resources[key].object

    def input(self, inputs: Mapping[str, InternalValue], key: str) -> object:
        """Return an input value in native representation."""
        try:
            return to_native(self._port_signature(key), self.port_types[key], inputs[key])
        except ValueError as error:
            raise ValueError(f'{self.where}: invalid input {key!r}: {error}') from error

    def output(self, key: str, value: object) -> InternalValue:
        """Return the internal value of an output that an implementation returned."""
        try:
            return from_native(self._port_signature(key), self.port_types[key], value)
        except ValueError as error:
            raise ValueError(f'{self.where}: invalid value for output {key!r}: {error}') from error

    def resource_output(self, key: str, value: object) -> ResourceValue:
        try:
            return ResourceValue(self.output_resource_types[key], value)
        except TypeError as error:
            raise ValueError(f'{self.where}: invalid value for output {key!r}: {error}') from error

    def check_result(self, result: object, result_type: type) -> None:
        if type(result) is not result_type:
            raise ValueError(f'{self.where} must return a {result_type.__name__}, got {result!r}')


class InitializerAdapter(abc.ABC):
    """Calls the initialize() method of one contract's implementations."""

    output_keys: ClassVar[Mapping[str, str]]  # Binding keys of the resource outputs by Python name.

    def construct(self, state_class: type[NativeState[Any]], services: StateServices[Any],
                  binding: InterfaceBinding) -> tuple[NativeState[Any], dict[str, ResourceValue]]:
        """Construct and initialize a native state; return it and its resource outputs."""
        state = state_class()
        state._services = services
        return state, self.initialize(state, services, binding)

    @abc.abstractmethod
    def initialize(self, state: NativeState[Any], services: StateServices[Any],
                   binding: InterfaceBinding) -> dict[str, ResourceValue]:
        ...


class ProcedureAdapter(abc.ABC):
    """Calls the prepare() and run() methods of one contract's implementations."""

    @abc.abstractmethod
    def prepare(self, procedure: NativeProcedure, services: PreparationServices, binding: InterfaceBinding) -> None:
        ...

    @abc.abstractmethod
    def run(self, procedure: NativeProcedure, services: ProcedureServices, binding: InterfaceBinding,
            inputs: Mapping[str, InternalValue]) -> dict[str, InternalValue]:
        """Invoke a procedure with input values by key; return its output values by key."""


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class FunctionBinding:
    """The resolved signature of one runtime native function in a program."""

    name: str  # Native name.
    parameter_types: tuple[Type, ...]
    return_type: Type

    def argument(self, arguments: Sequence[InternalValue], index: int) -> object:
        return to_native(None, self.parameter_types[index], arguments[index])

    def result(self, value: object) -> InternalValue:
        try:
            return from_native(None, self.return_type, value)
        except ValueError as error:
            raise ValueError(f'Native function {self.name!r} returned an invalid value: {error}') from error


class FunctionContract(Generic[F]):
    """The contract of a runtime native function, whose implementations have type F."""

    __slots__ = ('name', '_call')

    def __init__(self, name: str, call: Callable[[F, FunctionBinding, Sequence[InternalValue]], InternalValue]) -> None:
        self.name = name  # Native name.
        self._call = call

    def call(self, implementation: F, binding: FunctionBinding, arguments: Sequence[InternalValue]) -> InternalValue:
        """Call an implementation with internal values and return its internal result."""
        return self._call(implementation, binding, arguments)

    def __repr__(self) -> str:
        return f'FunctionContract({self.name!r})'
