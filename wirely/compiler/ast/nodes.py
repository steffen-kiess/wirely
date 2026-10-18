"""Instances, connections, state, and bindings."""

import dataclasses
import typing
from collections.abc import Iterable
from typing import Any

from wirely.compiler.attributes import copy_attributes
from wirely.compiler.stages import ResolutionState
from wirely.compiler import model
from wirely.util import check_parameters
from wirely.compiler.types import resolve_type, check_runtime_type, type_parameters

from .base import SourcePosition, SyntaxElement, CompilerHint, ProgramError, Identifier, Declaration, Value, syntax
from .specifiers import PortDirection, PortTypeSpecification, PortTypeSpecificationResource
from .values import Reference


@syntax
class ParameterBinding(SyntaxElement):
    name: Identifier
    value: Value
    _: dataclasses.KW_ONLY
    is_optional: bool = False

    @staticmethod
    def optional(name: Identifier, value: Value, *, source_position: SourcePosition) -> 'ParameterBinding':
        return ParameterBinding(name, value, source_position=source_position, is_optional=True)

    def __repr__(self) -> str:
        return f'Parameter <{self.name}> = <{self.value}>'


        # return f'Parameter <{self.name}> [{self.name.source_position.start_line}] = <{self.value}>'


@syntax
class Instantiation(SyntaxElement):
    type_name: Reference
    parameters: tuple[ParameterBinding, ...]

    @staticmethod
    def make(type_name: Reference, parameters: list[ParameterBinding], *,
             source_position: SourcePosition) -> 'Instantiation':
        return Instantiation(type_name, tuple(parameters), source_position=source_position,
                             inner_attributes=getattr(parameters, 'inner_attributes', ()))


