"""Translate compiler structures into runtime programs."""

import traceback

from wirely.compiler import model as blocks
from wirely.runtime.program import NativeInstance, Procedure, Program, ResultBinding
from wirely.compiler.types import resolve_type


def compile(global_block_type):
    try:
        return compile_nocatch(global_block_type)
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
                var_type = resolve_type(var_type, block.par_values)
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
        block.instantiate(singletons=singletons, variable_map=variable_map, state_map_new=state_map_new, all_variables=all_variables, state_map_changed=state_map_changed, state_map=state_map, state_variables=state_variables, instances=instances, path=(block,), parent_instance=None)

    def resolve_binding(path, source):
        source_path, variable = blocks.resolve_variable(path, source)
        if isinstance(variable, blocks.StateRead):
            state = variable.state
            if variable.phase == 'new':
                if state.producer is None:
                    raise blocks.ProgramError(variable.reference, f'State {state.name}.new requires a producing procedure')
                return blocks.InputSourceBinding(state_map_new[source_path, state])
            return blocks.InputSourceBinding(
                state_map[source_path, state],
                state_map_changed[source_path, state],
            )
        if isinstance(variable, blocks.Block.StateVariable):
            return blocks.InputSourceBinding(
                state_map[source_path, variable],
                state_map_changed[source_path, variable],
            )
        return blocks.InputSourceBinding(add_var(source_path, variable))

    for path in instances:
        block = path[-1]
        if not isinstance(block, blocks.Block):
            block = block.singleton_instance
        block.bind_variables(path=path, variable_map=variable_map, resolve_binding=resolve_binding)

    procs = {}
    done = set()
    doing = []
    def do(path, proc, reference=None):
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
            raise blocks.ProgramError(reference or proc.declaration_procedure,
                                      'Dependency producer belongs to a later execution stage', hints=[
                blocks.CompilerHint(proc.declaration_procedure, 'Producer was declared here'),
            ])

        # print(f'Going to add {path} {proc}')
        if (path, proc.proc_identifier) in doing:
            raise blocks.ProgramError(reference or proc.declaration_procedure,
                                      'Circular procedure dependency', hints=[
                blocks.CompilerHint(proc.declaration_procedure, 'Producer was declared here'),
            ])
        doing.append((path, proc.proc_identifier))
        for dep in proc.dependencies:
            if blocks.ast.dependency_is_resource(dep):
                continue
            else:
                members = dep.members if isinstance(dep, blocks.Block.Input) and dep.is_list else (dep,)
                for member in members:
                    producer_path, variable = blocks.resolve_variable(path, member)
                    if isinstance(variable, blocks.StateRead):
                        if variable.phase == 'new':
                            do(producer_path, variable.state.producer, variable.reference)
                        continue
                    if not isinstance(variable, blocks.Block.StateVariable):
                        do(producer_path, variable.producer, proc.declaration_procedure.depends)
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

    node_ids = {path: instance.static_context.name
                for path, instance in instances.items()}

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

    initializer_bindings = {}
    construction_dependencies = {}
    for path, instance in instances.items():
        block = instance.block if instance.block is not None else instance.block_type.singleton_instance
        bindings = []
        dependencies = []
        for name, (source, binding) in block.initializer_sources.items():
            paths, is_list = resource_paths(path, source, binding)
            bindings.append((name, paths, is_list))
            dependencies.extend((provider, binding) for provider in paths)
        initializer_bindings[path] = tuple(
            (name, tuple(node_ids[provider] for provider in paths), is_list)
            for name, paths, is_list in bindings
        )
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
    native_construction_order = tuple(node_ids[path] for path in construction_order)
    native_instances = {
        node_ids[path]: NativeInstance(
            name=node_ids[path], type_name=instance.block_type.fullname_tuple,
            has_native_state=instance.block_type.is_native,
            parameters=instance.static_context.parameters,
            configuration=instance.static_context.configuration,
            initializer_bindings=initializer_bindings[path],
        )
        for path, instance in instances.items()
    }
    initial_state = {}
    for path, instance in instances.items():
        block = instance.block or instance.block_type.singleton_instance
        for state in block.block_statevars:
            initial_state[state_map[path, state]] = state.initial_value

    schedules = {}
    for stage, stage_procs in procs.items():
        schedule = []
        for path, proc in stage_procs:
            dependencies = []
            resources = []
            results = {}
            for name, source in proc.static_context._sources.items():
                if blocks.ast.dependency_is_resource(source):
                    binding = next(binding for binding in proc.declaration_procedure.depends.dependencies
                                   if binding.key == name)
                    paths, is_list = resource_paths(path, source, binding)
                    resources.append((name, tuple(node_ids[provider] for provider in paths), is_list))
                    continue
                is_list = isinstance(source, blocks.Block.Input) and source.is_list
                members = source.members if is_list else (source,)
                dependencies.append((name, tuple(resolve_binding(path, member) for member in members), is_list))
            for name, target in proc.static_context._targets.items():
                if isinstance(target, blocks.Block.StateVariable):
                    results[name] = ResultBinding(
                        state_map[path, target], state_map_new[path, target],
                        state_map_changed[path, target],
                    )
                else:
                    results[name] = ResultBinding(variable_map[path, target.var_identifier])
            schedule.append(Procedure(
                node_id=node_ids[path],
                reference=(instances[path].block_type.fullname_tuple, proc.declaration_procedure.python_name),
                node_context=native_instances[node_ids[path]],
                dependencies=tuple(dependencies), resources=tuple(resources),
                results=results,
                state_dependencies=tuple(state_map_changed[path, state] for state in proc.block.block_statevars),
            ))
        schedules[stage] = tuple(schedule)
    node_aliases = {}
    variables = {}

    def add_aliases(scope, path=(), prefix=''):
        for name, block in scope.blocks.items():
            child_path = blocks.path_resolve_singleton(path + (block,))
            alias = prefix + name
            node_aliases[alias] = node_ids[child_path]
            owner = block.block_type.singleton_instance if block.block_type.is_singleton else block
            members = owner.block_inputs + owner.block_outputs + owner.block_statevars + owner.block_temporaries
            for member in members:
                variable = variable_map.get((child_path, member.var_identifier))
                if variable is not None:
                    variables.setdefault(alias + '.' + member.name, variable)
            output = owner.block_outputs_byname.get('')
            if output is not None:
                variable = variable_map.get((child_path, output.var_identifier))
                if variable is not None:
                    variables.setdefault(alias, variable)
            add_aliases(block.block_type, child_path, alias + '.')

    add_aliases(graph)
    singleton_aliases = {
        instance.block_type.fullname: node_ids[path]
        for path, instance in instances.items() if instance.is_singleton
    }
    return Program(
        name=str(graph), graph=graph,
        temporaries=blocks.VariableBlock(all_variables, f'{graph}.temporaries'),
        state_variables=blocks.VariableBlock(state_variables, f'{graph}.state_variables'),
        native_construction_order=native_construction_order,
        instances=native_instances, initial_state=initial_state, procs=schedules,
        node_aliases=node_aliases, variables=variables, singleton_aliases=singleton_aliases,
    )
