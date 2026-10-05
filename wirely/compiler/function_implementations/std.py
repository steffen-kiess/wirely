"""Compiler-only configuration predicates."""


def is_valid_edge_type(*, context):
    return context.arguments['value'] in ('Rising', 'Falling', 'Both')
