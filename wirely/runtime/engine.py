import asyncio
import contextlib
import datetime
from types import MappingProxyType

import wirely as blocks


class Engine:
    def __init__(self, program):
        from wirely.native import get_native_block_type_by_name

        if not isinstance(program, blocks.Program):
            raise Exception('program parameter is not a Program')

        self.program = program

        self.cache = None  # Temporary values from the last completed step.

        self.debug = False

        # Is a datetime during async task shutdown unless the shutdown was triggered before all async tasks were started
        self.shutdown_time = None

        # The time of the last complete async task shutdown. Should be used only for debugging / tests.
        self.last_shutdown_time = None

        self.triggered_procs = set()
        self.triggered_procs_event = asyncio.Event()

        self.wakeups = {}
        self.next_wakeup = None

        # The current state, will be .old during the next step.
        self.state = blocks.VariableBlockInstance(self.program.state_variables)
        # The state before the last step. Was .old during the last step.
        self.last_step_state = None
        self.native_states = {}

        self.fake_monotonic_time_offset = None

        type_names = {proc.reference[0] for schedule in program.procs.values()
                      for proc in schedule if proc.body is None}
        self.native_classes = {name: get_native_block_type_by_name(name) for name in type_names}
        for node_id in program.native_construction_order:
            instance = program.instances[node_id]
            native_state = None
            if instance.has_native_state:
                native_class = self.native_classes.get(instance.type_name)
                if native_class is None:
                    native_class = get_native_block_type_by_name(instance.type_name)
                    self.native_classes[instance.type_name] = native_class
                context = blocks.NativeNodeContext(
                    name=instance.name, values=instance.initializer_values,
                    inputs=MappingProxyType(self._resource_inputs(instance.initializer_bindings)),
                    configuration=instance.configuration, _engine=self, _node_id=node_id,
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
            now_monotonic = blocks.MonotonicTime.current_time()
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
        if wakeup_time is not None and not wakeup_time.is_invalid and not blocks.MonotonicTime.is_comparable(now, wakeup_time):
            print('Warning: Got non-current wakeup time {wakeup_time}', flush=True)
            wakeup_time = blocks.MonotonicTime.create_invalid()
        if wakeup_time is None:
            return

        if self.next_wakeup is None or wakeup_time.is_invalid or blocks.MonotonicTime.compare(self.next_wakeup, wakeup_time) > 0:
            self.next_wakeup = wakeup_time

    def step(self):
        if self.debug:
            print(f'Starting step of {self.program.name}')
        self.next_wakeup = None
        cache = self.cache
        state_old = self.state  # Snapshot read throughout this step.
        # State produced by this step, published when it completes.
        state_new = blocks.VariableBlockInstance(self.program.state_variables)
        variables = blocks.VariableBlockInstance(self.program.temporaries)
        run_count = 0
        state_change_count = 0
        step_info = blocks.StepContext()
        for stage, stage_procs in self.program.procs.items():
            step_info.start_stage(self, stage)
            now = step_info.stage_start_time_monotonic[stage]
            for proc in stage_procs:
                key = proc.key
                inputs = self._resource_inputs(proc.resources)
                skip_step = cache is not None and key not in self.triggered_procs
                for name, bindings, is_list in proc.dependencies:
                    values = []
                    for binding in bindings:
                        variable = binding.variable
                        if variable in self.program.state_variables.variable_set:
                            value = state_old[variable]
                            if skip_step and not variable.var_type.equals(value, self.last_step_state[variable]):
                                skip_step = False
                        else:
                            value = variables[variable]
                            if skip_step and not variable.var_type.equals(value, cache[variable]):
                                skip_step = False
                        values.append(blocks.value_types._to_native(variable.var_type, value))
                    inputs[name] = tuple(values) if is_list else values[0]
                if key in self.wakeups:
                    deadline = self.wakeups[key]
                    if not blocks.MonotonicTime.is_comparable(now, deadline) or blocks.MonotonicTime.compare(now, deadline) >= 0:
                        skip_step = False
                if skip_step:
                    for result in proc.results.values():
                        variable = result.variable
                        if result.state_new is not None:
                            value = state_old[variable]
                            state_new.add(variable, value)
                            variables.add(result.state_new, value)
                        else:
                            variables.add(variable, cache[variable])
                    if key in self.wakeups:
                        self.update_next_wakeup(now, self.wakeups[key])
                    continue
                if self.debug:
                    print(f'Running procedure {proc.node_id} {proc.reference}')
                run_count += 1
                self.triggered_procs.discard(key)
                results = {}
                context = blocks.ProcedureContext(
                    inputs=inputs, results=results, static=proc, step_info=step_info,
                )
                if proc.body is None:
                    native_state = self.native_states[proc.node_id]
                    target = native_state if proc.node_context.has_native_state else self.native_classes[proc.reference[0]]
                    getattr(target, proc.reference[1])(context=context)
                else:
                    for name, value in proc.body.execute(inputs).items():
                        context.set_result(name, value)
                missing = set(proc.results) - context._written_results
                if missing:
                    raise ValueError(f'Procedure {proc.reference[1]!r} on {context.name} did not write results: {sorted(missing)!r}')
                if context.wakeup_time is None:
                    self.wakeups.pop(key, None)
                else:
                    self.wakeups[key] = context.wakeup_time
                self.update_next_wakeup(now, context.wakeup_time)
                for name, result in proc.results.items():
                    variable = result.variable
                    value = results[name]
                    if result.state_new is not None:
                        state_new.add(variable, value)
                        variables.add(result.state_new, value)
                        changed = not variable.var_type.equals(state_old[variable], value)
                        state_change_count += changed
                    else:
                        variables.add(variable, value)
        self.cache = variables
        self.last_step_state = state_old
        self.state = state_new
        if self.debug:
            print(f'Finished step of {self.program.name}')
        return run_count, state_change_count, step_info

    async def run(self):
        while True:
            await asyncio.sleep(0)
            run_count, state_change_count, step_info = self.step()
            if state_change_count == 0:
                now = blocks.MonotonicTime.current_time()
                # TODO: This probably is not really used or tested currently
                if self.fake_monotonic_time_offset is not None:
                    now_monotonic += self.fake_monotonic_time_offset

                # TODO: If there is a "while True" here, the code will crash (instead of looping) because of EAGAIN when writing to the terminal, seems like NONBLOCK is set
                # while True:
                # import os; import fcntl; fcntl.fcntl(1, fcntl.F_GETFL)
                # print(f'Waiting, wakeup is {self.next_wakeup}', flush=True)
                # import os; print(os.
                if self.next_wakeup is None:
                    # Sleep without timeout
                    await self.triggered_procs_event.wait()
                elif self.next_wakeup.is_invalid or not blocks.MonotonicTime.is_comparable(now, self.next_wakeup):
                    # Don't sleep
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

    def get_block_path_list(self, name_parts):
        return self.get_block_path('.'.join(name_parts))

    def get_block_path(self, name):
        try:
            return self.program.node_aliases[name]
        except KeyError:
            raise ValueError(f'Cannot find block {name!r}') from None

    def get_block_path_singleton(self, name):
        try:
            return self.program.singleton_aliases[name]
        except KeyError:
            raise ValueError(f'Cannot find singleton {name!r}') from None

    def get_native_state(self, name):
        return self.native_states[self.get_block_path(name)]

    def get_native_state_singleton(self, name):
        return self.native_states[self.get_block_path_singleton(name)]

    def get_variable(self, name):
        try:
            return self.program.variables[name]
        except KeyError:
            raise ValueError(f'Could not find variable {name!r}') from None

    def get_value(self, name):
        return self.get_bound_value(self.get_variable(name))

    def get_bound_value(self, variable):
        if variable in self.program.state_variables.variable_set:
            return self.last_step_state[variable]
        return self.cache[variable]

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
            self.fake_monotonic_time_offset = blocks.Duration(duration_ns=0)
        self.fake_monotonic_time_offset += offset
