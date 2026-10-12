"""Evaluate native consumers' static values after instance resolution."""

from types import MappingProxyType

from ..program import Constant
from . import model
from .expressions import compile_constant
from .types import resolve_type


def evaluate_bindings(uses, instance):
    """Return the typed values bound by a `uses` clause, keyed by name."""
    values = {}
    if uses is None:
        return MappingProxyType(values)
    scope = instance.node_type
    for binding in uses.bindings:
        expression = binding.expression
        with expression.context:
            value = compile_constant(scope, expression, None, instance.static_values)
            value = value.resolve_self(instance.static_values)
            try:
                value_type = resolve_type(value.value_type, instance.static_values)
                values[binding.key] = Constant(value_type, value_type.verify_value(value.evaluate()))
            except (ValueError, ArithmeticError) as error:
                raise model.ProgramError(expression, str(error)) from error
    return MappingProxyType(values)
