import asyncio
import contextlib
import datetime
from types import MappingProxyType

import wirely as blocks
from wirely.util import check_parameters

class Engine:
    def __init__(self, program):
        if not isinstance(program, blocks.Program):
            raise Exception('program parameter is not a Program')

        self.program = program

        self.cache = None

        self.debug = False

        # Is a datetime during async task shutdown unless the shutdown was triggered before all async tasks were started
        self.shutdown_time = None

        # The time of the last complete async task shutdown. Should be used only for debugging / tests.
        self.last_shutdown_time = None

        self.triggered_procs = set()
        self.triggered_procs_event = asyncio.Event()

        self.wakeups = {}
        self.next_wakeup = None

        self.state = blocks.VariableBlockInstance(self.program.state_variables)
        self.last_step_state = None
        self.native_states = {}

        self.fake_monotonic_time_offset = None

        for block_path in self.program.native_construction_order:
            block = block_path[-1]
            instance = self.program.instances[block_path]
            inputs = MappingProxyType(self._resource_inputs(self.program.initializer_bindings[block_path]))
            context = blocks.NativeNodeContext(
                name=instance.static_context.name,
                parameters=instance.static_context.parameters,
                inputs=inputs,
                configuration=instance.static_context.configuration,
                _engine=self,
                _block_path=block_path,
            )
            self.native_states[block_path] = block.create_native_state(context=context)
            if not isinstance(block, blocks.Block):
                block = block.singleton_instance
            for var in block.block_statevars:
                self.state.add(self.program.state_map[block_path, var], var.initial_value)

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
    def trigger(self, path, proc):
        # TODO: More checks with path
        if not isinstance(proc, blocks.Block.Procedure):
            raise Exception('proc parameter is not a Block.Procedure')

        if (path, proc) not in self.program.all_procs_set:
            # print(self.program.all_procs_set)
            raise Exception(f'Got procedure {path} {proc} which is not in {self.program.graph}')

        self.triggered_procs.add((path, proc))
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
            print(f'Starting step of {self.program.graph}')
            # print(f'Wakeups: {self.wakeups}')

        self.next_wakeup = None

        cache = self.cache
        state_old = self.state
        state_new = blocks.VariableBlockInstance(self.program.state_variables)
        vars = blocks.VariableBlockInstance(self.program.temporaries)
        run_count = 0
        change_count = 0
        state_change_count = 0
        step_info = blocks.StepContext()
        for stage, stage_procs in self.program.procs.items():
            # print(f'Stage {stage}', flush=True)
            step_info.start_stage(self, stage)

            now = step_info.stage_start_time_monotonic[stage]

            for path, proc in stage_procs:
                if path[-1] != proc.block:
                    if isinstance(path[-1], blocks.Block):  # TODO: Handle singletons here?
                        raise Exception('path[-1] != proc.block')
                native_state = self.native_states[path]

                # print(proc)
                instance = self.program.instances[path]
                static = self.program.proc_static_contexts[(instance, proc)]
                inputs = self._resource_inputs(static._resource_bindings)
                skip_step = cache is not None and (path, proc) not in self.triggered_procs
                for name, bindings, is_list in static._input_bindings:
                    values = []
                    for binding in bindings:
                        variable = binding.variable
                        if binding.state_changed is not None:
                            value = state_old[variable]
                            if skip_step and cache[binding.state_changed]:
                                skip_step = False
                        else:
                            value = vars[variable]
                            if skip_step and not variable.var_type.equals(value, cache[variable]):
                                skip_step = False
                        values.append(blocks.value_types._to_native(variable.var_type, value))
                    inputs[name] = tuple(values) if is_list else values[0]
                # TODO: Avoid depending on the value of all state variables?
                for dep in proc.block.block_statevars:
                    if skip_step and cache[self.program.state_map_changed[path, dep]]:
                        skip_step = False
                res = {}

                if (path, proc) in self.wakeups:
                    if not blocks.MonotonicTime.is_comparable(now, self.wakeups[(path, proc)]) or blocks.MonotonicTime.compare(now, self.wakeups[(path, proc)]) >= 0:
                        # Timeout, run step
                        skip_step = False

                if skip_step:
                    for res_var in proc.results:
                        if isinstance(res_var, blocks.Block.StateVariable):
                            old_value = state_old[self.program.state_map[path, res_var]]
                            state_new.add(self.program.state_map[path, res_var], old_value)
                            vars.add(self.program.state_map_new[path, res_var], old_value)
                            vars.add(self.program.state_map_changed[path, res_var], False)
                        else:
                            # TODO: Clean up temporary / output stuff
                            vars.add(self.program.variable_map[path, res_var.var_identifier], cache[self.program.variable_map[path, res_var.var_identifier]])

                    if (path, proc) in self.wakeups:
                        self.update_next_wakeup(now, self.wakeups[(path, proc)])
                    continue

                if self.debug:
                    print(f'Running procedure {path} {proc}')

                run_count += 1

                if (path, proc) in self.triggered_procs:
                    self.triggered_procs.remove((path, proc))

                state_old_block = {}
                # TODO: Remove state_new_block, use res instead?
                state_new_block = {}
                for var in proc.block.block_statevars:
                    state_old_block[var] = state_old[self.program.state_map[path, var]]
                    # TODO: Should this be initialized here?
                    # state_new_block[var] = state_old[var]

                pcontext = blocks.ProcedureContext(inputs=inputs, results=res, state_new=state_new_block, static=static, step_info=step_info)
                proc.impl(context=pcontext, native_state=native_state)
                missing = set(static._targets) - pcontext._written_results
                if missing:
                    raise ValueError(f'Procedure {proc.declaration_procedure.python_name!r} on {pcontext.name} did not write results: {sorted(missing)!r}')

                if pcontext.wakeup_time is None:
                    if (path, proc) in self.wakeups:
                        del self.wakeups[(path, proc)]
                else:
                    self.wakeups[(path, proc)] = pcontext.wakeup_time
                self.update_next_wakeup(now=now, wakeup_time=pcontext.wakeup_time)

                unknown = set(res)
                unknown_newstate = set(state_new_block)
                # TODO: Clean up, more checking for state variables
                for res_var in proc.results:
                    if isinstance(res_var, blocks.Block.StateVariable):
                        if res_var not in unknown_newstate:
                            raise Exception(f'State output {res_var.name} of native procedure {proc} is missing')
                        unknown_newstate.remove(res_var)
                        val = state_new_block[res_var]
                        state_new.add(self.program.state_map[path, res_var], val)

                        new_value = res_var.impl_var_type.verify_value(val)
                        vars.add(self.program.state_map_new[path, res_var], new_value)
                        if not res_var.impl_var_type.equals(state_old_block[res_var], new_value):
                            state_change_count += 1
                            vars.add(self.program.state_map_changed[path, res_var], True)
                        else:
                            vars.add(self.program.state_map_changed[path, res_var], False)

                        continue

                    if res_var not in unknown:
                        raise Exception(f'Output {res_var.name_desc} of native procedure {proc} is missing')
                    unknown.remove(res_var)
                    val = res[res_var]
                    # print(f'Writing {res_var}')
                    # TODO: Clean up temporary / output stuff

                    # TODO: Move this code somewhere else? / Use already resolved type?
                    var_type = res_var.impl_var_type
                    for block in reversed(path):
                        if isinstance(block, blocks.Block):  # TODO: Handle singletons here?
                            var_type = var_type.resolve_self(block.par_values)

                    # print('RESULT', proc.block.block_type, proc, res_var)
                    new_value = var_type.verify_value(val)
                    if cache is None or not var_type.equals(new_value, cache[self.program.variable_map[path, res_var.var_identifier]]):
                        change_count += 1
                    # print(self.program.variable_map, [path, res_var.var_identifier])
                    vars.add(self.program.variable_map[path, res_var.var_identifier], new_value)
                if len(unknown):
                    raise Exception(f'{proc} returned unknown values: {sorted(unknown)}')
                if len(unknown_newstate):
                    raise Exception(f'{proc} returned unknown state values: {sorted(unknown_newstate)}')

        self.cache = vars
        self.last_step_state = state_old
        self.state = state_new

        if self.debug:
            print(f'Finished step of {self.program.graph}')

        # print(state_old, state_new)
        # print(run_count, change_count, state_change_count)
        # return run_count
        # return change_count
        # TODO: Clean up return values
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
        path = []
        graph = self.program.graph
        for part in name_parts:
            # print('XXX', graph, part, list(graph.blocks))
            if part not in graph.blocks:
                # TODO: Clean up
                # raise Exception('Cannot find block ' + '.'.join([block.name for block in path] + [part]))
                raise Exception('Cannot find block ' + '.'.join([(block.name if isinstance(block, blocks.Block) else block.name.id_str) for block in path] + [part]))
            block = graph.blocks[part]
            if block.block_type.is_singleton:
                path = [block.block_type]
            else:
                path.append(block)
            graph = block.block_type

        return tuple(path)

    def get_block_path(self, name):
        return self.get_block_path_list(name.split('.'))

    def get_block_path_singleton(self, name):
        scope = self.program.graph
        parts = name.split('.')
        for part in parts[:-1]:
            scope = scope.libraries[part]
        ty = scope.block_type_implementations[parts[-1]]
        return (ty,)

    # TODO: Name? get_native_state? get_native_block? get_block_instance?
    def get_native_state(self, name):
        path = self.get_block_path(name)
        return self.native_states[path]

    def get_native_state_singleton(self, name):
        path = self.get_block_path_singleton(name)
        return self.native_states[path]

    def get_variable(self, name):
        name_parts = name.split('.')

        # TODO: Avoid special case
        if len(name_parts) == 1:
            path = self.get_block_path_list(name_parts)
            block = path[-1]
            if not isinstance(block, blocks.Block):
                block = block.singleton_instance
            return path, block.block_outputs_byname[''].var_identifier

        path = self.get_block_path_list(name_parts[:-1])
        block = path[-1]
        if not isinstance(block, blocks.Block):
            block = block.singleton_instance
        lname = name_parts[-1]

        # TODO: Clean this up
        # TODO: How to handle input/output clashes?
        for value in block.block_inputs:
            if value.name == lname:
                return path, value.var_identifier
        for value in block.block_outputs:
            if value.name == lname:
                return path, value.var_identifier
        for value in block.block_statevars:
            if value.name == lname:
                return path, value.var_identifier
        for value in block.block_temporaries:
            if value.name == lname:
                return path, value.var_identifier

        if lname in block.block_type.blocks:
            path = self.get_block_path_list(name_parts)
            child = path[-1]
            if not isinstance(child, blocks.Block):
                child = child.singleton_instance
            return path, child.block_outputs_byname[''].var_identifier

        # print(block.block_inputs, block.block_temporaries, block.block_statevars)
        raise Exception(f'Could not find {name}')


    def get_value(self, name):
        var = self.program.variable_map[self.get_variable(name)]
        return self.get_bound_value(var)

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