class DeclarationInstance(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, instantiation: Instantiation, *, source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.instantiation = instantiation
        self.inner_attributes = instantiation.inner_attributes

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        name = self.name.id_str
        if name in graph.node_declarations:
            raise ProgramError(self.name, f'Duplicate node name {name!r}', hints=[
                CompilerHint(graph.node_declarations[name], 'Previous node was here'),
            ])
        register_name(graph, self.name, self)
        graph.node_declarations[name] = self

    def resolve_instance_arguments(self, graph: 'model.NodeType') -> None:
        name = self.name
        inst = self.instantiation

        self.node_name = name.id_str
        parameter_bindings = inst.parameters

        self.node_type = graph.resolve_node_type(inst.type_name)
        # print(self.node_name, self.node_type, parameter_bindings)

        # The node type may be declared after this node.
        if self.node_type.interface_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Instance arguments require a prepared child interface')

        self.par_values: dict['model.DeclarationParameter', Any] = {}
        # Parameters whose bindings failed, when the coordinator continues after errors.
        self.failed_parameters: set['model.DeclarationParameter'] = set()
        coordinator = graph.coordinator
        seen: set['model.DeclarationParameter'] = set()
        failed_bindings: set[ParameterBinding] = set()
        for parameter in parameter_bindings:
            key = parameter.name
            value = parameter.value
            par = self.node_type.parsed_parameters.get(key.id_str)
            with coordinator.contain(parameter, failures=failed_bindings):
                if key.id_str in self.node_type.parsed_constants:
                    raise ProgramError(key, f'Cannot override constant {key.id_str!r}')
                if par is None:
                    raise ProgramError(key, f'Unknown parameter for {self.node_type.name}: {key}')
                if par in seen:
                    raise ProgramError(parameter, f'Duplicate parameter binding {key.id_str!r}')
                seen.add(par)
                if getattr(par, 'failed', False):
                    # The parameter's own error was already reported.
                    failed_bindings.add(parameter)
                    continue
                if parameter.is_optional:
                    if not par.is_optional:
                        raise ProgramError(parameter, 'Optional binding requires an optional or defaulted parameter')
                    from ..expressions import compile_constant
                    compile_constant(graph, value, model.types.TypeOption(par.type_resolved), {})
                    self.par_values[par] = model.value.OptionalBinding(graph, value, par)
            if parameter in failed_bindings and par is not None:
                self.failed_parameters.add(par)

        pending = {self.node_type.parsed_parameters[parameter.name.id_str]: parameter
                   for parameter in parameter_bindings
                   if not parameter.is_optional and parameter not in failed_bindings}

        def bind(par: 'model.DeclarationParameter') -> None:
            parameter = pending.pop(par, None)
            if parameter is None:
                return
            # Bind the parameters this parameter's type refers to first.
            for dependency in type_parameters(par.type_resolved):
                bind(dependency)
            value = parameter.value
            with coordinator.contain(parameter, failures=failed_bindings):
                par_type = resolve_type(par.type_resolved, self.par_values)
                # TODO: Is passing par_values=self.par_values here the correct thing to do?
                if par_type == model.types.ValueType:
                    val = graph.get_const_value(value, par_type, par_values=self.par_values)
                    self.par_values[par] = (val.value if isinstance(val, model.ConstantValue) else val)
                else:
                    val = graph.get_const_value(value, par_type, par_values=self.par_values)
                    self.par_values[par] = val
            if parameter in failed_bindings:
                self.failed_parameters.add(par)

        for par in list(pending):
            bind(par)

    def create_instance_interface(self, graph: 'model.NodeType') -> None:
        self.node_type.create_instance_interface(
            graph=graph, par_values=self.par_values, name=self.node_name, instantiation=self,
        )


class DeclarationConnection(Declaration):
    edge_attributes: tuple  # Validated attributes of each edge, set by the attribute pass.

    @check_parameters
    def __init__(self, sources: typing.List[Reference], target: Reference, *, source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.sources = sources
        self.target = target

    def bind_body(self, graph: 'model.NodeType') -> None:
        sources = self.sources
        target = self.target

        # print(source_node_name, source_output_name, target_node_name, target_input_name)
        res_sources = [source.resolve(graph, PortDirection.Output, 'source') for source in sources]

        res_target = target.resolve(graph, PortDirection.Input, 'target')

        # TODO: Disallow multiple connect statements to a list input?
        with self.context:
            for source, attributes in zip(res_sources, self.edge_attributes):
                model.connect(source.port, res_target.port, self, attributes=attributes)


class DeclarationState(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, var_type: Value, initial_value: Value, *, source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.var_type = var_type
        self.initial_value = initial_value

        self.name_str = self.name.id_str

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        name = self.name.id_str
        if name in graph.value_nodes:
            hints = [
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ]
            raise ProgramError(self, f'Duplicate name {name!r}', hints=hints)
        register_name(graph, self.name, self)
        graph.value_nodes[name] = self

    def register_interface_declarations(self, interface: 'model.NodeType') -> None:
        self.interface = interface

    def prepare_interface(self, new_graph: 'model.NodeType') -> None:
        ty = self.interface.resolve_type(self.var_type)
        check_runtime_type(ty, self)
        # TODO: Resolve initial_value
        variable = model.StateNode(new_graph, ty, name=self.name_str, initial_value=self.initial_value)
        copy_attributes(variable, self)


def validate_nodes(graph: 'model.NodeType') -> None:
    """Require a source for each procedure input port, variable, and native resource output."""
    for procedure in graph.procedures.values():
        procedure.validate_ports()
    for name, variable in graph.value_nodes.items():
        is_state = isinstance(variable, DeclarationState)
        storage = (graph.state_nodes if is_state else graph.temporary_nodes)[name]
        if storage.connected_source is None:
            raise ProgramError(variable, f'{"State" if is_state else "Binding"} {name!r} has no source')
    initializer = getattr(graph, 'initializer', None)
    produced = initializer.output_keys if initializer is not None else {}
    for resource in graph.local_resources.values():
        if resource.connected_source is None and resource not in produced:
            raise ProgramError(resource.declaration, f'Binding {resource.name!r} has no source')
        if resource.connected_source is not None and resource in produced:
            connection = next(connection.source_element for connection in graph.connections
                              if connection.target is resource)
            assert initializer is not None
            port = next(port for port in initializer.produces.outputs if port.key == produced[resource])
            raise ProgramError(connection, f'Binding {resource.name!r} is both produced and connected', hints=[
                CompilerHint(port, 'Produced by the native initializer here'),
            ])
    for node in (*graph.state_nodes.values(), *graph.temporary_nodes.values(), *graph.local_resources.values()):
        check_alias_cycle(graph, node)
    if getattr(graph, 'is_native', False):
        validate_resource_outputs(graph)
    check_unused(graph)


def check_unused(graph: 'model.NodeType') -> None:
    """Warn about bindings of the node type that nothing reads."""
    read = {id(connection.source) for connection in graph.connections}
    initializer = getattr(graph, 'initializer', None)
    if initializer is not None:
        read.update(id(dependency) for dependency in initializer.depends.get_resolved_dependencies())
    warnings = graph.coordinator.warnings
    assert warnings is not None
    for name, node in graph.temporary_nodes.items():
        if id(node) not in read:
            warnings.warn('Unused', graph.value_nodes[name].name, f'Binding {name!r} is never read',
                          instance_trace=False)
    for name, resource in graph.local_resources.items():
        if id(resource) not in read:
            warnings.warn('Unused', resource.declaration.name, f'Binding {name!r} is never read',
                          instance_trace=False)


def validate_resource_outputs(graph: 'model.NodeType') -> None:
    """Require one source for each resource output of a native node type."""
    if graph.initializer is None:
        raise ProgramError(graph.name, 'Native node types require a native initializer')
    produced = graph.initializer.output_keys
    for output in graph.output_ports.values():
        if output.resource_type is None:
            continue
        name = 'Default output' if output.output_name == '' else f'Output {output.output_name!r}'
        if output.connected_source is None and output not in produced:
            raise ProgramError(output.port_spec, f'{name} has no source')
        if output.connected_source is not None and output in produced:
            connection = next(connection.source_element for connection in graph.connections
                              if connection.target is output)
            port = next(port for port in graph.initializer.produces.outputs if port.key == produced[output])
            raise ProgramError(connection, f'{name} is both produced and connected', hints=[
                CompilerHint(port, 'Produced by the native initializer here'),
            ])


def passed_on_variable(source: object) -> Any:
    """Return the temporary, resource, or copied state whose value source passes on, if any."""
    if isinstance(source, (model.TemporaryNode, model.LocalResource)):
        return source
    if isinstance(source, model.StateRead) and source.phase == 'new' and source.state.copy_source is not None:
        return source.state
    return None


def check_alias_cycle(graph: 'model.NodeType', start: Any) -> None:
    """Reject a variable whose value is passed back to it without a procedure."""
    node = start
    seen = []
    while node is not None and node not in seen:
        seen.append(node)
        node = passed_on_variable(node.copy_source if isinstance(node, model.StateNode) else node.connected_source)
        if node is start:
            connections = [next(connection for connection in graph.connections if connection.target is node)
                           for node in seen]
            raise circular_connection(connections[0], reversed(connections[1:]))


def circular_connection(connection: 'model.Connection', others: Iterable['model.Connection']) -> ProgramError:
    """Return the error for a cycle reported at connection, with the others in data-flow order."""
    return ProgramError(connection.source_element, 'Circular connection', hints=[
        CompilerHint(other.source_element, 'Cycle continues here') for other in others
    ])


def register_name(graph: 'model.NodeType', name: Identifier, declaration: Declaration) -> None:
    """Give nodes and named ports one namespace."""
    previous = graph.name_declarations.get(name.id_str)
    if previous is not None:
        raise ProgramError(name, f'Duplicate name {name.id_str!r}', hints=[
            CompilerHint(previous, 'Previous declaration was here'),
        ])
    graph.name_declarations[name.id_str] = declaration


class DeclarationBinding(Declaration):
    """A named value, event, or resource connection with exactly one source."""

    @check_parameters
    def __init__(self, name: Identifier, port_spec: PortTypeSpecification, *, source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.name_str = name.id_str
        self.port_spec = port_spec
        if port_spec.list_spec.is_list:
            raise ProgramError(port_spec, 'Bindings cannot be multi')
        self.var_type = port_spec.ty

    @property
    def is_resource(self) -> bool:
        return isinstance(self.port_spec, PortTypeSpecificationResource)

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        name = self.name_str
        if not self.is_resource and name in graph.value_nodes:
            raise ProgramError(self, f'Duplicate name {name!r}', hints=[
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ])
        register_name(graph, self.name, self)
        if not self.is_resource:
            graph.value_nodes[name] = self

    def register_interface_declarations(self, interface: 'model.NodeType') -> None:
        self.interface = interface

    def prepare_interface(self, new_graph: 'model.NodeType') -> None:
        variable: model.LocalResource | model.TemporaryNode
        if self.is_resource:
            resource_type = self.interface.resolve_resource_type(self.port_spec.ty)
            variable = model.LocalResource(new_graph, self, resource_type)
        else:
            ty = self.interface.resolve_type(self.var_type)
            check_runtime_type(ty, self)
            variable = model.TemporaryNode(new_graph, ty, name=self.name_str, port_spec=self.port_spec)
        copy_attributes(variable, self)
