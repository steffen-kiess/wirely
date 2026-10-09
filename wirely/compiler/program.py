"""Translate compiler structures into runtime programs."""

from wirely.compiler import model as blocks
from wirely.compiler.diagnostics import error_diagnostics, print_diagnostic
from wirely.compiler.types import resolve_type, check_runtime_type
from wirely.program import (
    Constant, Function, InputSourceBinding, NativeInstance, Procedure, Program, ResultBinding,
    RunStage, Variable, VariableBlock,
)


def compile(global_block_type, *, on_diagnostic=None):
    try:
        return compile_nocatch(global_block_type)
    except blocks.ProgramError as exc:
        report = print_diagnostic if on_diagnostic is None else on_diagnostic
        always_print_stacktrace = blocks.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)
        for diagnostic in error_diagnostics(exc, always_print_stacktrace=always_print_stacktrace):
            report(diagnostic)
        return None


def compile_nocatch(global_block_type):
    # TODO: More checks, e.g. that global_block_type has not parameters or inputs / outputs
    graph = global_block_type
    root = None
    if graph.variables or graph.procedures or graph.parsed_constants or graph.assertions:
        for declaration in graph.procedures.values():
            if declaration.is_native:
                raise blocks.ProgramError(declaration, 'Native procedures must belong to a node type')
            declaration.compile_interface(graph)
        root = graph.create(name='<global>', graph=graph, par_values={},
                            instantiation=graph, register=False)

    variable_map = {}
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
        parameters = {}
        instance = instances[path]
        while instance is not None:
            parameters.update(instance.static_values)
            instance = instance.parent_instance
        var_type = resolve_type(var.impl_var_type, parameters)
        owner = instances[path].block or instances[path].block_type.singleton_instance
        source = (owner.block_type.variables.get(var.name, owner.instantiation)
                  if owner.is_root else owner.instantiation)
        check_runtime_type(var_type, source)
        # TODO: Clean up name?
        # name = '.'.join([block.name for block in path])
        name = blocks.Block.path_name(path)
        variable_name = name + '.' + var.name if name else var.name
        v = Variable(variable_name, var_type)
        # print(v.name)
        binding = InputSourceBinding(v)
        variable_map[path, var.var_identifier] = binding
        all_variables.append(v)
        return binding

    # TODO: Use get_all_block_paths()?
    singletons = {}
    top_blocks = (root,) if root is not None else tuple(graph.blocks.values())
    for block in top_blocks:
        block.instantiate(singletons=singletons, variable_map=variable_map, all_variables=all_variables, state_map=state_map, state_variables=state_variables, instances=instances, path=(block,), parent_instance=None)

    def resolve_binding(path, source):
        source_path, variable = blocks.resolve_variable(path, source)
        if isinstance(variable, blocks.StateRead):
            state = variable.state
            if variable.phase == 'new':
                if state.producer is None:
                    raise blocks.ProgramError(variable.reference, f'State {state.name}.new requires a producing procedure')
                return InputSourceBinding(state_map[source_path, state], new_state=True)
            return InputSourceBinding(state_map[source_path, state], new_state=False)
        if isinstance(variable, blocks.Block.StateVariable):
            return InputSourceBinding(state_map[source_path, variable], new_state=False)
        return add_var(source_path, variable)

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

    for stage in RunStage:
        procs[stage] = []
        scheduled_blocks = (root,) if root is not None else tuple(
            graph.blocks[name] for name in sorted(graph.blocks)
        )
        for block in scheduled_blocks:
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
            initializer_bindings=initializer_bindings[path],
            initializer_values=instance.initializer_values,
        )
        for path, instance in instances.items()
    }
    initial_state = {}
    for path, instance in instances.items():
        block = instance.block or instance.block_type.singleton_instance
        for state in block.block_statevars:
            value_type = resolve_type(state.var_type, instance.static_values)
            with state.initial_value.context:
                value = instance.block_type.get_const_value(
                    state.initial_value, value_type, instance.static_values,
                )
                try:
                    initial_state[state_map[path, state]] = value_type.verify_value(value.evaluate())
                except (ValueError, ArithmeticError) as error:
                    raise blocks.ProgramError(state.initial_value, str(error)) from error

    # Compile .wrl bodies after instance parameters and variable types resolve.
    from .expressions import compile_procedure
    bodies = {}
    constants = []
    constant_indices = {}

    functions = []
    function_indices = {}

    def intern_function(declaration):
        if declaration.function_name not in function_indices:
            function_indices[declaration.function_name] = len(functions)
            functions.append(Function(declaration.function_name, declaration.argument_names,
                                      declaration.parameter_types, declaration.return_type))
        return function_indices[declaration.function_name]

    def intern_constant(value_type, value):
        value = value_type.verify_value(value)
        key = value_type, value_type.serialize(value)
        if key not in constant_indices:
            constant_indices[key] = len(constants)
            constants.append(Constant(value_type, value))
        return constant_indices[key]

    for stage_procs in procs.values():
        for path, proc in stage_procs:
            if proc.declaration_procedure.is_native:
                continue
            source_types = {}
            for name, source in proc.static_context._sources.items():
                declaration = proc.declaration_procedure
                binding = next(binding for binding in declaration.depends.dependencies
                               if binding.key == name)
                if blocks.ast.dependency_is_resource(source):
                    raise blocks.ProgramError(binding, 'Procedures cannot access resources')
                if getattr(source, 'is_list', False):
                    raise blocks.ProgramError(binding, 'Multi inputs are not supported in procedures')
                spec = getattr(source, 'conn_spec', None)
                if spec is not None and spec.kind_spec.kind == blocks.ConnectionKind.Event:
                    raise blocks.ProgramError(binding, 'Event connectors are not supported in procedures')
                source_types[name] = resolve_binding(path, source).variable.var_type
            result_types = {}
            for name, target in proc.static_context._targets.items():
                spec = getattr(target, 'conn_spec', None)
                binding = next(binding for binding in proc.declaration_procedure.produces.outputs
                               if binding.key == name)
                if isinstance(spec, blocks.ConnectorSpecificationResource):
                    raise blocks.ProgramError(binding, 'Procedures cannot produce resources')
                if spec is not None and spec.kind_spec.kind == blocks.ConnectionKind.Event:
                    raise blocks.ProgramError(binding, 'Event connectors are not supported in procedures')
                variable = (state_map[path, target] if isinstance(target, blocks.Block.StateVariable)
                            else variable_map[path, target.var_identifier].variable)
                result_types[name] = variable.var_type
            bodies[path, proc] = compile_procedure(proc, instances[path], source_types, result_types,
                                                 intern_constant, intern_function)

    from .static_bindings import evaluate_bindings
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
                    results[name] = ResultBinding(state_map[path, target])
                else:
                    results[name] = ResultBinding(variable_map[path, target.var_identifier].variable)
            schedule.append(Procedure(
                node_id=node_ids[path],
                reference=(instances[path].block_type.fullname_tuple, proc.declaration_procedure.python_name),
                node_context=native_instances[node_ids[path]],
                dependencies=tuple(dependencies), resources=tuple(resources),
                results=results, body=bodies.get((path, proc)),
                values=evaluate_bindings(proc.declaration_procedure.uses, instances[path]),
            ))
        schedules[stage] = tuple(schedule)
    node_aliases = {}
    variables = {}

    def aliased_members(owner, members):
        # Default outputs are aliased by their node, other outputs by their own name.
        storage = {output.orig_temporary for output in owner.block_outputs if hasattr(output, 'orig_temporary')}
        return [member for member in members if member.name != '' and member not in storage]

    def add_aliases(scope, path=(), prefix=''):
        for name, block in scope.blocks.items():
            child_path = blocks.path_resolve_singleton(path + (block,))
            alias = prefix + name
            node_aliases[alias] = node_ids[child_path]
            owner = block.block_type.singleton_instance if block.block_type.is_singleton else block
            members = owner.block_inputs + owner.block_outputs + owner.block_statevars + owner.block_temporaries
            for member in aliased_members(owner, members):
                binding = variable_map.get((child_path, member.var_identifier))
                if binding is not None:
                    variables.setdefault(alias + '.' + member.name, binding)
            output = owner.block_outputs_byname.get('')
            if output is not None:
                binding = variable_map.get((child_path, output.var_identifier))
                if binding is not None:
                    variables.setdefault(alias, binding)
            add_aliases(block.block_type, child_path, alias + '.')

    root_path = (root,) if root is not None else ()
    add_aliases(graph, root_path)
    if root is not None:
        for member in aliased_members(root, root.block_temporaries + root.block_statevars):
            variables[member.name] = variable_map[root_path, member.var_identifier]
    singleton_aliases = {
        instance.block_type.fullname: node_ids[path]
        for path, instance in instances.items() if instance.is_singleton
    }
    return Program(
        name=str(graph), graph=graph,
        temporaries=VariableBlock(all_variables, f'{graph}.temporaries'),
        state_variables=VariableBlock(state_variables, f'{graph}.state_variables'),
        native_construction_order=native_construction_order,
        instances=native_instances, initial_state=initial_state, procs=schedules,
        node_aliases=node_aliases, variables=variables, singleton_aliases=singleton_aliases,
        constants=tuple(constants),
        functions=tuple(functions),
    )
