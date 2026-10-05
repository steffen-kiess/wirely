"""Compiler-only configuration predicates."""


def is_valid_code(*, context):
    code = context.arguments['code']
    return len(code) <= 15 and all('0' <= digit <= '9' for digit in code)
