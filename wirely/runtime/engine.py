import asyncio
import contextlib
import datetime
from types import MappingProxyType

import wirely
from wirely.program import Program, validate_program
from wirely.runtime.contexts import NativeNodeContext, VariableTableInstance, ProcedureContext, StepContext
from wirely.runtime.procedures import execute_procedure


class Engine:
    def __init__(self, program):
        from .linking import link_native_classes, link_native_functions

        if not isinstance(program, Program):
            raise Exception('program parameter is not a Program')

        validate_program(program)
        self.program = program

        self.cache = None  # Temporary values from the last completed step.

        self.debug = False

        # Is a datetime during async task shutdown unless the shutdown was triggered before all async tasks were started
        self.shutdown_time = None

        # The time of the last complete async task shutdown. Should be used only for debugging / tests.
        self.last_shutdown_time = None

        self.triggered_procs = set()
        self.triggered_procs_event = asyncio.Event()

        self.step_count = 0  # Number of completed steps.
        # While paused, run() starts no steps; step() can still be called directly.
        self.paused = False
        self._resumed_event = asyncio.Event()
        self._resumed_event.set()

        self.wakeups = {}
        self.next_wakeup = None

        # The current state, will be .old during the next step.
        self.state = VariableTableInstance(self.program.state_variables)
        # The state before the last step. Was .old during the last step.
        self.last_step_state = None
        self.native_states = {}

        self.fake_monotonic_time_offset = None

        self.native_classes = link_native_classes(program)
        self.native_functions = link_native_functions(program)
        for node_id in program.native_construction_order:
            instance = program.instances[node_id]
            native_state = None
            if instance.has_native_state:
                native_class = self.native_classes[instance.type_name]
                context = NativeNodeContext(
                    name=instance.name, values=instance.initializer_values,
                    inputs=MappingProxyType(self._resource_inputs(instance.initializer_bindings)),
                    _engine=self, _node_id=node_id,
                )
                native_state = native_class(context=context)
            self.native_states[node_id] = native_state
        for variable, value in program.initial_state.items():
            self.state.add(variable, value)

    def _resource_inputs(self, bindings):
        inputs = {}
        for name, paths, is_list in bindings:
            values = tuple(self.native_states[path] for path in paths)
            inputs[name] = values if is_list else values[0]
        return inputs

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        def start_task_cleanup():
            now_monotonic = wirely.MonotonicTime.current_time()
            if self.fake_monotonic_time_offset is not None:
                now_monotonic += self.fake_monotonic_time_offset

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            now = now_utc.astimezone()

            print(f'Task cleanup starting at {now}')

            # TODO: Set monotonic_shutdown_time?
            self.shutdown_time = now

        def finish_task_cleanup():
            print('Task cleanup done')

            self.last_shutdown_time = self.shutdown_time
            self.shutdown_time = None

        async_exit_stack.callback(finish_task_cleanup)

        # TODO: Currently this (e.g. connecting to knxd) is blocking and done sequentially. Should ths be non-blocking instead? (I.e. should the connection be created on the background task and should start_tasks_async() be removed?)
        for state in self.native_states.values():
            if state is not None:
                state.start_tasks(task_group, async_exit_stack)
                await state.start_tasks_async(task_group, async_exit_stack)

        async_exit_stack.callback(start_task_cleanup)

    # TODO: Threading?
    def trigger(self, node_id, proc_name):
        key = node_id, proc_name
        if not isinstance(proc_name, str) or not any(
            proc.key == key for schedule in self.program.procs.values() for proc in schedule
        ):
            raise ValueError(f'Unknown procedure name {proc_name!r} on {node_id}')
        self.triggered_procs.add(key)
        self.triggered_procs_event.set()

    # Update self.next_wakeup to include wakeup_time
    def update_next_wakeup(self, now, wakeup_time):
        if wakeup_time is None:
            return
        if not wirely.MonotonicTime.is_comparable(now, wakeup_time):
            # A time from another generation cannot be waited for; it is due now.
            print(f'Warning: Got non-current wakeup time {wakeup_time}', flush=True)
            wakeup_time = now

        if (self.next_wakeup is None or not wirely.MonotonicTime.is_comparable(self.next_wakeup, wakeup_time)
                or wirely.MonotonicTime.compare(self.next_wakeup, wakeup_time) > 0):
            self.next_wakeup = wakeup_time

    def step(self):
        if self.debug:
            print(f'Starting step of {self.program.name}')
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
            step_info.start_stage(self, stage)
            now = step_info.stage_start_time_monotonic[stage]
            for proc in stage_procs:
                key = proc.key
                inputs = self._resource_inputs(proc.resources)
                skip_step = cache is not None and key not in self.triggered_procs
                for name, input_sources, is_list in proc.dependencies:
                    values = []
                    for input_source in input_sources:
                        variable = input_source.variable
                        if input_source.new_state is None:
                            value = variables[variable]
                            if skip_step and not variable.var_type.equals(value, cache[variable]):
                                skip_step = False
                        elif input_source.new_state:
                            value = state_new[variable]
                            # The old state is the previous step's new state.
                            if skip_step and not variable.var_type.equals(value, state_old[variable]):
                                skip_step = False
                        else:
                            value = state_old[variable]
                            if skip_step and not variable.var_type.equals(value, self.last_step_state[variable]):
                                skip_step = False
                        values.append(wirely.value_types._to_native(variable.var_type, value))
                    inputs[name] = tuple(values) if is_list else values[0]
                if key in self.wakeups:
                    deadline = self.wakeups[key]
                    if not wirely.MonotonicTime.is_comparable(now, deadline) or wirely.MonotonicTime.compare(now, deadline) >= 0:
                        skip_step = False
                if skip_step:
                    for output in proc.outputs.values():
                        variable = output.variable
                        if variable in self.program.state_variables.variable_set:
                            state_new.add(variable, state_old[variable])
                        else:
                            variables.add(variable, cache[variable])
                    if key in self.wakeups:
                        self.update_next_wakeup(now, self.wakeups[key])
                    continue
                if self.debug:
                    print(f'Running procedure {proc.node_id} {proc.reference}')
                run_count += 1
                self.triggered_procs.discard(key)
                outputs = {}
                context = ProcedureContext(
                    inputs=inputs, outputs=outputs, static=proc, step_info=step_info,
                )
                if proc.body is None:
                    native_state = self.native_states[proc.node_id]
                    target = native_state if proc.node_context.has_native_state else self.native_classes[proc.reference[0]]
                    getattr(target, proc.reference[1])(context=context)
                else:
                    for name, value in execute_procedure(self.program, proc, inputs, self.native_functions).items():
                        context.set_output(name, value)
                missing = set(proc.outputs) - context._written_outputs
                if missing:
                    raise ValueError(f'Procedure {proc.reference[1]!r} on {context.name} did not write outputs: {sorted(missing)!r}')
                if context.wakeup_time is None:
                    self.wakeups.pop(key, None)
                else:
                    self.wakeups[key] = context.wakeup_time
                self.update_next_wakeup(now, context.wakeup_time)
                for name, output in proc.outputs.items():
                    variable = output.variable
                    value = outputs[name]
                    if variable in self.program.state_variables.variable_set:
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
            print(f'Finished step of {self.program.name}')
        return run_count, state_change_count, step_info

    async def run(self):
        while True:
            await asyncio.sleep(0)
            await self._resumed_event.wait()
            run_count, state_change_count, step_info = self.step()
            if state_change_count == 0:
                now = wirely.MonotonicTime.current_time()
                if self.fake_monotonic_time_offset is not None:
                    now += self.fake_monotonic_time_offset

                # TODO: If there is a "while True" here, the code will crash (instead of looping) because of EAGAIN when writing to the terminal, seems like NONBLOCK is set
                # while True:
                # import os; import fcntl; fcntl.fcntl(1, fcntl.F_GETFL)
                # print(f'Waiting, wakeup is {self.next_wakeup}', flush=True)
                # import os; print(os.
                if self.next_wakeup is None:
                    # Sleep without timeout
                    await self.triggered_procs_event.wait()
                elif (not wirely.MonotonicTime.is_comparable(now, self.next_wakeup)
                        or wirely.MonotonicTime.compare(self.next_wakeup, now) <= 0):
                    # The wakeup is not comparable or has already passed; don't sleep.
                    pass
                else:
                    diff = self.next_wakeup - now
                    # Sleep until timeout
                    try:
                        async with asyncio.timeout(diff.total_seconds):
                            await self.triggered_procs_event.wait()
                    except TimeoutError:
                        pass
                self.triggered_procs_event.clear()

    def pause(self):
        self.paused = True
        self._resumed_event.clear()

    def resume(self):
        self.paused = False
        self._resumed_event.set()
        # Handle triggers and wakeups that arrived while paused.
        self.triggered_procs_event.set()

    def get_node_path_list(self, name_parts):
        return self.get_node_path('.'.join(name_parts))

    def get_node_path(self, name):
        try:
            return self.program.node_aliases[name]
        except KeyError:
            raise ValueError(f'Cannot find node {name!r}') from None

    def get_node_path_singleton(self, name):
        try:
            return self.program.singleton_aliases[name]
        except KeyError:
            raise ValueError(f'Cannot find singleton {name!r}') from None

    def get_native_state(self, name):
        return self.native_states[self.get_node_path(name)]

    def get_native_state_singleton(self, name):
        return self.native_states[self.get_node_path_singleton(name)]

    def get_variable(self, name):
        return self._get_source(name).variable

    def _get_source(self, name):
        try:
            return self.program.variables[name]
        except KeyError:
            raise ValueError(f'Could not find variable {name!r}') from None

    def get_value(self, name):
        return self.get_source_value(self._get_source(name))

    def get_source_value(self, input_source):
        if input_source.new_state is None:
            return self.cache[input_source.variable]
        if input_source.new_state:
            return self.state[input_source.variable]
        return self.last_step_state[input_source.variable]

    def assert_value_is(self, name, expected_value):
        actual_value = self.get_value(name)
        if actual_value != expected_value:
            raise Exception(f'Got unexpected value for {name}: Expected {expected_value}, got {actual_value}')

    def assert_is_idle(self):
        run_count, state_change_count, step_info = self.step()
        if run_count != 0:
            raise Exception(f'Is not idle, executed {run_count} procs')

    def advance_fake_monotonic_time(self, offset):
        if self.fake_monotonic_time_offset is None:
            print('Warning: Setting fake monotonic time offset', flush=True)
            self.fake_monotonic_time_offset = wirely.Duration(duration_ns=0)
        self.fake_monotonic_time_offset += offset
