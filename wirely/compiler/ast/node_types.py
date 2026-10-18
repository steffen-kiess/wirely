"""Node type declarations."""

import dataclasses
import typing

from wirely.compiler import model

from .base import CompilerHint, Declaration, Identifier, ProgramError, SourcePosition
from .specifiers import NodeTypeSpecifier
from .values import StringConstant

if typing.TYPE_CHECKING:
    from .scope import Scope


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationNodeType(Declaration):
    """A node type block, or the global body without a name. Each compilation builds a NodeType from it."""

    specifiers: tuple[NodeTypeSpecifier, ...]
    name: Identifier | None
    declarations: tuple[Declaration, ...]
    _: dataclasses.KW_ONLY
    alias: StringConstant | None = None
    is_native: bool = dataclasses.field(init=False)
    is_singleton: bool = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        status = {'native': False, 'singleton': False}
        for specifier in self.specifiers:
            name = specifier.specifier_str
            if name not in status:
                raise Exception(f'Unknown specifier: {name!r}')
            if status[name]:
                raise ProgramError(specifier, f'Got duplicate {name} specifier')
            status[name] = True
        object.__setattr__(self, 'is_native', status['native'])
        object.__setattr__(self, 'is_singleton', status['singleton'])

    @staticmethod
    def make(*args: typing.Any, source_position: SourcePosition) -> 'DeclarationNodeType':
        # Grammar arguments mix specifiers, a name, an alias, and declarations.
        specifiers = args[:-3]
        name, alias, declarations = args[-3:]
        return DeclarationNodeType(tuple(specifiers), name, tuple(declarations), alias=alias,
                                   source_position=source_position, inner_attributes=getattr(declarations, 'inner_attributes', ()))

    def collect_declarations(self, graph: 'Scope') -> None:
        model.NodeType(self, graph).collect_declarations()

    def register(self, graph: 'Scope', node_type: 'model.NodeType') -> None:
        """Register the node type built from this declaration in its enclosing scope."""
        assert self.name is not None
        if self.name.id_str in graph.struct_types:
            raise ProgramError(self.name, f'Node type {self.name.id_str} conflicts with struct type', hints=[
                CompilerHint(graph.struct_types[self.name.id_str].declaration, 'Previous declaration was here'),
            ])
        if self.name.id_str in graph.enum_types:
            raise ProgramError(self.name, f'Node type {self.name.id_str} conflicts with enum type', hints=[
                CompilerHint(graph.enum_types[self.name.id_str].declaration, 'Previous declaration was here'),
            ])
        if self.name.id_str in graph.resource_types:
            raise ProgramError(self, f'Node type {self.name.id_str} conflicts with resource type', hints=[
                CompilerHint(graph.resource_types[self.name.id_str].declaration, 'Previous resource type was here'),
            ])
        if self.name.id_str in graph.node_types:
            hints = [
                CompilerHint(graph.node_types[self.name.id_str].declaration, 'Previous node type was here'),
            ]
            raise ProgramError(self, f'Duplicate node type {self.name!r}', hints=hints)
        graph.node_types[self.name.id_str] = node_type
