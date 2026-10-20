"""Port declarations of node types."""

import dataclasses
import typing

from wirely.compiler.attributes import copy_attributes
from wirely.compiler import model
from wirely.compiler.types import check_runtime_type

from .base import Value, SourcePosition, CompilerHint, ProgramError, InternalCompilerError, Identifier, Declaration
from .specifiers import ListSpecifier, ConnectionKindSpecifier, ConnectionKind, PortDirection, PortDirectionSpecifier, PortTypeSpecification, PortTypeSpecificationData, PortTypeSpecificationResource


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationPort(Declaration):
    name: Identifier | None
    direction_spec: PortDirectionSpecifier
    port_spec: PortTypeSpecification
    list_spec: ListSpecifier = dataclasses.field(init=False)
    ty: Value = dataclasses.field(init=False)
    kind_spec: ConnectionKindSpecifier = dataclasses.field(init=False)
    kind: ConnectionKind = dataclasses.field(init=False)
    direction: PortDirection = dataclasses.field(init=False)
    name_str: str = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        # TODO: Remove?
        object.__setattr__(self, 'list_spec', self.port_spec.list_spec)
        if isinstance(self.port_spec, PortTypeSpecificationData):
            object.__setattr__(self, 'ty', self.port_spec.ty)
            object.__setattr__(self, 'kind_spec', self.port_spec.kind_spec)

            object.__setattr__(self, 'kind', self.kind_spec.kind)

        object.__setattr__(self, 'direction', self.direction_spec.direction)

        if self.list_spec.is_list and self.direction != PortDirection.Input:
            raise ProgramError(self, f'Got multi specifier for non-input')
        if isinstance(self.port_spec, PortTypeSpecificationResource) and self.direction == PortDirection.Input:
            self.port_spec.check_supported_input()

        if self.name is None:
            if self.direction == PortDirection.Input:
                object.__setattr__(self, 'name_str', 'default input')
            elif self.direction == PortDirection.Output:
                object.__setattr__(self, 'name_str', 'default output')
            else:
                raise Exception(f'Unknown value for direction: {self.direction}')
        else:
            object.__setattr__(self, 'name_str', self.name.id_str)

    @staticmethod
    def create(direction_spec: PortDirectionSpecifier, name: Identifier, port_spec : PortTypeSpecification, *,
               source_position: SourcePosition) -> 'DeclarationPort':
        return DeclarationPort(name, port_spec=port_spec, direction_spec=direction_spec, source_position=source_position)

    @staticmethod
    def create_default(direction_spec: PortDirectionSpecifier, port_spec : PortTypeSpecification, *,
                       source_position: SourcePosition) -> 'DeclarationPort':
        return DeclarationPort(None, port_spec=port_spec, direction_spec=direction_spec, source_position=source_position)

    def register_interface_declarations(self, interface: 'model.NodeType') -> None:
        if interface.is_singleton and self.direction_spec.direction == PortDirection.Input:
            raise model.ProgramError(self, 'Singleton node type cannot have inputs')

    def prepare_interface(self, new_graph: 'model.NodeType') -> None:
        # TODO: Use self.kind ConnectionKind
        # TODO: Pass par_values to resolve_type?
        # TODO: Should the entires port_spec instance be passed?
        if isinstance(self.port_spec, PortTypeSpecificationData):
            ty = new_graph.resolve_type(self.ty)
            check_runtime_type(ty, self)
            resource_type = None
        elif isinstance(self.port_spec, PortTypeSpecificationResource):
            ty = None
            resource_type = new_graph.resolve_resource_type(self.port_spec.ty)
        else:
            raise InternalCompilerError(self, Exception('Unknown port type specification'))
        port: model.TypeInputPort | model.TypeOutputPort
        if self.direction == PortDirection.Input:
            name = self.name_str
            if name == '':
                name = 'default input'
            if name in new_graph.input_ports:
                raise ProgramError(self, f'Duplicate input `{name}`', hints=[
                    CompilerHint(new_graph.input_ports[name].port_spec, 'Previous input was here'),
                ])
            port = model.TypeInputPort(new_graph, ty, name=name, input_name=self.name_str, is_list=self.list_spec.is_list, port_spec=self.port_spec, resource_type=resource_type)
        elif self.direction == PortDirection.Output:
            name = self.name_str
            if name == '':
                name = 'default output'
            if name in new_graph.output_ports:
                raise ProgramError(self, f'Duplicate output `{name}`', hints=[
                    CompilerHint(new_graph.output_ports[name].port_spec, 'Previous output was here'),
                ])
            port = model.TypeOutputPort(new_graph, ty, name=name, output_name=self.name_str, port_spec=self.port_spec, resource_type=resource_type)
        else:
            raise Exception(f'Unknown direction: {self.direction!r}')

        copy_attributes(port, self, new_graph.coordinator.attributes(self))

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        if self.name is None:
            return
        previous = graph.name_declarations.get(self.name.id_str)
        # prepare_interface reports duplicate ports of the same kind.
        if not (isinstance(previous, DeclarationPort) and previous.direction == self.direction):
            model.ast.register_name(graph, self.name, self)
