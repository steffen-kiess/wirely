"""Native functions used by tests."""

from collections.abc import Callable

import wirely
from wirely.functions import native_function

native_functions: dict[str, Callable[..., object]] = {}


@native_function(native_functions, 'Clamp')
def clamp(*, context):
    value, low, high = context.arguments
    return min(max(value, low), high)


@native_function(native_functions, 'Nothing')
def nothing(*, context):
    return wirely.Unit()


# Violates the contract on purpose, to test how failures are reported.
@native_function(native_functions, 'Fail')
def fail(*, context):
    raise RuntimeError('Fail always fails')
