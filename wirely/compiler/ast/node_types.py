"""Node type declarations."""

import typing

from wirely.compiler import model
from wirely.util import check_parameters

from ..attributes import ParsedFile
from .base import CompilerHint, Declaration, Identifier, ProgramError, SourcePosition
from .specifiers import NodeTypeSpecifier
from .values import StringConstant

if typing.TYPE_CHECKING:
    from .scope import Scope


class DeclarationNodeType(Declaration):
    """A node type block, or the global body without a name. Each compilation builds a NodeType from it."""

    @check_parameters
    def __init__(self, specifiers: list[NodeTypeSpecifier], name: Identifier | None,
                 declarations: ParsedFile | list[Declaration], *, alias: StringConstant | None = None,
                 source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.alias = alias  # Native name replacing the qualified name.
        self.declarations = declarations
        self.inner_attributes = getattr(declarations, 'inner_attributes', ())

        all_specifiers = ['native', 'singleton']
        specifiers_status = dict({key: False for key in all_specifiers})
        for specifier in specifiers:
            if specifier.specifier_str not in all_specifiers:
                raise Exception(f'Unknown specifier: {specifier.specifier_str!r}')
            if specifiers_status[specifier.specifier_str]:
                raise ProgramError(specifier, f'Got duplicate {specifier.specifier_str} specifier')
            specifiers_status[specifier.specifier_str] = True
        self.is_native = specifiers_status['native']
        self.is_singleton = specifiers_status['singleton']

    @staticmethod
    def make(*args: typing.Any, source_position: SourcePosition) -> 'DeclarationNodeType':
        # Grammar arguments mix specifiers, a name, an alias, and declarations.
        specifiers = args[:-3]
        name, alias, declarations = args[-3:]
        return DeclarationNodeType(list(specifiers), name, declarations, alias=alias, source_position=source_position)

    def collect_declarations(self, graph: 'Scope') -> None:
        model.NodeType(self, graph).collect_declarations()

    def register(self, graph: 'Scope', node_type: 'model.NodeType') -> None:
        """Register the node type built from this declaration in its enclosing scope."""
        assert self.name is not None
        if self.name.id_str in graph.struct_types:
            raise ProgramError(self.name, f'Node type {self.name.id_str} conflicts with struct type', hints=[
                CompilerHint(graph.struct_types[self.name.id_str], 'Previous declaration was here'),
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
