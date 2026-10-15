"""Compiler-only configuration predicates."""


def supports_type(*, context):
    value_type, = context.arguments
    return value_type.supports_csv
