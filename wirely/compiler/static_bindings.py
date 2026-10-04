"""Evaluate native consumers' static values after instance resolution."""

from types import MappingProxyType

from wirely import MISSING_PARAMETER
from . import model as blocks
from .expressions import compile_constant


def evaluate_bindings(uses, instance):
    values = {}
    if uses is None:
        return MappingProxyType(values)
    scope = instance.block_type
    for binding in uses.bindings:
        expression = binding.expression
        with expression.context:
            declaration = None
            if isinstance(expression, blocks.Reference) and expression.length == 1:
                declaration = scope.static_declarations.get(expression.identifiers[0].id_str)
            if (declaration is not None and declaration.is_optional
                    and declaration.default_value is None
                    and declaration not in instance.static_values):
                values[binding.key] = MISSING_PARAMETER
                continue
            value = compile_constant(scope, expression, None, instance.static_values)
            value = value.resolve_self(instance.static_values)
            try:
                concrete = value.evaluate()
                values[binding.key] = blocks.value_types._to_native(value.value_type, concrete)
            except (ValueError, ArithmeticError) as error:
                raise blocks.ProgramError(expression, str(error)) from error
    return MappingProxyType(values)
