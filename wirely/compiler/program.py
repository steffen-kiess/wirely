"""Translate compiler structures into runtime programs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .instance import NodePath
    from .parameters import StaticValue

from wirely.compiler import model
from wirely.compiler.diagnostics import Diagnostic, error_diagnostics, print_diagnostic
from wirely.compiler.types import resolve_type, check_runtime_type
from .expressions import compile_constant, compile_procedure
from .stages import ResolutionState
from wirely.program import (
    Constant, Function, InputSource, Instruction, NativeInstance, Opcode, Operand, OperandKind,
    NativeNodeType, NativeProcedureInterface, OutputTarget, Procedure, ProcedureBody, Program, ResourceBinding,
    RunStage, Variable, VariableTable,
)

# Body of a procedure storing a copied state: output.0 = input.0.
COPY_BODY = ProcedureBody((
    Instruction(Opcode.COPY, (Operand(OperandKind.OUTPUT, 0), Operand(OperandKind.INPUT, 0))),
    Instruction(Opcode.RETURN),
))


def cycle_error(element: model.ast.SourceElement, message: str,
                steps: Sequence[tuple[model.ast.SourceElement | None, Sequence[model.Connection]]],
                via: Sequence[model.Connection]) -> model.ProgramError:
    """Report a dependency cycle at element, with hints following the data around it.

    steps are the (element, via) pairs from the start of the cycle to the
    reader closing it; via holds the connections from a producer to element.
    """
    elements = []
    for step_element, step_via in reversed(steps):
        elements += [connection.source_element for connection in step_via] + [step_element]
    elements += [connection.source_element for connection in via]
    hints: list = []
    for hint_element in elements:
        if hint_element is not element and all(hint_element is not hint.source_element for hint in hints):
            assert hint_element is not None
            hints.append(model.CompilerHint(hint_element, 'Cycle continues here'))
    return model.ProgramError(element, message, hints=hints)


def compile(global_node_type: model.NodeType, *,
            on_diagnostic: Callable[[Diagnostic], None] | None = None) -> Program | None:
    report = print_diagnostic if on_diagnostic is None else on_diagnostic
    warnings = global_node_type.coordinator.warnings
    assert warnings is not None
    previous_report, warnings.report = warnings.report, report
    try:
        return compile_nocatch(global_node_type)
    except model.ProgramError as exc:
        always_print_stacktrace = model.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)
        for diagnostic in error_diagnostics(exc, always_print_stacktrace=always_print_stacktrace):
            report(diagnostic)
        return None
    finally:
        warnings.report = previous_report


def compile_nocatch(global_node_type: model.NodeType) -> Program:
    return lower_program(global_node_type)


def specialize_instances(graph: model.NodeType, root: model.InstanceNode | None, *,
                         variable_map: dict[tuple[NodePath, str], InputSource], all_variables: list[Variable],
                         state_map: dict[tuple[NodePath, model.InstanceStateVariable], Variable],
                         state_variables: list[Variable], instances: dict[NodePath, model.NodeTypeInstance]) -> None:
    singletons: dict[model.NodeType, model.InstanceNode] = {}
    top_nodes = (root,) if root is not None else tuple(graph.instance_nodes.values())
    for node in top_nodes:
        node.specialize(singletons=singletons, variable_map=variable_map,
                        all_variables=all_variables, state_map=state_map,
                        state_variables=state_variables, instances=instances,
                        path=(node,), parent_instance=None)


# Sources may be data ports, state reads, static values, or resource ports.
def members(path: NodePath, source: Any) -> list[tuple[NodePath, Any, tuple[tuple[NodePath, Any], ...]]]:
    """Return (path, port, steps) for each source of a dependency, see list_members()."""
    if getattr(source, 'is_list', False):
        return model.list_members(path, source)
    return [(path, source, ())]


def lower_program(global_node_type: model.NodeType) -> Program:
    # TODO: More checks, e.g. that global_node_type has not parameters or inputs / outputs
    graph = global_node_type
    warnings = graph.coordinator.warnings
    assert warnings is not None
    if graph.body_state is not ResolutionState.COMPLETE:
        raise RuntimeError('Program lowering requires a bound and validated global graph')
    root = None
    if graph.value_nodes or graph.procedures or graph.parsed_constants or graph.assertions:
        root = graph.create_instance_interface(name='<global>', graph=graph, par_values={},
                                               instantiation=graph, register=False)
        graph.populate_instance(root)

    variable_map: dict[tuple[NodePath, str], InputSource] = {}
    all_variables: list[Variable] = []
    state_map: dict[tuple[NodePath, model.InstanceStateVariable], Variable] = {}
    state_variables: list[Variable] = []
    # TODO: Try to avoid needing this by making sure everyone uses instance values instead of paths?
    instances: dict[NodePath, model.NodeTypeInstance] = {}

    def add_var(path: NodePath, var: Any) -> InputSource:
        # TODO: Clean up handling of singletons (vars probably should contain a reference to a NodeType, not to an InstanceNode)
        if (path, var.var_identifier) in variable_map:
            return variable_map[path, var.var_identifier]

        # print(f'add_var({path}, {var.node})')
        if path[-1] != var.node:
            if isinstance(path[-1], model.InstanceNode):  # TODO: Handle singletons here?
                raise Exception('path[-1] != var.node')
        # TODO: Move this code somewhere else?
        parameters: dict[model.DeclarationParameter, StaticValue] = {}
        instance: model.NodeTypeInstance | None = instances[path]
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

    specialize_instances(graph, root, variable_map=variable_map, all_variables=all_variables,
                         state_map=state_map, state_variables=state_variables, instances=instances)

    constants: list[Constant] = []
    constant_indices: dict[tuple[model.Type, bytes], int] = {}

    def intern_constant(value_type: model.Type, value: object) -> int:
        value = value_type.verify_value(value)
        key = value_type, value_type.serialize(value)
        if key not in constant_indices:
            constant_indices[key] = len(constants)
            constants.append(Constant(value_type, value))
        return constant_indices[key]

    def static_source(path: NodePath, source: model.StaticSource) -> InputSource:
        """Return the constant supplying a parameter or constant connected as a source."""
        instance = instances[path]
        reference = source.reference
        assert isinstance(reference, model.Identifier)
        assert source.var_type is not None
        with reference.context, warnings.instance(instance.instantiation_hint()):
            try:
                value_type = resolve_type(source.var_type, instance.static_values)
                expression = model.Reference([reference], source_position=reference.source_position)
                value = compile_constant(instance.node_type, expression, value_type, instance.static_values)
                try:
                    concrete = value.resolve_self(instance.static_values).evaluate()
                except (ValueError, ArithmeticError) as error:
                    raise model.ProgramError(reference, str(error)) from error
            except model.ProgramError as error:
                hint = instance.instantiation_hint()
                if hint is not None:
                    error.hints = [*error.hints, hint]
                raise
        return InputSource(constant=constants[intern_constant(value_type, concrete)])

    def resolve_source(path: NodePath, source: Any) -> InputSource:
        source_path, variable = model.resolve_variable(path, source)
        if isinstance(variable, model.StaticSource):
            return static_source(source_path, variable)
        if isinstance(variable, model.StateRead):
            return InputSource(state_map[source_path, variable.state], new_state=variable.phase == 'new')
        if isinstance(variable, model.InstanceStateVariable):
            return InputSource(state_map[source_path, variable], new_state=False)
        return add_var(source_path, variable)

    for path in instances:
        node = path[-1]
        if not isinstance(node, model.InstanceNode):
            node = node.singleton_instance
        node.bind_variables(path=path, variable_map=variable_map, resolve_source=resolve_source)

    procs: dict[RunStage, list[tuple[NodePath, model.InstanceProcedure]]] = {}
    done: set[tuple[NodePath, str]] = set()
    doing: list[tuple[tuple[NodePath, str], model.ast.SourceElement | None, Sequence[model.Connection]]] = []
    def do(path: NodePath, proc: model.InstanceProcedure, reference: model.ast.SourceElement | None = None,
           via: Sequence[model.Connection] = ()) -> None:
        """Schedule proc after its producers; via has the connections from proc to reference."""
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
        key = path, proc.proc_identifier
        keys = [entry[0] for entry in doing]
        if key in keys:
            steps = [entry[1:] for entry in doing[keys.index(key) + 1:]]
            raise cycle_error(reference or proc.declaration_procedure, 'Circular procedure dependency',
                              steps, via)
        doing.append((key, reference, via))
        for dep in proc.dependencies:
            if model.ast.dependency_is_resource(dep):
                continue
            else:
                for member_path, member, member_steps in members(path, dep):
                    trace = {(step_path, id(step)): step for step_path, step in member_steps}
                    producer_path, variable = model.resolve_variable(member_path, member, trace)
                    connections = model.traced_connections(list(trace.values()))
                    if isinstance(variable, model.StateRead):
                        if variable.phase == 'new':
                            assert isinstance(variable.state, model.InstanceStateVariable)
                            assert variable.state.producer is not None
                            assert isinstance(variable.reference, model.ast.SourceElement)
                            do(producer_path, variable.state.producer, variable.reference, connections)
                        continue
                    if not isinstance(variable, (model.InstanceStateVariable, model.StaticSource)):
                        do(producer_path, variable.producer, proc.declaration_procedure.depends, connections)
        if doing.pop()[0] != key:
            raise Exception('doing.pop()[0] != key')
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

    def resource_path(path: NodePath, source: Any,
                      port: model.ast.SourceElement) -> tuple[NodePath, ResourceBinding, list[model.Connection]]:
        """Return a resource provider, its binding, and the connections leading to it."""
        trace: dict = {}
        provider_path, output = model.resolve_variable(path, source, trace)
        node_type = instances[provider_path].node_type
        if not node_type.is_native:
            raise model.ProgramError(port, 'Resource input has no native provider', hints=[
                model.CompilerHint(output.port_spec, 'Resource output was declared here'),
            ])
        produced = output.resource if isinstance(output, model.InstanceLocalResource) else output.type_output_port
        assert node_type.initializer is not None
        binding = ResourceBinding(node_ids[provider_path], node_type.initializer.output_keys[produced])
        return provider_path, binding, model.traced_connections(list(trace.values()))

    initializer_bindings: dict[NodePath, dict[str, ResourceBinding]] = {}
    construction_dependencies: dict[NodePath, list[tuple[NodePath, model.ast.SourceElement, list[model.Connection]]]] = {}
    for path, instance in instances.items():
        node = instance.node if instance.node is not None else instance.node_type.singleton_instance
        bindings = {}
        providers: list[tuple[NodePath, model.ast.SourceElement, list[model.Connection]]] = []
        for name, (source, port) in node.initializer_sources.items():
            provider, bindings[name], via = resource_path(path, source, port)
            providers.append((provider, port, via))
        initializer_bindings[path] = bindings
        construction_dependencies[path] = providers

    construction_order: list[NodePath] = []
    constructed: set[NodePath] = set()
    # (path, port, via) of each node being constructed and how it was reached.
    constructing: list[tuple[NodePath, model.ast.SourceElement | None, Sequence[model.Connection]]] = []

    def order_construction(path: NodePath, port: model.ast.SourceElement | None = None,
                           via: Sequence[model.Connection] = ()) -> None:
        if path in constructed:
            return
        constructing.append((path, port, via))
        for provider, provider_port, provider_via in construction_dependencies[path]:
            paths = [entry[0] for entry in constructing]
            if provider in paths:
                steps = [entry[1:] for entry in constructing[paths.index(provider) + 1:]]
                raise cycle_error(provider_port, 'Circular native initializer dependency', steps, provider_via)
            order_construction(provider, provider_port, provider_via)
        constructing.pop()
        constructed.add(path)
        construction_order.append(path)

    for path in instances:
        order_construction(path)
    native_construction_order = tuple(node_ids[path] for path in construction_order)
    native_instances = {
        node_ids[path]: NativeInstance(
            name=node_ids[path], type_name=instance.node_type.native_name,
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
    bodies: dict[tuple[NodePath, model.InstanceProcedure], ProcedureBody] = {}
    old_state_dependencies: dict[tuple[NodePath, model.InstanceProcedure], tuple[Variable, ...]] = {}

    functions: list[Function] = []
    function_indices: dict[str, int] = {}

    def intern_function(function: Function) -> int:
        if function.name not in function_indices:
            function_indices[function.name] = len(functions)
            functions.append(function)
        return function_indices[function.name]

    for stage_procs in procs.values():
        for path, proc in stage_procs:
            # Native multi value/event specialization is not implemented by the runtime.
            if proc.declaration_procedure.is_native and any(
                    getattr(source, 'is_list', False) and not model.ast.dependency_is_resource(source)
                    for source in proc.static_context._sources.values()):
                raise model.ProgramError(proc.declaration_procedure,
                                         'Native runtime does not support multi value or event dependencies')
            if proc.declaration_procedure.is_native:
                continue
            source_types: dict[str | tuple[str, int] | Variable, model.Type] = {}
            multi_sources = {}
            event_sources = set()
            changed_states = {}
            old_states: dict = {}
            for name, source in proc.static_context._sources.items():
                declaration = proc.declaration_procedure
                port = next(port for port in declaration.depends.dependencies
                               if port.key == name)
                if isinstance(source, model.StateRead):
                    state_path, state = model.resolve_variable(path, source.state)
                    variable = state_map[state_path, state]
                    if source.phase == 'old':
                        old_states.setdefault(variable, name)
                    elif (isinstance(port, model.ast.ReferencePort)
                          and port.reference.length == 2
                          and port.reference.identifiers[0].id_str in instances[path].node_type.state_nodes
                          and port.reference.identifiers[1].id_str == 'new'):
                        changed_states[name] = variable
                if model.ast.dependency_is_resource(source):
                    raise model.ProgramError(port, 'Procedures cannot access resources')
                spec = getattr(source, 'port_spec', None)
                if spec is not None and spec.kind_spec.kind == model.ConnectionKind.Event:
                    event_sources.add(name)
                if getattr(source, 'is_list', False):
                    if hasattr(source, 'var_type'):
                        value_type = resolve_type(source.var_type, instances[path].static_values)
                    else:
                        assert spec is not None
                        value_type = instances[path].node_type.resolve_type(
                            spec.ty, par_values=instances[path].static_values)
                    if name in event_sources:
                        # Each event source holds None while absent.
                        value_type = model.types.TypeOption(value_type)
                    keys = []
                    for index, (member_path, member, _) in enumerate(members(path, source)):
                        member_key = name, index
                        source_types[member_key] = resolve_source(member_path, member).value_type
                        keys.append(member_key)
                    multi_sources[name] = value_type, tuple(keys)
                else:
                    source_types[name] = resolve_source(path, source).value_type
            output_types = {}
            event_outputs = set()
            for name, target in proc.static_context._targets.items():
                spec = getattr(target, 'port_spec', None)
                port = next(port for port in proc.declaration_procedure.produces.outputs
                               if port.key == name)
                if isinstance(spec, model.PortTypeSpecificationResource):
                    raise model.ProgramError(port, 'Procedures cannot produce resources')
                if spec is not None and spec.kind_spec.kind == model.ConnectionKind.Event:
                    event_outputs.add(name)
                output_variable = (state_map[path, target] if isinstance(target, model.InstanceStateVariable)
                                   else variable_map[path, target.var_identifier].variable)
                assert output_variable is not None
                output_types[name] = output_variable.var_type
                if (isinstance(target, model.InstanceStateVariable)
                        and isinstance(port, model.ast.ReferencePort)
                        and port.reference.length == 1
                        and port.reference.identifiers[0].id_str in instances[path].node_type.state_nodes):
                    changed_states[name] = output_variable
            with warnings.instance(instances[path].instantiation_hint()):
                bodies[path, proc], old_state_dependencies[path, proc] = compile_procedure(
                    proc, instances[path], source_types, output_types, intern_constant, intern_function,
                    multi_sources, event_sources, event_outputs, changed_states, old_states,
                )

    from .static_bindings import evaluate_bindings
    schedules: dict[RunStage, tuple[Procedure, ...]] = {}
    for stage, stage_procs in procs.items():
        schedule = []
        for path, proc in stage_procs:
            dependencies: list[tuple[str, InputSource]] = []
            resources = {}
            outputs = {}
            declaration = proc.declaration_procedure
            input_ports = {port.key: port for port in declaration.depends.dependencies}
            output_keys = {port.key: port.python_key for port in declaration.produces.outputs}
            for name, source in proc.static_context._sources.items():
                port = input_ports[name]
                key = port.python_key
                if model.ast.dependency_is_resource(source):
                    _, resources[key], _ = resource_path(path, source, port)
                    continue
                is_list = getattr(source, 'is_list', False)
                for index, (member_path, member, _) in enumerate(members(path, source)):
                    debug_name = f'{key}[{index}]' if is_list else key
                    dependencies.append((debug_name, resolve_source(member_path, member)))
            for old_state in old_state_dependencies.get((path, proc), ()):
                dependencies.append((f'<changed old {old_state.name}>', InputSource(old_state, new_state=False)))
            for name, target in proc.static_context._targets.items():
                key = output_keys[name]
                if isinstance(target, model.InstanceStateVariable):
                    outputs[key] = OutputTarget(state_map[path, target])
                else:
                    output_variable = variable_map[path, target.var_identifier].variable
                    assert output_variable is not None
                    outputs[key] = OutputTarget(output_variable)
            schedule.append(Procedure(
                node_id=node_ids[path],
                reference=(declaration.native_name if declaration.is_native
                           else f'{instances[path].node_type.qualified_name}:{declaration.name.id_str}'),
                node_context=native_instances[node_ids[path]],
                dependencies=tuple(dependencies), resources=resources,
                outputs=outputs, body=bodies.get((path, proc)),
                values=evaluate_bindings(proc.declaration_procedure.uses, instances[path]),
            ))
        schedules[stage] = tuple(schedule)

    # Copied states store their sources once every procedure has run.
    # Readers of their new value read the source directly.
    copies = []
    for path, instance in instances.items():
        node = instance.node or instance.node_type.singleton_instance
        for state in node.state_variables:
            if state.copy_source is None:
                continue
            copies.append(Procedure(
                node_id=node_ids[path],
                reference=f'{instance.node_type.qualified_name}:<state {state.name}>',
                node_context=native_instances[node_ids[path]],
                dependencies=(('value', resolve_source(path, state.copy_source)),),
                resources={}, outputs={'value': OutputTarget(state_map[path, state])},
                body=COPY_BODY,
            ))
    schedules[RunStage.OUTPUT] += tuple(copies)
    node_aliases: dict[str, str] = {}
    variables: dict[str, InputSource] = {}

    def aliased_items(items: Iterable[Any]) -> list[Any]:
        # Default outputs are aliased by their node, other outputs by their own name.
        return [item for item in items
                if item.name != '' and getattr(item, 'port', None) is None]

    def add_aliases(scope: model.NodeType, path: NodePath = (), prefix: str = '') -> None:
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
    native_types: dict[str, NativeNodeType] = {}
    native_procedures: dict[str, NativeProcedureInterface] = {}
    native_declarations: dict[str, model.DeclarationProcedure] = {}
    for instance in instances.values():
        node_type = instance.node_type
        if node_type.is_native:
            assert node_type.native_name is not None
            native_types[node_type.native_name] = native_node_type(node_type)
        for procedure in node_type.procedures.values():
            if not procedure.is_native:
                continue
            name = procedure.native_name
            interface = native_procedure_interface(procedure)
            previous = native_declarations.setdefault(name, procedure)
            if previous is not procedure and native_procedures[name] != interface:
                raise model.ProgramError(procedure, f'Native procedure {name!r} is declared with different interfaces',
                                         hints=[model.CompilerHint(previous, 'Previous declaration was here')])
            native_procedures[name] = interface
    return Program(
        name=str(graph), graph=graph,
        temporaries=VariableTable(all_variables, f'{graph}.temporaries'),
        state_variables=VariableTable(state_variables, f'{graph}.state_variables'),
        native_construction_order=native_construction_order,
        instances=native_instances, initial_state=initial_state, procs=schedules,
        node_aliases=node_aliases, variables=variables, singleton_aliases=singleton_aliases,
        constants=tuple(constants),
        functions=tuple(functions),
        native_types=native_types, native_procedures=native_procedures,
    )


def uses_names(uses: model.UsesDeclaration | None) -> tuple[str, ...]:
    return () if uses is None else tuple(binding.key for binding in uses.bindings)


def native_procedure_interface(procedure: model.DeclarationProcedure) -> NativeProcedureInterface:
    """Return the declared interface of a native procedure."""
    return NativeProcedureInterface(
        uses=uses_names(procedure.uses),
        resources={port.declaration.python_key: port.resource_type.native_name
                   for port in procedure.input_ports.values() if port.resource_type is not None},
        inputs=tuple(port.declaration.python_key for port in procedure.input_ports.values()
                     if port.resource_type is None),
        outputs=tuple(port.declaration.python_key for port in procedure.output_ports.values()),
    )


def native_node_type(node_type: model.NodeType) -> NativeNodeType:
    """Return the declared native interface of a node type with native state."""
    initializer = node_type.initializer
    initializer_resources = {}
    resource_outputs = {}
    if initializer is not None:
        for port, dependency in zip(initializer.depends.dependencies,
                                    initializer.depends.get_resolved_dependencies()):
            if isinstance(dependency, model.InstanceNode):
                dependency = dependency.output_ports_byname['']
            initializer_resources[port.key] = dependency.resource_type.native_name
        for output, key in initializer.output_keys.items():
            assert isinstance(output, (model.TypeOutputPort, model.LocalResource))
            assert output.resource_type is not None
            resource_outputs[key] = output.resource_type.native_name
    assert node_type.native_name is not None
    return NativeNodeType(
        name=node_type.native_name,
        initializer_uses=uses_names(initializer.uses) if initializer is not None else (),
        initializer_resources=initializer_resources, resource_outputs=resource_outputs,
    )
