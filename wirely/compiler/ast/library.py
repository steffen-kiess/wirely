"""Namespace declarations."""

from wirely.compiler import model
from wirely.util import check_parameters

from .base import SourcePosition, CompilerHint, ProgramError, Identifier
from .scope import Scope
from .type_declarations import DeclarationStructType, DeclarationEnumType, DeclarationResourceType
from .functions import DeclarationNativeFunction


# TODO: Should this to some extend be merged with NodeType? Should there be a common base class?
class DeclarationLibrary(Scope):
    @check_parameters
    def __init__(self, name: Identifier, declarations: list, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.declarations = declarations
        self.inner_attributes = getattr(declarations, 'inner_attributes', ())

        # TODO?
        self.libraries = {}

    @staticmethod
    def make(*args, source_position: SourcePosition):
        names = args[:-1]
        declarations = args[-1]
        for name in reversed(names):
            if name.id_str == 'parameter_option':
                raise ProgramError(name, 'parameter_option is reserved for the intrinsic')
            declarations = [DeclarationLibrary(
                name, declarations, source_position=source_position)]
        return declarations[0]

    @property
    def fullname(self):
        if isinstance(self.parent_graph, DeclarationLibrary):
            return self.parent_graph.fullname + '.' + self.name.id_str
        return self.name.id_str

    @property
    def fullname_tuple(self):
        if isinstance(self.parent_graph, DeclarationLibrary):
            return self.parent_graph.fullname_tuple + (self.name.id_str,)
        return (self.name.id_str,)

    def collect_declarations(self, graph):
        if hasattr(self, 'parent_graph'):
            raise Exception("hasattr(self, 'parent_graph')")
        self.parent_graph = graph
        self.services = graph.services
        self.coordinator = graph.coordinator
        # TODO: What should be allowed here?
        if not isinstance(graph, DeclarationLibrary) and graph.name is not None:
            raise ProgramError(self, 'Attempt to put namespace into invalid context')

        if self.name.id_str in graph.struct_types:
            raise model.ProgramError(self.name, f'Namespace {self.name.id_str} conflicts with struct type', hints=[
                model.CompilerHint(graph.struct_types[self.name.id_str], 'Previous declaration was here'),
            ])
        if self.name.id_str in graph.enum_types:
            raise ProgramError(self.name, f'Namespace {self.name.id_str} conflicts with enum type', hints=[
                CompilerHint(graph.enum_types[self.name.id_str].declaration, 'Previous declaration was here'),
            ])
        self.compiled_scope = graph.libraries.setdefault(self.name.id_str, self)

        self.coordinator.declaration_groups.append((self.compiled_scope, self.declarations))

        for decl in self.declarations:
            with self.coordinator.contain(decl), decl.context:
                # TODO: What is allowed here?
                if not isinstance(decl, (model.NodeType, DeclarationLibrary, DeclarationResourceType, DeclarationEnumType, DeclarationStructType, DeclarationNativeFunction)):
                    hints = [
                        CompilerHint(self, 'In this namespace'),
                    ]
                    raise ProgramError(decl, 'Declaration not allowed in namespace', hints=hints)

                decl.collect_declarations(self.compiled_scope)
                

    def __repr__(self):
        return self.fullname
