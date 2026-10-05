"""Compiler-only configuration predicates."""


def supports_type(*, context):
    return context.arguments['type'].supports_csv
