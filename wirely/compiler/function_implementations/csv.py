"""Compiler-only configuration predicates."""

from ..native_functions import NativeFunctionContext


def supports_type(*, context: NativeFunctionContext) -> bool:
    value_type, = context.arguments
    return value_type.supports_csv
