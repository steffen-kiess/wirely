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
from wirely.program.names import member_name
from .expressions import compile_constant, compile_procedure
from .stages import ResolutionState
from wirely.program import (
    Constant, Function, InputSource, Instruction, NativeProcedureEntry, Opcode, Operand, OperandKind,
    OutputTarget, Procedure, ProcedureBody, Program, ProgramSymbols,
    RunStage, Variable, VariableBank, VariableTable,
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
                                               instantiation=graph.declaration, register=False)
        graph.populate_instance(root)

    variable_map: dict[tuple[NodePath, str], InputSource] = {}
    all_variables: list[Variable] = []
    fixed_variables: list[Variable] = []
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
        parameters: dict[model.NodeParameter, StaticValue] = {}
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
        if not isinstance(var_type, model.ResourceType):
            check_runtime_type(var_type, source)
        name = model.InstanceNode.path_name(path)
        # Port storage is named after its procedure: <path>.<procedure>.<port>.
        variable_name = member_name(name, var.name)
        v = Variable(var_type, name=variable_name)
        fixed = isinstance(var_type, model.ResourceType) or model.connection_kind(var) == model.ConnectionKind.Fixed
        input_source = InputSource(VariableBank.FIXED_VALUES if fixed else VariableBank.TEMPORARIES, v)
        (fixed_variables if fixed else all_variables).append(v)
        variable_map[path, var.var_identifier] = input_source
        return input_source

    specialize_instances(graph, root, variable_map=variable_map, all_variables=all_variables,
                         state_map=state_map, state_variables=state_variables, instances=instances)

    constants: dict[tuple[model.Type, object], Constant] = {}  # By type and serialized or const-only value.

    def intern_constant(value_type: model.Type, value: object) -> Constant:
        """Return a shared Constant for equal typed values."""
        value = value_type.verify_value(value)
        key: tuple[model.Type, object]
        if value_type.is_runtime_type:
            key = value_type, value_type.serialize(value)
        else:
            # Const-only values are compared directly; unhashable ones are not shared.
            try:
                hash(value)
            except TypeError:
                return Constant(value_type, value)
            key = value_type, value
        if key not in constants:
            constants[key] = Constant(value_type, value)
        return constants[key]

    def static_source(path: NodePath, source: model.StaticSource) -> InputSource:
        """Return the constant supplying a parameter or constant connected as a source."""
        instance = instances[path]
        reference = source.reference
        assert isinstance(reference, model.Identifier)
        assert source.var_type is not None
        with reference.context, warnings.instance(instance.instantiation_hint()):
            try:
                value_type = resolve_type(source.var_type, instance.static_values)
                expression = model.Reference((reference,), source_position=reference.source_position)
                value = compile_constant(instance.node_type, expression, value_type, instance.static_values)
                try:
                    concrete = value.resolve_self(instance.static_values).evaluate()
                except (ValueError, ArithmeticError) as error:
                    raise model.ProgramError(reference, str(error)) from error
            except model.ProgramError as error:
                hint = instance.instantiation_hint()
                if hint is not None:
                    error.hints = (*error.hints, hint)
                raise
        return InputSource(VariableBank.CONST_VALUES, constant=intern_constant(value_type, concrete))

    def resolve_source(path: NodePath, source: Any) -> InputSource:
        source_path, variable = model.resolve_variable(path, source)
        if isinstance(variable, model.StaticSource):
            return static_source(source_path, variable)
        if isinstance(variable, model.StateRead):
            bank = VariableBank.NEW_STATE if variable.phase == 'new' else VariableBank.OLD_STATE
            return InputSource(bank, state_map[source_path, variable.state])
        if isinstance(variable, model.InstanceStateVariable):
            return InputSource(VariableBank.OLD_STATE, state_map[source_path, variable])
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
                if isinstance(variable, model.InstanceOutputPort):
                    raise model.ProgramError(proc.declaration_procedure.reads,
                                             'Fixed input is not written by a load procedure', hints=[
                        model.CompilerHint(variable.port_spec, 'Fixed output was declared here'),
                    ])
                if not isinstance(variable, (model.InstanceStateVariable, model.StaticSource)):
                    do(producer_path, variable.producer, proc.declaration_procedure.reads, connections)
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

    functions: dict[str, Function] = {}

    def intern_function(function: Function) -> Function:
        return functions.setdefault(function.name, function)

    for stage_procs in procs.values():
        for path, proc in stage_procs:
            # Native multi value/event specialization is not implemented by the runtime.
            if proc.declaration_procedure.is_native and any(
                    getattr(source, 'is_list', False) and not model.ast.dependency_is_fixed(source)
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
                port = next(port for port in declaration.read_ports.dependencies
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
                if not getattr(source, 'is_list', False) and isinstance(resolve_source(path, source).value_type, model.ResourceType):
                    raise model.ProgramError(port, 'Procedure bodies cannot read resources')
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
                port = next(port for port in proc.declaration_procedure.writes.outputs
                               if port.key == name)
                if isinstance(resolve_source(path, target).value_type, model.ResourceType):
                    raise model.ProgramError(port, 'Procedure bodies cannot write resources')
                if spec is not None and spec.kind_spec.kind == model.ConnectionKind.Event:
                    event_outputs.add(name)
                output_variable = (state_map[path, target] if isinstance(target, model.InstanceStateVariable)
                                   else variable_map[path, target.var_identifier].variable)
                assert isinstance(output_variable, Variable)
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

    native_entries: dict[str, NativeProcedureEntry] = {}
    native_declarations: dict[str, model.TypeProcedure] = {}
    for instance in instances.values():
        node_type = instance.node_type
        for procedure in node_type.procedures.values():
            if not procedure.is_native:
                continue
            name = procedure.native_name
            entry = native_procedure_entry(procedure)
            previous = native_declarations.setdefault(name, procedure)
            if previous is not procedure and native_entries[name] != entry:
                raise model.ProgramError(procedure.declaration,
                                         f'Native procedure `{name}` is declared with different interfaces',
                                         hints=[model.CompilerHint(previous.declaration, 'Previous declaration was here')])
            native_entries[name] = entry

    from .static_bindings import evaluate_bindings
    schedules: dict[RunStage, tuple[Procedure, ...]] = {}
    for stage, stage_procs in procs.items():
        schedule = []
        for path, proc in stage_procs:
            inputs: list[tuple[str, InputSource]] = []
            outputs: list[tuple[str, OutputTarget]] = []
            declaration = proc.declaration_procedure
            input_ports = {port.key: port for port in declaration.read_ports.dependencies}
            output_keys = {port.key: port.python_key for port in declaration.writes.outputs}
            for name, source in proc.static_context._sources.items():
                port = input_ports[name]
                key = port.python_key
                if declaration.is_native and getattr(source, 'is_list', False):
                    raise model.ProgramError(port, 'Native runtime does not support multi value or event dependencies')
                for member_path, member, _ in members(path, source):
                    inputs.append((key, resolve_source(member_path, member)))
            for old_state in old_state_dependencies.get((path, proc), ()):
                inputs.append(('', InputSource(VariableBank.OLD_STATE, old_state)))
            for name, target in proc.static_context._targets.items():
                key = output_keys[name]
                if isinstance(target, model.InstanceStateVariable):
                    outputs.append((key, OutputTarget(VariableBank.NEW_STATE, state_map[path, target])))
                else:
                    target_source = resolve_source(path, target)
                    assert target_source.variable is not None
                    outputs.append((key, OutputTarget(target_source.bank, target_source.variable)))
            native = None
            if declaration.is_native:
                native = native_entries[instances[path].node_type.procedures[declaration.name.id_str].native_name]
                for key, constant in evaluate_bindings(declaration.static_reads, instances[path],
                                                       intern_constant).items():
                    inputs.append((key, InputSource(VariableBank.CONST_VALUES, constant=constant)))
                named_inputs, named_outputs = dict(inputs), dict(outputs)
                assert sorted(named_inputs) == sorted(native.input_names), (named_inputs, native)
                assert sorted(named_outputs) == sorted(native.output_names), (named_outputs, native)
                inputs = [(name, named_inputs[name]) for name in native.input_names]
                outputs = [(name, named_outputs[name]) for name in native.output_names]
            schedule.append(Procedure(
                name=member_name(node_ids[path], declaration.name.id_str),
                inputs=tuple(source for _, source in inputs),
                outputs=tuple(target for _, target in outputs),
                native=native, body=bodies.get((path, proc)),
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
                name=member_name(node_ids[path], f'<state {state.name}>'),
                inputs=(resolve_source(path, state.copy_source),),
                outputs=(OutputTarget(VariableBank.NEW_STATE, state_map[path, state]),),
                body=COPY_BODY,
            ))
    schedules[RunStage.OUTPUT] += tuple(copies)
    node_aliases: dict[str, str] = {}
    variables: dict[str, InputSource] = {}

    def aliased_items(items: Iterable[Any]) -> list[Any]:
        # Default outputs are aliased by their node, other outputs by their own name.
        return [item for item in items
                if item.name != '' and getattr(item, 'port', None) is None]

    def add_value_aliases(owner: model.InstanceNode, path: NodePath, alias: str) -> None:
        items = owner.input_ports + owner.output_ports + owner.state_variables + owner.temporaries
        for item in aliased_items(items):
            input_source = variable_map.get((path, item.var_identifier))
            if input_source is not None:
                variables.setdefault(member_name(alias, item.name), input_source)
        output = owner.output_ports_byname.get('')
        if output is not None:
            input_source = variable_map.get((path, output.var_identifier))
            if input_source is not None:
                variables.setdefault(alias, input_source)

    def add_aliases(scope: model.NodeType, path: NodePath = (), prefix: str = '') -> None:
        for name, node in scope.instance_nodes.items():
            child_path = model.path_resolve_singleton(path + (node,))
            alias = member_name(prefix, name)
            node_aliases[alias] = node_ids[child_path]
            owner = node.node_type.singleton_instance if node.node_type.is_singleton else node
            add_value_aliases(owner, child_path, alias)
            add_aliases(node.node_type, child_path, alias)

    root_path = (root,) if root is not None else ()
    add_aliases(graph, root_path)
    if root is not None:
        for item in aliased_items(root.temporaries + root.state_variables):
            variables[item.name] = variable_map[root_path, item.var_identifier]
    for path, instance in instances.items():
        if instance.is_singleton:
            alias = node_ids[path]
            owner = instance.node_type.singleton_instance
            add_value_aliases(owner, path, alias)
            add_aliases(instance.node_type, path, alias)
    singleton_aliases = {
        instance.node_type.fullname: node_ids[path]
        for path, instance in instances.items() if instance.is_singleton
    }
    return Program(
        graph=graph,
        temporaries=VariableTable(all_variables, f'{graph}.temporaries'),
        state_variables=VariableTable(state_variables, f'{graph}.state_variables'),
        fixed_values=VariableTable(fixed_variables, f'{graph}.fixed'),
        initial_state=initial_state, procs=schedules,
        symbols=ProgramSymbols(name=str(graph), node_aliases=node_aliases, singleton_aliases=singleton_aliases,
                               variable_aliases=variables),
    )


def native_procedure_entry(procedure: model.TypeProcedure) -> NativeProcedureEntry:
    """Return the native procedure entry of a native procedure or initializer declaration."""
    static_reads = procedure.static_reads
    return NativeProcedureEntry(
        name=procedure.native_name,
        input_names=(*(() if static_reads is None else (binding.key for binding in static_reads.bindings)),
                     *(port.declaration.python_key for port in procedure.input_ports.values())),
        output_names=tuple(port.declaration.python_key for port in procedure.output_ports.values()),
        initializer=isinstance(procedure, model.TypeInitializer),
    )
