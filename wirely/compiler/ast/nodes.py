"""Instances, connections, state, and bindings."""

import dataclasses
import typing
from collections.abc import Iterable
from typing import Any

from wirely.compiler.attributes import copy_attributes
from wirely.compiler.stages import ResolutionState
from wirely.compiler import model
from wirely.compiler.types import resolve_type, check_runtime_type, type_parameters

from .base import SourcePosition, SyntaxElement, CompilerHint, ProgramError, Identifier, Declaration, Value
from .specifiers import PortDirection, PortTypeSpecification, PortTypeSpecificationFixed
from .values import Reference


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
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


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class Instantiation(SyntaxElement):
    type_name: Reference
    parameters: tuple[ParameterBinding, ...]

    @staticmethod
    def make(type_name: Reference, parameters: list[ParameterBinding], *,
             source_position: SourcePosition) -> 'Instantiation':
        return Instantiation(type_name, tuple(parameters), source_position=source_position,
                             inner_attributes=getattr(parameters, 'inner_attributes', ()))


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationInstance(Declaration):
    name: Identifier
    instantiation: Instantiation
    def __post_init__(self) -> None:
        object.__setattr__(self, 'inner_attributes', self.instantiation.inner_attributes)


    def collect_declarations(self, graph: 'model.NodeType') -> None:
        name = self.name.id_str
        if name in graph.node_declarations:
            raise ProgramError(self.name, f'Duplicate node name `{name}`', hints=[
                CompilerHint(graph.node_declarations[name].declaration, 'Previous node was here'),
            ])
        register_name(graph, self.name, self)
        graph.node_declarations[name] = ChildNode(graph, self)


class ChildNode:
    """A node declared in a node type body, with its type and arguments resolved in a compilation."""

    node_type: 'model.NodeType'  # Set by resolve_arguments().

    def __init__(self, graph: 'model.NodeType', declaration: DeclarationInstance) -> None:
        self.graph = graph
        self.declaration = declaration
        self.par_values: dict['model.NodeParameter', Any] = {}
        # Parameters whose bindings failed, when the coordinator continues after errors.
        self.failed_parameters: set['model.NodeParameter'] = set()

    @property
    def name(self) -> str:
        return self.declaration.name.id_str

    def resolve_arguments(self) -> None:
        graph = self.graph
        inst = self.declaration.instantiation
        parameter_bindings = inst.parameters

        self.node_type = graph.resolve_node_type(inst.type_name)

        # The node type may be declared after this node.
        if self.node_type.interface_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Instance arguments require a prepared child interface')

        coordinator = graph.coordinator
        seen: set['model.NodeParameter'] = set()
        for parameter in parameter_bindings:
            key = parameter.name
            value = parameter.value
            par = self.node_type.parsed_parameters.get(key.id_str)
            with coordinator.contain(parameter):
                if key.id_str in self.node_type.parsed_constants:
                    raise ProgramError(key, f'Cannot override constant `{key.id_str}`')
                if par is None:
                    raise ProgramError(key, f'Unknown parameter for `{self.node_type.name}`: `{key}`')
                if par in seen:
                    raise ProgramError(parameter, f'Duplicate parameter binding `{key.id_str}`')
                seen.add(par)
                if coordinator.has_failed(par.declaration):
                    # The parameter's own error was already reported.
                    coordinator.failed.add(parameter)
                    continue
                if parameter.is_optional:
                    if not par.is_optional:
                        raise ProgramError(parameter, 'Optional binding requires an optional or defaulted parameter')
                    from ..expressions import compile_constant
                    compile_constant(graph, value, model.types.TypeOption(par.type_resolved), {})
                    self.par_values[par] = model.value.OptionalBinding(graph, value, par)
            if coordinator.has_failed(parameter) and par is not None:
                self.failed_parameters.add(par)

        pending = {self.node_type.parsed_parameters[parameter.name.id_str]: parameter
                   for parameter in parameter_bindings
                   if not parameter.is_optional and not coordinator.has_failed(parameter)}

        def bind(par: 'model.NodeParameter') -> None:
            parameter = pending.pop(par, None)
            if parameter is None:
                return
            # Bind the parameters this parameter's type refers to first.
            for dependency in type_parameters(par.type_resolved):
                bind(dependency)
            value = parameter.value
            with coordinator.contain(parameter):
                par_type = resolve_type(par.type_resolved, self.par_values)
                # TODO: Is passing par_values=self.par_values here the correct thing to do?
                if par_type == model.types.ValueType:
                    val = graph.get_const_value(value, par_type, par_values=self.par_values)
                    self.par_values[par] = (val.value if isinstance(val, model.ConstantValue) else val)
                else:
                    val = graph.get_const_value(value, par_type, par_values=self.par_values)
                    self.par_values[par] = val
            if coordinator.has_failed(parameter):
                self.failed_parameters.add(par)

        for par in list(pending):
            bind(par)

    def create_instance_interface(self) -> None:
        self.node_type.create_instance_interface(
            graph=self.graph, par_values=self.par_values, name=self.name, instantiation=self.declaration,
            failed_parameters=self.failed_parameters,
        )


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationConnection(Declaration):
    sources: tuple[Reference, ...]
    target: Reference

    def bind_body(self, graph: 'model.NodeType') -> None:
        sources = self.sources
        target = self.target

        # print(source_node_name, source_output_name, target_node_name, target_input_name)
        res_sources = [source.resolve(graph, PortDirection.Output, 'source') for source in sources]

        res_target = target.resolve(graph, PortDirection.Input, 'target')

        # TODO: Disallow multiple connect statements to a list input?
        with self.context:
            edge_attributes = graph.coordinator.metadata.edge_attributes[self]
            for source, attributes in zip(res_sources, edge_attributes):
                model.connect(source.port, res_target.port, self, attributes=attributes)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationState(Declaration):
    name: Identifier
    var_type: Value
    initial_value: Value

    @property
    def name_str(self) -> str:
        return self.name.id_str

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        name = self.name.id_str
        if name in graph.value_nodes:
            hints = [
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ]
            raise ProgramError(self, f'Duplicate name `{name}`', hints=hints)
        register_name(graph, self.name, self)
        graph.value_nodes[name] = self

    def prepare_interface(self, new_graph: 'model.NodeType') -> None:
        ty = new_graph.resolve_type(self.var_type)
        check_runtime_type(ty, self)
        # TODO: Resolve initial_value
        variable = model.StateNode(new_graph, ty, name=self.name_str, initial_value=self.initial_value)
        copy_attributes(variable, self, new_graph.coordinator.attributes(self))


