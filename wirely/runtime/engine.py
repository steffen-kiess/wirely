import asyncio
import contextlib
import datetime
import functools
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

import wirely
from wirely.program import InputSource, Procedure, Program, RunStage, Variable, VariableBank, validate_program
from wirely.runtime.contexts import StepContext, VariableTableInstance
from wirely.runtime.native_api import (
    InterfaceBinding, NativeProcedure, NativeState, PreparationServices, ProcedureAdapter, ProcedureServices,
    StateServices,
)
from wirely.runtime.extensions import RuntimeNativeExtension, resolve_extensions
from wirely.runtime.resources import ResourceValue
from wirely.runtime.signatures import Procedure as SignatureProcedure
from wirely.types import InternalValue, TypeResource
from wirely.types.time import Duration, MonotonicTime
from wirely.runtime.procedures import execute_procedure
from wirely.runtime.wall_clock import WallClockWaiter, WallWakeup


class Engine:
    def __init__(self, program: Program, *, extensions: Iterable[RuntimeNativeExtension] = (),
                 override_extensions: Iterable[RuntimeNativeExtension] = (),
                 include_builtin_extensions: bool = True) -> None:
        from .linking import (
            link_native_contracts, link_native_enums, link_native_functions,
            link_native_resource_types, link_native_signatures, link_native_structs, link_opaque_values,
        )

        if not isinstance(program, Program):
            raise Exception('program parameter is not a Program')

        extension = resolve_extensions(extensions, override_extensions=override_extensions,
                                       include_builtin_extensions=include_builtin_extensions)
        self.native_types = extension.native_types
        validate_program(program)
        self.program = program

        self.cache: VariableTableInstance | None = None  # Temporary values from the last completed step.

        self.debug = False

        # Set during cleanup only if all async tasks were started successfully.
        self.is_shutting_down = False
        # Wall time at the start of that cleanup, if known.
        self.shutdown_time: datetime.datetime | None = None

        # The time of the last complete async task shutdown. Should be used only for debugging / tests.
        self.last_shutdown_time: datetime.datetime | None = None

        self.triggered_procs: set[str] = set()  # Names of triggered procedures.
        self.triggered_procs_event = asyncio.Event()

        self.step_count = 0  # Number of completed steps.
        # While paused, run() starts no steps; step() can still be called directly.
        self.paused = False
        self._resumed_event = asyncio.Event()
        self._resumed_event.set()
        # Called after each step and when pausing or resuming, e.g. to notify control clients.
        self.status_listeners: set[Callable[[], None]] = set()

        self.wakeups: dict[str, MonotonicTime] = {}
        self.next_wakeup: MonotonicTime | None = None
        self.wall_wakeups: dict[str, WallWakeup] = {}

        # The current state, will be .old during the next step.
        self.state = VariableTableInstance(self.program.state_variables)
        # The state before the last step. Was .old during the last step.
        self.last_step_state: VariableTableInstance | None = None
        self.native_states = {}

        self.fake_monotonic_time_offset: Duration | None = None
        self._fake_monotonic_time_generation: int | None = None
        self._fake_wall_time: datetime.datetime | None = None
        self._has_fake_wall_time = False
        self.test_twon_fake_server_info: Any = None  # Set by the test runner for 2N tests.

        link_native_enums(program, extension)
        link_native_structs(program, extension)
        link_opaque_values(program, extension)
        link_native_signatures(program, extension)
        self.resource_types = link_native_resource_types(program, extension)
        linked_states, linked_procedures = link_native_contracts(program, extension)
        self.native_functions = link_native_functions(program, extension)
        self.fixed_values = VariableTableInstance(program.fixed_values)
        self.resource_values: dict[Variable, ResourceValue] = {}
        # Triggers follow resource object identity, including handles passed through load selectors.
        self._resource_triggers: dict[Variable, list[str]] = {}
        self._resource_origins: dict[Variable, frozenset[Variable]] = {}
        self._initializer_outputs: dict[str, Mapping[str, Variable]] = {}
        self._native_procedures: dict[int, tuple[NativeProcedure, ProcedureAdapter, InterfaceBinding]] = {}
        for proc in program.procs.get(RunStage.LOAD, ()):
            if proc.native is None:
                values = [self._fixed_input(source) for source in proc.inputs]
                output_values = execute_procedure(proc, values, self.native_functions)
            else:
                if proc.initializer:
                    linked = linked_states[proc.native.name]
                    interface = self._procedure_binding(proc, linked.signature)
                    self._initializer_outputs[proc.name] = {
                        key: target.variable for key, target in proc.named_outputs()
                        if target.variable.is_resource
                    }
                    services: StateServices[Any] = StateServices(
                        native_types=self.native_types, name=proc.node,
                        output_keys=linked.adapter.output_keys,
                        trigger=functools.partial(self._trigger_output, proc.name),
                        shutdown_time=lambda: self.shutdown_time,
                        is_shutting_down=lambda: self.is_shutting_down,
                    )
                    state, outputs = linked.adapter.construct(linked.factory, services, interface)
                    self.native_states[proc.name] = state
                else:
                    linked_procedure = linked_procedures[proc.native.name]
                    interface = self._procedure_binding(proc, linked_procedure.signature)
                    inputs = {key: self._fixed_input(source) for key, source in self._port_inputs(proc, interface)
                              if source.resource is None}
                    outputs = linked_procedure.adapter.load(
                        linked_procedure.factory(),
                        PreparationServices(native_types=self.native_types, name=proc.node),
                        interface, inputs,
                    )
                output_values = [outputs[key] for key in proc.native.output_names]
            for (index, target), value in zip(enumerate(proc.outputs), output_values):
                if target.variable.is_resource:
                    if not isinstance(value, ResourceValue):
                        raise ValueError(f'Resource output {index} did not return a resource')
                    if target.variable in self.resource_values:
                        raise ValueError('Resource is already initialized')
                    self.resource_values[target.variable] = value
                    origins = set()
                    if not proc.initializer:
                        for source in proc.inputs:
                            if source.resource is not None and self.resource_values[source.resource].object is value.object:
                                origins.update(self._resource_origins[source.resource])
                    self._resource_origins[target.variable] = frozenset(origins or (target.variable,))
                else:
                    self.fixed_values.add(target.variable, value)
        # Prepare runtime procedures only after the complete load schedule has run.
        for stage, schedule in program.procs.items():
            if stage == RunStage.LOAD:
                continue
            for proc in schedule:
                for source in proc.inputs:
                    if source.resource is not None:
                        for origin in self._resource_origins[source.resource]:
                            keys = self._resource_triggers.setdefault(origin, [])
                            if proc.name not in keys:
                                keys.append(proc.name)
                if proc.native is not None:
                    linked_procedure = linked_procedures[proc.native.name]
                    interface = self._procedure_binding(proc, linked_procedure.signature)
                    implementation = linked_procedure.factory()
                    linked_procedure.adapter.prepare(
                        implementation, PreparationServices(native_types=self.native_types, name=proc.node), interface)
                    self._native_procedures[id(proc)] = implementation, linked_procedure.adapter, interface
        for variable, initial in program.initial_state.items():
            self.state.add(variable, initial)

    def _fixed_input(self, source: InputSource) -> Any:
        if source.constant is not None:
            return source.constant.value
        if source.resource is not None:
            return self.resource_values[source.resource]
        assert source.bank == VariableBank.FIXED_VALUES and isinstance(source.variable, Variable)
        return self.fixed_values[source.variable]

    @staticmethod
    def _port_inputs(proc: Procedure, interface: InterfaceBinding) -> list[tuple[str, InputSource]]:
        """Return the named inputs of a native procedure that are not compile-time reads."""
        return [(name, source) for name, source in proc.named_inputs() if name not in interface.signature.uses]

    def _procedure_binding(self, proc: Procedure, signature: Any) -> InterfaceBinding:
        assert proc.native is not None
        uses = {name: source.constant for name, source in proc.named_inputs() if name in signature.uses}
        ports = [(name, source) for name, source in proc.named_inputs() if name not in uses]
        port_types = {name: source.value_type for name, source in ports if source.resource is None}
        port_types.update((name, target.variable.var_type) for name, target in proc.named_outputs()
                          if not target.variable.is_resource)
        return InterfaceBinding(
            native_types=self.native_types,
            where=f'Native {"initializer" if proc.initializer else "procedure"} `{proc.native.name}` of `{proc.node}`', signature=signature,
            use_types={key: constant.value_type for key, constant in uses.items() if constant is not None},
            use_values={key: cast(InternalValue, constant.value) for key, constant in uses.items() if constant is not None},
            resources={name: self.resource_values[source.resource] for name, source in ports
                       if source.resource is not None},
            port_types=port_types,
            output_resource_types={name: self.resource_types[cast(TypeResource, target.variable.var_type).native_name]
                                   for name, target in proc.named_outputs() if target.variable.is_resource},
            fixed_inputs={name: self._fixed_input(source) for name, source in ports
                          if source.resource is None
                          and source.bank in (VariableBank.FIXED_VALUES, VariableBank.CONST_VALUES)},
        )

    async def start_tasks_async(self, task_group: asyncio.TaskGroup,
                                async_exit_stack: contextlib.AsyncExitStack) -> None:
        def start_task_cleanup() -> None:
            now_monotonic = self.get_monotonic_time()

            now = self.get_wall_time()

            print(f'Task cleanup starting at {now}')

            # TODO: Set monotonic_shutdown_time?
            self.shutdown_time = now
            self.is_shutting_down = True

        def finish_task_cleanup() -> None:
            print('Task cleanup done')

            self.last_shutdown_time = self.shutdown_time
            self.shutdown_time = None
            self.is_shutting_down = False

        async_exit_stack.callback(finish_task_cleanup)

        # TODO: Currently this (e.g. connecting to knxd) is blocking and done sequentially. Should ths be non-blocking instead? (I.e. should the connection be created on the background task and should start_tasks_async() be removed?)
        for state in self.native_states.values():
            if state is not None:
                state.start_tasks(task_group, async_exit_stack)
                await state.start_tasks_async(task_group, async_exit_stack)

        async_exit_stack.callback(start_task_cleanup)

    # TODO: Threading?
    def trigger(self, node_id: str, proc_name: str) -> None:
        key = f'{node_id}:{proc_name}'
        if not isinstance(proc_name, str) or not any(
            proc.name == key for stage, schedule in self.program.procs.items() if stage != RunStage.LOAD for proc in schedule
        ):
            raise ValueError(f'Unknown procedure name `{proc_name}` on {node_id}')
        self._trigger_key(key)

    def _trigger_output(self, initializer: str, output: str) -> None:
        slot = self._initializer_outputs[initializer].get(output)
        if slot is None:
            raise ValueError(f'Output `{output}` is not a resource')
        if slot in self.resource_values:
            for key in self._resource_triggers.get(slot, ()):
                self._trigger_key(key)

    def _trigger_key(self, key: str) -> None:
        self.triggered_procs.add(key)
        self.triggered_procs_event.set()

    # Update self.next_wakeup to include wakeup_time
    def update_next_wakeup(self, now: MonotonicTime, wakeup_time: MonotonicTime | None) -> None:
        if wakeup_time is None:
            return
        if not wirely.MonotonicTime.is_comparable(now, wakeup_time):
            # A time from another generation cannot be waited for; it is due now.
            print(f'Warning: Got non-current wakeup time {wakeup_time}', flush=True)
            wakeup_time = now

        if (self.next_wakeup is None or not wirely.MonotonicTime.is_comparable(self.next_wakeup, wakeup_time)
                or wirely.MonotonicTime.compare(self.next_wakeup, wakeup_time) > 0):
            self.next_wakeup = wakeup_time

    def step(self) -> tuple[int, int, StepContext]:
        if self.debug:
            print(f'Starting step of {self.program.symbols.name}')
        self.next_wakeup = None
        cache = self.cache
        state_old = self.state  # Snapshot read throughout this step.
        # State produced by this step, published when it completes.
        state_new = VariableTableInstance(self.program.state_variables)
        variables = VariableTableInstance(self.program.temporaries)
        run_count = 0
        state_change_count = 0
        step_info = StepContext()
        for stage, stage_procs in self.program.procs.items():
            if stage == RunStage.LOAD:
                continue
            step_info.start_stage(self, stage)
            now = step_info.stage_start_time_monotonic[stage]
            for proc in stage_procs:
                key = proc.name
                values: list[InternalValue] = []
                skip_step = cache is not None and key not in self.triggered_procs
                for input_source in proc.inputs:
                    if input_source.resource is not None:
                        continue
                    if input_source.bank == VariableBank.CONST_VALUES:
                        # Constants never change.
                        assert input_source.constant is not None
                        value = input_source.constant.value
                        values.append(cast(InternalValue, value))
                        continue
                    variable = input_source.variable
                    assert isinstance(variable, Variable)
                    if input_source.bank == VariableBank.FIXED_VALUES:
                        # Fixed values never change after loading.
                        value = self.fixed_values[variable]
                    elif input_source.bank == VariableBank.TEMPORARIES:
                        value = variables[variable]
                        if skip_step:
                            assert cache is not None
                            if not variable.var_type.equals(value, cache[variable]):
                                skip_step = False
                    elif input_source.bank == VariableBank.NEW_STATE:
                        value = state_new[variable]
                        # The old state is the previous step's new state.
                        if skip_step and not variable.var_type.equals(value, state_old[variable]):
                            skip_step = False
                    else:
                        value = state_old[variable]
                        if skip_step:
                            assert self.last_step_state is not None
                            if not variable.var_type.equals(value, self.last_step_state[variable]):
                                skip_step = False
                    values.append(cast(InternalValue, value))
                if key in self.wakeups:
                    deadline = self.wakeups[key]
                    if not wirely.MonotonicTime.is_comparable(now, deadline) or wirely.MonotonicTime.compare(now, deadline) >= 0:
                        skip_step = False
                if key in self.wall_wakeups and self.wall_wakeups[key].is_due(step_info._stage_times[stage].local):
                    skip_step = False
                if skip_step:
                    assert cache is not None
                    for output in proc.outputs:
                        variable = output.variable
                        assert isinstance(variable, Variable)
                        if output.bank == VariableBank.NEW_STATE:
                            state_new.add(variable, state_old[variable])
                        else:
                            variables.add(variable, cache[variable])
                    if key in self.wakeups:
                        self.update_next_wakeup(now, self.wakeups[key])
                    continue
                if self.debug:
                    print(f'Running procedure {proc.name}')
                run_count += 1
                self.triggered_procs.discard(key)
                if proc.native is not None:
                    implementation, adapter, binding = self._native_procedures[id(proc)]
                    services = ProcedureServices(name=proc.node, native_types=self.native_types,
                                                 stage_times=step_info._stage_times,
                                                 trigger_self=functools.partial(self._trigger_key, key))
                    value_names = [name for name, source in proc.named_inputs() if source.resource is None]
                    inputs = {name: value for name, value in zip(value_names, values)
                              if name not in binding.signature.uses}
                    outputs = adapter.run(implementation, services, binding, inputs)
                    output_values = [outputs[name] for name in proc.native.output_names]
                    wakeup_time = services.wakeup_time
                    wall_wakeup = services.wall_wakeup
                else:
                    output_values = execute_procedure(proc, values, self.native_functions)
                    wakeup_time = None
                    wall_wakeup = None
                if wall_wakeup is None:
                    self.wall_wakeups.pop(key, None)
                else:
                    self.wall_wakeups[key] = wall_wakeup
                if wakeup_time is None:
                    self.wakeups.pop(key, None)
                else:
                    self.wakeups[key] = wakeup_time
                self.update_next_wakeup(now, wakeup_time)
                for output, value in zip(proc.outputs, output_values):
                    variable = output.variable
                    assert isinstance(variable, Variable)
                    if output.bank == VariableBank.NEW_STATE:
                        state_new.add(variable, value)
                        changed = not variable.var_type.equals(state_old[variable], value)
                        state_change_count += changed
                    else:
                        variables.add(variable, value)
        self.cache = variables
        self.last_step_state = state_old
        self.state = state_new
        self.step_count += 1
        if self.debug:
            print(f'Finished step of {self.program.symbols.name}')
        self._status_changed()
        return run_count, state_change_count, step_info

    async def run(self) -> None:
        wall_waiter = WallClockWaiter(self.get_wall_time, self.triggered_procs_event.set)
        try:
            while True:
                await asyncio.sleep(0)
                await self._resumed_event.wait()
                run_count, state_change_count, step_info = self.step()
                if state_change_count != 0:
                    continue
                now = self.get_monotonic_time()
                # TODO: If there is a "while True" here, the code will crash (instead of looping) because of EAGAIN when writing to the terminal, seems like NONBLOCK is set
                # while True:
                # import os; import fcntl; fcntl.fcntl(1, fcntl.F_GETFL)
                # print(f'Waiting, wakeup is {self.next_wakeup}', flush=True)
                # import os; print(os.
                wall_now = self.get_wall_time()
                if any(request.is_due(wall_now) for request in self.wall_wakeups.values()):
                    continue
                # Sleep without timeout unless a real monotonic deadline supplies one.
                timeout = None
                if self.next_wakeup is not None:
                    if (not MonotonicTime.is_comparable(now, self.next_wakeup)
                            or MonotonicTime.compare(self.next_wakeup, now) <= 0):
                        # The wakeup is not comparable or has already passed; don't sleep.
                        continue
                    if self._fake_monotonic_time_generation is None:
                        # Sleep until timeout, unless a trigger or wall-clock wakeup arrives first.
                        timeout = (self.next_wakeup - now).total_seconds
                ends = [request.end for request in self.wall_wakeups.values() if request.end is not None]
                wall_waiter.arm(min(ends, default=None),
                                real=bool(self.wall_wakeups) and not self._has_fake_wall_time,
                                now=wall_now,
                                monitor_offset=any(request.utc_offset is not None
                                                   for request in self.wall_wakeups.values()))
                # Close the race between sampling wall time and arming the timer.
                if any(request.is_due(self.get_wall_time()) for request in self.wall_wakeups.values()):
                    continue
                try:
                    async with asyncio.timeout(timeout):
                        await self.triggered_procs_event.wait()
                except TimeoutError:
                    pass
                self.triggered_procs_event.clear()
        finally:
            wall_waiter.close()

    def pause(self) -> None:
        self.paused = True
        self._resumed_event.clear()
        self._status_changed()

    def resume(self) -> None:
        self.paused = False
        self._resumed_event.set()
        # Handle triggers and wakeups that arrived while paused.
        self.triggered_procs_event.set()
        self._status_changed()

    def _status_changed(self) -> None:
        for listener in tuple(self.status_listeners):
            listener()

    def get_node_path_list(self, name_parts: Iterable[str]) -> str:
        return self.get_node_path('.'.join(name_parts))

    def get_node_path(self, name: str) -> str:
        try:
            return self.program.symbols.node_aliases[name]
        except KeyError:
            raise ValueError(f'Cannot find node `{name}`') from None

    def get_node_path_singleton(self, name: str) -> str:
        try:
            return self.program.symbols.singleton_aliases[name]
        except KeyError:
            raise ValueError(f'Cannot find singleton `{name}`') from None

    def _get_native_state(self, node_id: str, initializer: str | None) -> NativeState | None:
        if initializer is not None:
            return self.native_states.get(f'{node_id}:{initializer}')
        states = [state for key, state in self.native_states.items() if key.partition(':')[0] == node_id]
        if len(states) > 1:
            raise ValueError(f'Node `{node_id}` has several initializers; specify an initializer name')
        return states[0] if states else None

    def get_native_state(self, name: str, initializer: str | None = None) -> NativeState | None:
        return self._get_native_state(self.get_node_path(name), initializer)

    def get_native_state_singleton(self, name: str, initializer: str | None = None) -> NativeState | None:
        return self._get_native_state(self.get_node_path_singleton(name), initializer)

    def get_variable(self, name: str) -> Variable:
        variable = self._get_source(name).variable
        if variable is None:
            raise ValueError(f'Variable alias `{name}` refers to a constant')
        if variable.is_resource:
            raise ValueError(f'Variable alias `{name}` refers to a resource')
        return variable

    def _get_source(self, name: str) -> InputSource:
        try:
            return self.program.symbols.variable_aliases[name]
        except KeyError:
            raise ValueError(f'Could not find variable `{name}`') from None

    def get_value(self, name: str) -> object:
        return self.get_source_value(self._get_source(name))

    def get_source_value(self, input_source: InputSource) -> object:
        if input_source.bank in (VariableBank.CONST_VALUES, VariableBank.FIXED_VALUES):
            return self._fixed_input(input_source)
        variable = input_source.variable
        assert isinstance(variable, Variable)
        if input_source.bank == VariableBank.TEMPORARIES:
            assert self.cache is not None
            return self.cache[variable]
        if input_source.bank == VariableBank.NEW_STATE:
            return self.state[variable]
        assert self.last_step_state is not None
        return self.last_step_state[variable]

    def assert_value_is(self, name: str, expected_value: object) -> None:
        actual_value = self.get_value(name)
        if actual_value != expected_value:
            raise Exception(f'Got unexpected value for {name}: Expected {expected_value}, got {actual_value}')

    def assert_is_idle(self) -> None:
        run_count, state_change_count, step_info = self.step()
        if run_count != 0:
            raise Exception(f'Is not idle, executed {run_count} procs')

    def set_fake_wall_time(self, value: datetime.datetime | None) -> None:
        """Freeze wall time at an aware datetime, or simulate unknown time with None."""
        if value is not None and (not isinstance(value, datetime.datetime) or value.utcoffset() is None):
            raise ValueError('Fake wall time must be timezone-aware')
        self._fake_wall_time = value
        self._has_fake_wall_time = True
        self.triggered_procs_event.set()

    def clear_fake_wall_time(self) -> None:
        """Restore the real wall-clock source."""
        self._has_fake_wall_time = False
        self._fake_wall_time = None
        self.triggered_procs_event.set()

    def get_wall_time(self) -> datetime.datetime | None:
        """Return local wall time, or None when unknown, preserving a fake time's timezone."""
        if self._has_fake_wall_time:
            return self._fake_wall_time
        return datetime.datetime.now(datetime.timezone.utc).astimezone()

    def get_monotonic_time(self) -> MonotonicTime:
        """Return this engine's current monotonic time."""
        offset = self.fake_monotonic_time_offset
        if self._fake_monotonic_time_generation is not None:
            return MonotonicTime(generation=self._fake_monotonic_time_generation,
                                 ticks=offset.ticks if offset is not None else 0)
        now = MonotonicTime.current_time()
        return now + offset if offset is not None else now

    def enable_fixed_monotonic_time(self) -> None:
        """Use the offset as fixed time in a new generation for this engine's lifetime."""
        if self._fake_monotonic_time_generation is not None:
            return
        generation = MonotonicTime.current_time().generation + 1
        if generation > MonotonicTime.MAX_GENERATION:
            raise ValueError('Monotonic time generation out of range')
        if self.fake_monotonic_time_offset is None:
            self.fake_monotonic_time_offset = Duration(ticks=0)
        self._fake_monotonic_time_generation = generation
        self.triggered_procs_event.set()

    def advance_fake_monotonic_time_generation(self) -> None:
        """Start a new fixed-clock generation without changing its ticks."""
        generation = self._fake_monotonic_time_generation
        if generation is None:
            raise ValueError('Fixed monotonic time is not enabled')
        if generation == MonotonicTime.MAX_GENERATION:
            raise ValueError('Monotonic time generation out of range')
        self._fake_monotonic_time_generation = generation + 1
        self.triggered_procs_event.set()

    def advance_fake_monotonic_time(self, offset: Duration) -> None:
        if self.fake_monotonic_time_offset is None:
            print('Warning: Setting fake monotonic time offset', flush=True)
            self.fake_monotonic_time_offset = wirely.Duration(ticks=0)
        self.fake_monotonic_time_offset += offset
        self.triggered_procs_event.set()
