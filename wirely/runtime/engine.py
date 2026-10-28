import asyncio
import contextlib
import datetime
import functools
from collections.abc import Callable, Iterable
from typing import Any, cast

import wirely
from wirely.program.program import InputSource, Procedure, RunStage, Variable, VariableBank
from wirely.runtime.contexts import StepContext, VariableTableInstance
from wirely.runtime.loading import LoadedProgram, fixed_input, procedure_owner_label
from wirely.runtime.native_api import NativeState, ProcedureServices
from wirely.runtime.signatures import Procedure as SignatureProcedure
from wirely.program.types import InternalValue
from wirely.types.time import Duration, MonotonicTime
from wirely.runtime.procedures import execute_procedure
from wirely.runtime.wall_clock import WallClockWaiter, WallWakeup


class Engine:
    def __init__(self, loaded: LoadedProgram) -> None:
        if not isinstance(loaded, LoadedProgram):
            raise TypeError('loaded parameter is not a LoadedProgram')
        loaded._host.attach(self)
        program = loaded.program
        self.program = program
        self.native_types = loaded.native_types
        self.native_functions = loaded.native_functions
        self.resource_types = loaded.resource_types
        self._procedure_indices = loaded.procedure_indices
        self._runtime_procedures = frozenset(proc for stage, schedule in program.procs.items()
                                             if stage != RunStage.LOAD for proc in schedule)
        self.fixed_values = loaded.fixed_values
        self.resource_values = loaded.resource_values
        self._resource_triggers = loaded.resource_triggers
        self._initializer_outputs = loaded.initializer_outputs
        self.native_states = loaded.native_states
        self._native_procedures = loaded.native_procedures

        self.cache: VariableTableInstance | None = None  # Temporary values from the last completed step.

        self.debug = False

        # Set during cleanup only if all async tasks were started successfully.
        self.is_shutting_down = False
        # Wall time at the start of that cleanup, if known.
        self.shutdown_time: datetime.datetime | None = None

        # The time of the last complete async task shutdown. Should be used only for debugging / tests.
        self.last_shutdown_time: datetime.datetime | None = None

        self.triggered_procs: set[Procedure] = set()  # Triggered procedure identities.
        self.triggered_procs_event = asyncio.Event()

        self.step_count = 0  # Number of completed steps.
        # While paused, run() starts no steps; step() can still be called directly.
        self.paused = False
        self._resumed_event = asyncio.Event()
        self._resumed_event.set()
        # Called after each step and when pausing or resuming, e.g. to notify control clients.
        self.status_listeners: set[Callable[[], None]] = set()

        self.wakeups: dict[Procedure, MonotonicTime] = {}
        self.next_wakeup: MonotonicTime | None = None
        self.wall_wakeups: dict[Procedure, WallWakeup] = {}

        # The current state, will be .old during the next step.
        self.state = VariableTableInstance(self.program.state_variables)
        # The state before the last step. Was .old during the last step.
        self.last_step_state: VariableTableInstance | None = None

        self.fake_monotonic_time_offset: Duration | None = None
        self._fake_monotonic_time_generation: int | None = None
        self._fake_wall_time: datetime.datetime | None = None
        self._has_fake_wall_time = False
        self.test_twon_fake_server_info: Any = None  # Set by the test runner for 2N tests.

        # Set when activation starts; an engine is activated at most once.
        self._activated = False

        for variable, initial in program.initial_state.items():
            self.state.add(variable, initial)

    async def activate(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        """Start the tasks of the native objects; their cleanup is registered on async_exit_stack.

        An engine is activated at most once; load the program again to restart it.
        """
        if self._activated:
            raise RuntimeError('The engine was already activated')
        self._activated = True

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

        # TODO: Currently this (e.g. connecting to a 2N device) is blocking and done sequentially. Should ths be non-blocking instead? (I.e. should the connection be created on the background task and should start_tasks_async() be removed?)
        for state in self.native_states.values():
            if state is not None:
                state.start_tasks(task_group, async_exit_stack)
                await state.start_tasks_async(task_group, async_exit_stack)

        async_exit_stack.callback(start_task_cleanup)

    def _procedure_owner_label(self, procedure: Procedure) -> str:
        return procedure_owner_label(procedure, self._procedure_indices)

    def _resolve_procedure(self, target: Procedure | str, candidates: Iterable[Procedure], kind: str) -> Procedure:
        if isinstance(target, Procedure):
            if target in candidates:
                return target
            raise ValueError(f'Unknown {kind} instance')
        if isinstance(target, str):
            matches = [proc for proc in candidates if proc.name == target]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ValueError(f'Ambiguous {kind} name `{target}`')
        raise ValueError(f'Unknown {kind} name `{target}`')

    # TODO: Threading?
    def trigger(self, target: Procedure | str) -> None:
        """Request execution by procedure handle or an unambiguous instance name."""
        self._trigger_key(self._resolve_procedure(target, self._runtime_procedures, 'procedure'))

    def _trigger_output(self, initializer: Procedure, output: str) -> None:
        slot = self._initializer_outputs[initializer].get(output)
        if slot is None:
            raise ValueError(f'Output `{output}` is not a resource')
        if slot in self.resource_values:
            for key in self._resource_triggers.get(slot, ()):
                self._trigger_key(key)

    def _trigger_key(self, key: Procedure) -> None:
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
            print(f'Starting step of {self.program.symbols.name}', flush=True)
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
                key = proc
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
                    print(f'Running procedure {self.program.procedure_label(proc)}', flush=True)
                run_count += 1
                self.triggered_procs.discard(key)
                if proc.native is not None:
                    implementation, adapter, binding = self._native_procedures[proc]
                    services = ProcedureServices(name=self._procedure_owner_label(proc), native_types=self.native_types,
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
                    output_values = execute_procedure(proc, values, self.native_functions, native_types=self.native_types,
                                                      procedure_index=self._procedure_indices[proc])
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
            print(f'Finished step of {self.program.symbols.name}', flush=True)
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

    def get_native_state(self, target: Procedure | str) -> NativeState:
        """Return native state by initializer handle or an unambiguous instance name."""
        return self.native_states[self._resolve_procedure(target, self.native_states, 'initializer')]

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
            return fixed_input(input_source, self.fixed_values, self.resource_values)
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
