"""Explicit operator bindings in a type's companion namespace."""

import dataclasses

from wirely.compiler import model

from .base import Declaration, Identifier, ProgramError, CompilerHint
from .values import Reference


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationOperator(Declaration):
    operator: Identifier
    function: Reference

    def collect_declarations(self, graph: model.Scope) -> None:
        if not isinstance(graph, model.Namespace):
            raise ProgramError(self, 'Operator bindings require a type companion namespace')
        symbol = self.operator.id_str
        if symbol not in ('+', '-', '<'):
            raise ProgramError(self.operator, f'Operator `{symbol}` cannot be bound')
        previous = graph.operator_bindings.get(symbol)
        if previous is not None:
            raise ProgramError(self, f'Duplicate binding for operator `{symbol}`', hints=[
                CompilerHint(previous, 'Previous binding was here'),
            ])
        graph.operator_bindings[symbol] = self

    def resolve_function(self, graph: model.Scope) -> model.CompiledFunction:
        assert isinstance(graph, model.Namespace)
        parent = graph.parent_graph
        assert parent is not None
        name = graph.name.id_str
        struct = parent.struct_types.get(name)
        enum = parent.enum_types.get(name)
        owner: model.Type
        if struct is not None:
            owner = struct.resolve_fields()
        elif enum is not None:
            owner = enum.value_type
        else:
            raise ProgramError(self, 'Operator bindings require a user-defined value type companion namespace')
        function = graph.resolve_function(self.function)
        assert function is not None
        symbol = self.operator.id_str
        if len(function.parameter_types) != 2 or function.parameter_types[0] != owner:
            raise ProgramError(self, f'Operator `{symbol}` requires a function with two parameters, '
                               f'the first of type `{owner}`')
        if symbol == '<' and (function.parameter_types[1] != owner
                              or function.return_type != model.types.boolean):
            raise ProgramError(self, f'Operator `<` requires signature `({owner}, {owner}): boolean`')
        return function

    def prepare_interface(self, graph: model.Scope) -> None:
        self.resolve_function(graph)
