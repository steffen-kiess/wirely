"""Namespace declarations and the scopes they declare."""

from typing import Any

from wirely.compiler import model
from wirely.util import check_parameters

from .base import SourcePosition, CompilerHint, Declaration, ProgramError, Identifier
from .scope import Scope
from .type_declarations import DeclarationStructType, DeclarationEnumType, DeclarationResourceType
from .functions import DeclarationNativeFunction


class DeclarationLibrary(Declaration):
    """A namespace block. Blocks with the same name in a scope declare one Namespace."""

    @check_parameters
    def __init__(self, name: Identifier, declarations: list, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.declarations = declarations
        self.inner_attributes = getattr(declarations, 'inner_attributes', ())

    @staticmethod
    def make(*args: Any, source_position: SourcePosition) -> 'DeclarationLibrary':
        names = args[:-1]
        declarations = args[-1]
        for name in reversed(names):
            declarations = [DeclarationLibrary(
                name, declarations, source_position=source_position)]
        return declarations[0]

    def collect_declarations(self, graph: Scope) -> None:
        # TODO: What should be allowed here?
        if not isinstance(graph, Namespace) and graph.name is not None:
            raise ProgramError(self, 'Attempt to put namespace into invalid context')

        if self.name.id_str in graph.struct_types:
            raise model.ProgramError(self.name, f'Namespace {self.name.id_str} conflicts with struct type', hints=[
                model.CompilerHint(graph.struct_types[self.name.id_str], 'Previous declaration was here'),
            ])
        if self.name.id_str in graph.enum_types:
            raise ProgramError(self.name, f'Namespace {self.name.id_str} conflicts with enum type', hints=[
                CompilerHint(graph.enum_types[self.name.id_str].declaration, 'Previous declaration was here'),
            ])
        namespace = graph.libraries.get(self.name.id_str)
        if namespace is None:
            namespace = graph.libraries[self.name.id_str] = Namespace(self, graph)
        else:
            namespace.blocks.append(self)

        namespace.coordinator.declaration_groups.append((namespace, self.declarations))

        for decl in self.declarations:
            with namespace.coordinator.contain(decl), decl.context:
                # TODO: What is allowed here?
                if not isinstance(decl, (model.DeclarationNodeType, DeclarationLibrary, DeclarationResourceType, DeclarationEnumType, DeclarationStructType, DeclarationNativeFunction)):
                    hints = [
                        CompilerHint(self, 'In this namespace'),
                    ]
                    raise ProgramError(decl, 'Declaration not allowed in namespace', hints=hints)

                decl.collect_declarations(namespace)


class Namespace(Scope):
    """The declarations of the namespace blocks with one name in a scope."""

    def __init__(self, block: DeclarationLibrary, parent_graph: Scope) -> None:
        super().__init__()
        self.blocks = [block]
        self.parent_graph = parent_graph
        self.services = parent_graph.services
        self.coordinator = parent_graph.coordinator

    @property
    def declaration(self) -> DeclarationLibrary:
        """The first block, where errors about the namespace point."""
        return self.blocks[0]

    @property
    def name(self) -> Identifier:
        return self.declaration.name

    @property
    def attributes(self) -> tuple:
        """The validated attributes of all blocks."""
        return tuple(attribute for block in self.blocks for attribute in block.attributes)

    @property
    def fullname(self) -> str:
        if isinstance(self.parent_graph, Namespace):
            return self.parent_graph.fullname + '.' + self.name.id_str
        return self.name.id_str

    @property
    def fullname_tuple(self) -> tuple[str, ...]:
        if isinstance(self.parent_graph, Namespace):
            return self.parent_graph.fullname_tuple + (self.name.id_str,)
        return (self.name.id_str,)

    def __repr__(self) -> str:
        return self.fullname
