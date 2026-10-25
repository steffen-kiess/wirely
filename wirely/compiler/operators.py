"""Operator lookup and types awaiting the first operand's specialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from . import model

if TYPE_CHECKING:
    from collections.abc import Mapping
    from .parameters import StaticValue
    from ..runtime.extensions import NativeTypes


SYMBOLS = {'add': '+', 'sub': '-', 'lt': '<', 'le': '<', 'gt': '<', 'ge': '<'}


def operator_function(scope: model.Scope, value_type: model.Type, operation: str,
                      source: model.ast.SourceElement) -> model.CompiledFunction:
    symbol = SYMBOLS[operation]
    if isinstance(value_type, (model.types.TypeStruct, model.types.TypeEnum)):
        namespace: model.Scope | None = scope.coordinator.global_node_type
        for name in value_type.name.split('.'):
            namespace = namespace.libraries.get(name) if namespace is not None else None
        if isinstance(namespace, model.Namespace):
            binding = namespace.operator_bindings.get(symbol)
            if binding is not None and not scope.coordinator.has_failed(binding):
                return binding.resolve_function(namespace)
    raise model.ProgramError(source, f'Type `{value_type}` has no binding for operator `{symbol}`')


@dataclass(frozen=True)
class OperatorType(model.Type):
    """The right argument or result type of an operator on an unresolved type."""

    first_type: model.Type
    operation: str
    scope: model.Scope
    source: model.ast.SourceElement
    argument: bool = False
    integer_only: bool = False

    @property
    def contains_parameters(self) -> bool:
        return True

    def resolve(self, parameters: Mapping[model.NodeParameter, StaticValue]) -> model.Type:
        from .types import resolve_type

        first = resolve_type(self.first_type, parameters)
        if first.contains_parameters:
            return OperatorType(first, self.operation, self.scope, self.source, self.argument, self.integer_only)
        if self.integer_only:
            if not isinstance(first, model.types.TypeInt):
                raise model.ProgramError(self.source, 'User-defined operators require a typed first operand')
            return first
        if isinstance(first, model.types.TypeInt):
            return first if self.argument or self.operation in ('add', 'sub') else model.types.boolean
        function = operator_function(self.scope, first, self.operation, self.source)
        return function.parameter_types[1] if self.argument else function.return_type

    def __str__(self) -> str:
        return f'operator {SYMBOLS[self.operation]} on {self.first_type}'

    def default_value(self) -> NoReturn:
        raise ValueError('Operator types require specialization')

    def verify_value(self, value: object) -> NoReturn:
        raise ValueError('Operator types require specialization')

    def equals(self, value1: object, value2: object) -> NoReturn:
        raise ValueError('Operator types require specialization')

    def _parse_impl(self, s: str, *, native_types: NativeTypes | None) -> NoReturn:
        raise ValueError('Operator types require specialization')
