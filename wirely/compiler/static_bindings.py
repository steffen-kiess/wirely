"""Evaluate native consumers' static values after instance resolution."""

from types import MappingProxyType

from . import model
from .expressions import compile_constant


def evaluate_bindings(uses, instance):
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
                concrete = value.evaluate()
                values[binding.key] = model.value_types._to_native(value.value_type, concrete)
            except (ValueError, ArithmeticError) as error:
                raise model.ProgramError(expression, str(error)) from error
    return MappingProxyType(values)
