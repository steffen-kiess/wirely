"""Translate compiler structures into runtime programs."""

from wirely.compiler import model
from wirely.compiler.diagnostics import error_diagnostics, print_diagnostic
from wirely.compiler.types import resolve_type, check_runtime_type
from wirely.program import (
    Constant, Function, InputSource, NativeInstance, OutputTarget, Procedure, Program,
    RunStage, Variable, VariableTable,
)


def compile(global_node_type, *, on_diagnostic=None):
    try:
        return compile_nocatch(global_node_type)
    except model.ProgramError as exc:
        report = print_diagnostic if on_diagnostic is None else on_diagnostic
        always_print_stacktrace = model.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)
        for diagnostic in error_diagnostics(exc, always_print_stacktrace=always_print_stacktrace):
            report(diagnostic)
        return None


def compile_nocatch(global_node_type):
    # TODO: More checks, e.g. that global_node_type has not parameters or inputs / outputs
    graph = global_node_type
    root = None
    if graph.value_nodes or graph.procedures or graph.parsed_constants or graph.assertions:
        for declaration in graph.procedures.values():
            if declaration.is_native:
                raise model.ProgramError(declaration, 'Native procedures must belong to a node type')
            declaration.compile_interface(graph)
        model.ast.validate_nodes(graph)
        root = graph.create(name='<global>', graph=graph, par_values={},
                            instantiation=graph, register=False)

    variable_map = {}
    all_variables = []
    state_map = {}
    state_variables = []
    # TODO: Try to avoid needing this by making sure everyone uses instance values instead of paths?
    instances = {}

    def add_var(path, var):
        # TODO: Clean up handling of singletons (vars probably should contain a reference to a NodeType, not to an InstanceNode)
        if (path, var.var_identifier) in variable_map:
            return variable_map[path, var.var_identifier]

        # print(f'add_var({path}, {var.node})')
        if path[-1] != var.node:
            if isinstance(path[-1], model.InstanceNode):  # TODO: Handle singletons here?
                raise Exception('path[-1] != var.node')
        # TODO: Move this code somewhere else?
        parameters = {}
        instance = instances[path]
        while instance is not None:
            parameters.update(instance.static_values)
            instance = instance.parent_instance
        var_type = resolve_type(var.impl_var_type, parameters)
        owner = instances[path].node or instances[path].node_type.singleton_instance
        if not owner.is_root:
            source = owner.instantiation
        elif getattr(var, 'port', None) is not None:
            # Report a variable fed by the port at its declaration.
            target = next((connection.target for connection in owner.node_type.connections
                           if connection.source is var.port
                           and isinstance(connection.target, model.TemporaryNode)), None)
            source = (var.port.declaration if target is None
                      else owner.node_type.value_nodes[target.name])
        else:
            source = owner.node_type.value_nodes.get(var.name, owner.instantiation)
        check_runtime_type(var_type, source)
        # TODO: Clean up name?
        # name = '.'.join([node.name for node in path])
        name = model.InstanceNode.path_name(path)
        # Port storage is named after its procedure: <path>:<procedure>.<port>.
        separator = ':' if getattr(var, 'port', None) is not None else '.'
        variable_name = name + separator + var.name if name else var.name
        v = Variable(variable_name, var_type)
        # print(v.name)
        input_source = InputSource(v)
        variable_map[path, var.var_identifier] = input_source
        all_variables.append(v)
        return input_source

    # TODO: Use get_all_node_paths()?
    singletons = {}
    top_nodes = (root,) if root is not None else tuple(graph.instance_nodes.values())
    for node in top_nodes:
        node.instantiate(singletons=singletons, variable_map=variable_map, all_variables=all_variables, state_map=state_map, state_variables=state_variables, instances=instances, path=(node,), parent_instance=None)

    def resolve_source(path, source):
        source_path, variable = model.resolve_variable(path, source)
        if isinstance(variable, model.StateRead):
            return InputSource(state_map[source_path, variable.state], new_state=variable.phase == 'new')
        if isinstance(variable, model.InstanceNode.StateVariable):
            return InputSource(state_map[source_path, variable], new_state=False)
        return add_var(source_path, variable)

    for path in instances:
        node = path[-1]
        if not isinstance(node, model.InstanceNode):
            node = node.singleton_instance
        node.bind_variables(path=path, variable_map=variable_map, resolve_source=resolve_source)

    procs = {}
    done = set()
    doing = []
    def do(path, proc, reference=None):
        # TODO: Clean this up (this makes sure that the correct Procedure instance is used for singletons)
        if proc.node.node_type.is_singleton and proc.node.node_type.singleton_instance != proc.node:
            proc3 = None
            for proc2 in proc.node.node_type.singleton_instance.procedures:
                # print(proc, proc2)
                if proc.name == proc2.name:
                    if proc3 is not None:
                        raise Exception('proc3 is not None')
                    proc3 = proc2
            if proc3 is None:
                raise Exception('proc3 is None')
            proc = proc3

        if isinstance(path[-1], model.InstanceNode) and path[-1].node_type.is_singleton:
            raise Exception('isinstance(path[-1], model.InstanceNode) and path[-1].node_type.is_singleton')

        if (path, proc.proc_identifier) in done:
            return

        if proc.declaration_procedure.stage.stage != stage:
            raise model.ProgramError(reference or proc.declaration_procedure,
                                      'Dependency producer belongs to a later execution stage', hints=[
                model.CompilerHint(proc.declaration_procedure, 'Producer was declared here'),
            ])

        # print(f'Going to add {path} {proc}')
        if (path, proc.proc_identifier) in doing:
            raise model.ProgramError(reference or proc.declaration_procedure,
                                      'Circular procedure dependency', hints=[
                model.CompilerHint(proc.declaration_procedure, 'Producer was declared here'),
            ])
        doing.append((path, proc.proc_identifier))
        for dep in proc.dependencies:
            if model.ast.dependency_is_resource(dep):
                continue
            else:
                members = dep.members if getattr(dep, 'is_list', False) else (dep,)
                for member in members:
                    producer_path, variable = model.resolve_variable(path, member)
                    if isinstance(variable, model.StateRead):
                        if variable.phase == 'new':
                            do(producer_path, variable.state.producer, variable.reference)
                        continue
                    if not isinstance(variable, model.InstanceNode.StateVariable):
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
        scheduled_nodes = (root,) if root is not None else tuple(
            graph.instance_nodes[name] for name in sorted(graph.instance_nodes)
        )
        for node in scheduled_nodes:
            # print('EE', node, node.get_procedure_paths(()))
            for path, proc in node.get_procedure_paths(()):
                if proc.declaration_procedure.stage.stage != stage:
                    continue
                do(path, proc)
                if len(doing):
                    raise Exception('len(doing)')

    node_ids = {path: instance.static_context.name
                for path, instance in instances.items()}

    def resource_paths(path, source, port):
        is_list = getattr(source, 'is_list', False)
        members = source.members if is_list else (source,)
        paths = []
        for member in members:
            try:
                provider_path, output = model.resolve_variable(path, member)
            except RecursionError as exc:
                raise model.ProgramError(port, 'Circular resource connection') from exc
            if not instances[provider_path].node_type.is_native:
                raise model.ProgramError(port, 'Resource input has no native provider', hints=[
                    model.CompilerHint(output.port_spec, 'Resource output was declared here'),
                ])
            paths.append(provider_path)
        return tuple(paths), is_list

    initializer_bindings = {}
    construction_dependencies = {}
    for path, instance in instances.items():
        node = instance.node if instance.node is not None else instance.node_type.singleton_instance
        bindings = []
        dependencies = []
        for name, (source, port) in node.initializer_sources.items():
            paths, is_list = resource_paths(path, source, port)
            bindings.append((name, paths, is_list))
            dependencies.extend((provider, port) for provider in paths)
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
        for provider, port in construction_dependencies[path]:
            if provider in constructing:
                raise model.ProgramError(port, 'Circular native initializer dependency')
            order_construction(provider)
        constructing.remove(path)
        constructed.add(path)
        construction_order.append(path)

    for path in instances:
        order_construction(path)
    native_construction_order = tuple(node_ids[path] for path in construction_order)
    native_instances = {
        node_ids[path]: NativeInstance(
            name=node_ids[path], type_name=instance.node_type.fullname_tuple,
            has_native_state=instance.node_type.is_native,
            parameters=instance.static_context.parameters,
            initializer_bindings=initializer_bindings[path],
            initializer_values=instance.initializer_values,
        )
        for path, instance in instances.items()
    }
    initial_state = {}
    for path, instance in instances.items():
        node = instance.node or instance.node_type.singleton_instance
        for state in node.state_variables:
            value_type = resolve_type(state.var_type, instance.static_values)
            with state.initial_value.context:
                value = instance.node_type.get_const_value(
                    state.initial_value, value_type, instance.static_values,
                )
                try:
                    initial_state[state_map[path, state]] = value_type.verify_value(value.evaluate())
                except (ValueError, ArithmeticError) as error:
                    raise model.ProgramError(state.initial_value, str(error)) from error

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
                port = next(port for port in declaration.depends.dependencies
                               if port.key == name)
                if model.ast.dependency_is_resource(source):
                    raise model.ProgramError(port, 'Procedures cannot access resources')
                if getattr(source, 'is_list', False):
                    raise model.ProgramError(port, 'Multi inputs are not supported in procedures')
                spec = getattr(source, 'port_spec', None)
                if spec is not None and spec.kind_spec.kind == model.ConnectionKind.Event:
                    raise model.ProgramError(port, 'Event ports are not supported in procedures')
                source_types[name] = resolve_source(path, source).variable.var_type
            output_types = {}
            for name, target in proc.static_context._targets.items():
                spec = getattr(target, 'port_spec', None)
                port = next(port for port in proc.declaration_procedure.produces.outputs
                               if port.key == name)
                if isinstance(spec, model.PortTypeSpecificationResource):
                    raise model.ProgramError(port, 'Procedures cannot produce resources')
                if spec is not None and spec.kind_spec.kind == model.ConnectionKind.Event:
                    raise model.ProgramError(port, 'Event ports are not supported in procedures')
                variable = (state_map[path, target] if isinstance(target, model.InstanceNode.StateVariable)
                            else variable_map[path, target.var_identifier].variable)
                output_types[name] = variable.var_type
            bodies[path, proc] = compile_procedure(proc, instances[path], source_types, output_types,
                                                 intern_constant, intern_function)

    from .static_bindings import evaluate_bindings
    schedules = {}
    for stage, stage_procs in procs.items():
        schedule = []
        for path, proc in stage_procs:
            dependencies = []
            resources = []
            outputs = {}
            for name, source in proc.static_context._sources.items():
                if model.ast.dependency_is_resource(source):
                    port = next(port for port in proc.declaration_procedure.depends.dependencies
                                   if port.key == name)
                    paths, is_list = resource_paths(path, source, port)
                    resources.append((name, tuple(node_ids[provider] for provider in paths), is_list))
                    continue
                is_list = getattr(source, 'is_list', False)
                members = source.members if is_list else (source,)
                dependencies.append((name, tuple(resolve_source(path, member) for member in members), is_list))
            for name, target in proc.static_context._targets.items():
                if isinstance(target, model.InstanceNode.StateVariable):
                    outputs[name] = OutputTarget(state_map[path, target])
                else:
                    outputs[name] = OutputTarget(variable_map[path, target.var_identifier].variable)
            schedule.append(Procedure(
                node_id=node_ids[path],
                reference=(instances[path].node_type.fullname_tuple, proc.declaration_procedure.python_name),
                node_context=native_instances[node_ids[path]],
                dependencies=tuple(dependencies), resources=tuple(resources),
                outputs=outputs, body=bodies.get((path, proc)),
                values=evaluate_bindings(proc.declaration_procedure.uses, instances[path]),
            ))
        schedules[stage] = tuple(schedule)
    node_aliases = {}
    variables = {}

    def aliased_items(items):
        # Default outputs are aliased by their node, other outputs by their own name.
        return [item for item in items
                if item.name != '' and getattr(item, 'port', None) is None]

    def add_aliases(scope, path=(), prefix=''):
        for name, node in scope.instance_nodes.items():
            child_path = model.path_resolve_singleton(path + (node,))
            alias = prefix + name
            node_aliases[alias] = node_ids[child_path]
            owner = node.node_type.singleton_instance if node.node_type.is_singleton else node
            items = owner.input_ports + owner.output_ports + owner.state_variables + owner.temporaries
            for item in aliased_items(items):
                input_source = variable_map.get((child_path, item.var_identifier))
                if input_source is not None:
                    variables.setdefault(alias + '.' + item.name, input_source)
            output = owner.output_ports_byname.get('')
            if output is not None:
                input_source = variable_map.get((child_path, output.var_identifier))
                if input_source is not None:
                    variables.setdefault(alias, input_source)
            add_aliases(node.node_type, child_path, alias + '.')

    root_path = (root,) if root is not None else ()
    add_aliases(graph, root_path)
    if root is not None:
        for item in aliased_items(root.temporaries + root.state_variables):
            variables[item.name] = variable_map[root_path, item.var_identifier]
    singleton_aliases = {
        instance.node_type.fullname: node_ids[path]
        for path, instance in instances.items() if instance.is_singleton
    }
    return Program(
        name=str(graph), graph=graph,
        temporaries=VariableTable(all_variables, f'{graph}.temporaries'),
        state_variables=VariableTable(state_variables, f'{graph}.state_variables'),
        native_construction_order=native_construction_order,
        instances=native_instances, initial_state=initial_state, procs=schedules,
        node_aliases=node_aliases, variables=variables, singleton_aliases=singleton_aliases,
        constants=tuple(constants),
        functions=tuple(functions),
    )
