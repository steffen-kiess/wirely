"""Native functions used by tests."""

import wirely
from wirely.functions import native_function

native_functions = {}


@native_function(native_functions, 'Clamp')
def clamp(*, context):
    arguments = context.arguments
    return min(max(arguments['value'], arguments['low']), arguments['high'])


@native_function(native_functions, 'Nothing')
def nothing(*, context):
    return wirely.Unit()


# Violates the contract on purpose, to test how failures are reported.
@native_function(native_functions, 'Fail')
def fail(*, context):
    raise RuntimeError('Fail always fails')