def validate_nodes(graph: 'model.NodeType') -> None:
    """Require a source for each procedure input port, variable, and native resource output."""
    for procedure in graph.procedures.values():
        procedure.validate_ports()
    for name, variable in graph.value_nodes.items():
        is_state = isinstance(variable, DeclarationState)
        storage = (graph.state_nodes if is_state else graph.temporary_nodes)[name]
        if storage.connected_source is None:
            raise ProgramError(variable, f'{"State" if is_state else "Binding"} `{name}` has no source')
    for resource in graph.local_resources.values():
        if resource.connected_source is None:
            raise ProgramError(resource.declaration, f'Binding `{resource.name}` has no source')
    for node in (*graph.state_nodes.values(), *graph.temporary_nodes.values(), *graph.local_resources.values()):
        check_alias_cycle(graph, node)
    if graph.initializers:
        validate_resource_outputs(graph)
    check_unused(graph)


def check_unused(graph: 'model.NodeType') -> None:
    """Warn about bindings of the node type that nothing reads, and about an unused native name."""
    read = {id(connection.source) for connection in graph.connections}
    warnings = graph.coordinator.warnings
    assert warnings is not None
    alias = getattr(graph, 'alias', None)
    if alias is not None:
        # The alias is the prefix of the native names of members without their own.
        used = any(procedure.is_native and procedure.alias is None for procedure in graph.procedures.values())
        if not used:
            warnings.warn('Unused', alias, f'Native name `{alias.value}` is not used by an initializer or native procedure',
                          instance_trace=False)
    for name, node in graph.temporary_nodes.items():
        if id(node) not in read:
            warnings.warn('Unused', graph.value_nodes[name].name, f'Binding `{name}` is never read',
                          instance_trace=False)
    for name, resource in graph.local_resources.items():
        if id(resource) not in read:
            warnings.warn('Unused', resource.declaration.name, f'Binding `{name}` is never read',
                          instance_trace=False)


def validate_resource_outputs(graph: 'model.NodeType') -> None:
    """Require one source for each fixed output of a node type with an initializer."""
    for output in graph.output_ports.values():
        if not isinstance(output.port_spec, model.PortTypeSpecificationFixed):
            continue
        name = 'Default output' if output.output_name == '' else f'Output `{output.output_name}`'
        if output.connected_source is None:
            raise ProgramError(output.port_spec, f'{name} has no source')


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
        raise ProgramError(name, f'Duplicate name `{name.id_str}`', hints=[
            CompilerHint(previous, 'Previous declaration was here'),
        ])
    graph.name_declarations[name.id_str] = declaration


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationBinding(Declaration):
    """A named value, event, or resource connection with exactly one source."""

    name: Identifier
    port_spec: PortTypeSpecification

    @property
    def name_str(self) -> str:
        return self.name.id_str

    @property
    def var_type(self) -> Value:
        return self.port_spec.ty
    def __post_init__(self) -> None:
        if self.port_spec.list_spec.is_list:
            raise ProgramError(self.port_spec, 'Bindings cannot be multi')


    @property
    def is_resource(self) -> bool:
        return isinstance(self.port_spec, PortTypeSpecificationFixed)

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        name = self.name_str
        if not self.is_resource and name in graph.value_nodes:
            raise ProgramError(self, f'Duplicate name `{name}`', hints=[
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ])
        register_name(graph, self.name, self)
        if not self.is_resource:
            graph.value_nodes[name] = self

    def prepare_interface(self, new_graph: 'model.NodeType') -> None:
        variable: model.LocalResource | model.TemporaryNode
        if self.is_resource:
            resource_type = new_graph.resolve_type(self.port_spec.ty)
            if not isinstance(resource_type, model.ResourceType):
                check_runtime_type(resource_type, self)
            variable = model.LocalResource(new_graph, self, resource_type)
        else:
            ty = new_graph.resolve_type(self.var_type)
            check_runtime_type(ty, self)
            variable = model.TemporaryNode(new_graph, ty, name=self.name_str, port_spec=self.port_spec)
        copy_attributes(variable, self, new_graph.coordinator.attributes(self))
