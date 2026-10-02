import traceback

import wirely as blocks
from wirely.util import check_parameters

class Program:
    def __init__(self):
        pass

    @staticmethod
    def compile(global_block_type):
        try:
            return Program.compile_nocatch(global_block_type)
        except blocks.ProgramError as exc:
            # TODO: Merge with code in compiler.py
            print(f'Error in {exc.source_element.source_position.filename}:{exc.source_element.source_position.start_line}: {exc}')
            if exc.source_element is not None:
                print(exc.get_context())
            if isinstance(exc, blocks.InternalCompilerError):
                traceback.print_exception(exc.inner_exception)
            for hint in exc.hints:
                print(f'Hint: {hint.source_element.source_position.filename}:{hint.source_element.source_position.start_line}: {hint}')
                print(hint.get_context())
            return None

    @staticmethod
    def compile_nocatch(global_block_type):
        # TODO: More checks, e.g. that global_block_type has not parameters or inputs / outputs
        graph = global_block_type

        variable_map = {}
        state_map_new = {}
        state_map_changed = {}
        all_variables = []
        state_map = {}
        state_variables = []
        # TODO: Try to avoid needing this by making sure everyone uses instance values instead of paths?
        instances = {}

        def add_var(path, var):
            # TODO: Clean up handling of singletons (vars probably should contain a reference to a BlockType, not to a Block)
            if (path, var.var_identifier) in variable_map:
                return variable_map[path, var.var_identifier]

            # print(f'add_var({path}, {var.block})')
            if path[-1] != var.block:
                if isinstance(path[-1], blocks.Block):  # TODO: Handle singletons here?
                    raise Exception('path[-1] != var.block')
            # TODO: Move this code somewhere else?
            var_type = var.impl_var_type
            for block in reversed(path):
                if isinstance(block, blocks.Block):  # TODO: Handle singletons here?
                    var_type = var_type.resolve_self(block.par_values)
            # TODO: Clean up name?
            # name = '.'.join([block.name for block in path])
            name = '.'.join([(block.name if isinstance(block, blocks.Block) else block.name.id_str) for block in path])
            v = blocks.Variable(name + '.' + var.name, var_type)
            # print(v.name)
            variable_map[path, var.var_identifier] = v
            all_variables.append(v)
            return v

        # TODO: Use get_all_block_paths()?
        singletons = {}
        for block in graph.blocks.values():
            block.instantiate(singletons=singletons, add_var=add_var, variable_map=variable_map, state_map_new=state_map_new, all_variables=all_variables, state_map_changed=state_map_changed, state_map=state_map, state_variables=state_variables, instances=instances, path=(block,), parent_instance=None)

        procs = {}
        done = set()
        doing = []
        def do(path, proc):
            # TODO: Clean this up (this makes sure that the correct Procedure instance is used for singletons)
            if proc.block.block_type.is_singleton and proc.block.block_type.singleton_instance != proc.block:
                proc3 = None
                for proc2 in proc.block.block_type.singleton_instance.block_procedures:
                    # print(proc, proc2)
                    if proc.name == proc2.name:
                        if proc3 is not None:
                            raise Exception('proc3 is not None')
                        proc3 = proc2
                if proc3 is None:
                    raise Exception('proc3 is None')
                proc = proc3

            if isinstance(path[-1], blocks.Block) and path[-1].block_type.is_singleton:
                raise Exception('isinstance(path[-1], blocks.Blocks) and path[-1].block_type.is_singleton')

            if (path, proc.proc_identifier) in done:
                return

            if proc.declaration_procedure.stage.stage != stage:
                # TODO: Nicer error message?
                raise Exception(f'Attempt to add procedure {proc} which is in stage {proc.declaration_procedure.stage.stage} during stage {stage}')

            # print(f'Going to add {path} {proc}')
            if (path, proc.proc_identifier) in doing:
                raise Exception(f'Got circular procedure dependency: {doing}')
            doing.append((path, proc.proc_identifier))
            for dep in proc.dependencies:
                if blocks.ast.dependency_is_resource(dep):
                    continue
                elif isinstance(dep, blocks.Block.StateVariable):
                    continue
                elif isinstance(dep, blocks.Block.Input) and dep.is_list:
                    for child in dep.members:
                        producer_path, producer_temporary = blocks.resolve_variable(path, child)
                        do(producer_path, producer_temporary.producer)
                else:
                    producer_path, producer_temporary = blocks.resolve_variable(path, dep)
                    do(producer_path, producer_temporary.producer)
            if doing.pop() != (path, proc.proc_identifier):
                raise Exception('doing.pop() != (path, proc.proc_identifier)')
            # print(f'Adding {path} {proc}')

            procs[stage].append((path, proc))

            if (path, proc.proc_identifier) in done:
                raise Exception('(path, proc.proc_identifier) in done')
            done.add((path, proc.proc_identifier))

        for stage in blocks.RunStage:
            procs[stage] = []
            for block_name in sorted(graph.blocks):
                block = graph.blocks[block_name]
                # print('EE', block, block.get_block_procedures_paths(()))
                for path, proc in block.get_block_procedures_paths(()):
                    if proc.declaration_procedure.stage.stage != stage:
                        continue
                    do(path, proc)
                    if len(doing):
                        raise Exception('len(doing)')

        all_procs = []
        for stage_procs in procs.values():
            all_procs += stage_procs

        res = Program()
        res.procs = procs
        res.all_procs_set = set(all_procs)
        res.graph = graph
        res.variable_map = variable_map
        res.state_map = state_map
        res.state_map_new = state_map_new
        res.state_map_changed = state_map_changed
        res.temporaries = blocks.VariableBlock(all_variables, f'{graph}.temporaries')
        res.state_variables = blocks.VariableBlock(state_variables, f'{graph}.state_variables')
        res.instances = instances
        res.native_triggers = {path: {} for path in instances}
        for path, proc in all_procs:
            res.native_triggers[path][proc.declaration_procedure.python_name] = proc

        def resource_paths(path, source, binding):
            is_list = isinstance(source, blocks.Block.Input) and source.is_list
            members = source.members if is_list else (source,)
            paths = []
            for member in members:
                try:
                    provider_path, output = blocks.resolve_variable(path, member)
                except RecursionError as exc:
                    raise blocks.ProgramError(binding, 'Circular resource connection') from exc
                if not instances[provider_path].block_type.is_native:
                    raise blocks.ProgramError(binding, 'Resource binding has no native provider', hints=[
                        blocks.CompilerHint(output.conn_spec, 'Resource output was declared here'),
                    ])
                paths.append(provider_path)
            return tuple(paths), is_list

        res.initializer_bindings = {}
        construction_dependencies = {}
        for path, instance in instances.items():
            block = instance.block if instance.block is not None else instance.block_type.singleton_instance
            bindings = []
            dependencies = []
            for name, (source, binding) in block.initializer_sources.items():
                paths, is_list = resource_paths(path, source, binding)
                bindings.append((name, paths, is_list))
                dependencies.extend((provider, binding) for provider in paths)
            res.initializer_bindings[path] = bindings
            construction_dependencies[path] = dependencies

        construction_order = []
        constructed = set()
        constructing = set()

        def order_construction(path):
            if path in constructed:
                return
            constructing.add(path)
            for provider, binding in construction_dependencies[path]:
                if provider in constructing:
                    raise blocks.ProgramError(binding, 'Circular native initializer dependency')
                order_construction(provider)
            constructing.remove(path)
            constructed.add(path)
            construction_order.append(path)

        for path in instances:
            order_construction(path)
        res.native_construction_order = construction_order

        proc_static_contexts = {}
        for stage_procs in res.procs.values():
            for path, proc in stage_procs:
                instance = res.instances[path]
                proc_context = proc.static_context
                proc_context = blocks.StaticProcedureContext(sources=proc_context._sources, targets=proc_context._targets, name=proc_context.name, block_parameter_values=proc_context.block_parameter_values, block=instance.static_context)
                for name, source in proc_context._sources.items():
                    if blocks.ast.dependency_is_resource(source):
                        binding = next(binding for binding in proc.declaration_procedure.depends.dependencies if binding.key == name)
                        paths, is_list = resource_paths(path, source, binding)
                        proc_context._resource_bindings.append((name, paths, is_list))
                        continue
                    is_state = isinstance(source, blocks.Block.StateVariable)
                    is_list = isinstance(source, blocks.Block.Input) and source.is_list
                    if is_state:
                        variables = (state_map[path, source],)
                    else:
                        members = source.members if is_list else (source,)
                        variables = []
                        for member in members:
                            producer_path, temporary = blocks.resolve_variable(path, member)
                            variables.append(variable_map[producer_path, temporary.var_identifier])
                        variables = tuple(variables)
                    proc_context._input_bindings.append((name, variables, is_state, is_list))
                for name, target in proc_context._targets.items():
                    if isinstance(target, blocks.Block.StateVariable):
                        variable = state_map[path, target]
                    else:
                        variable = variable_map[path, target.var_identifier]
                    proc_context._result_types[name] = variable.var_type
                proc_static_contexts[(instance, proc)] = proc_context
                # print(instance, proc, proc_context)
        res.proc_static_contexts = proc_static_contexts

        return res
