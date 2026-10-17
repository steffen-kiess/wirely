"""Native functions used by tests."""

from collections.abc import Callable

import wirely
from wirely.functions import RuntimeFunctionContext, native_function

native_functions: dict[str, Callable[..., object]] = {}


@native_function(native_functions, 'Clamp')
def clamp(*, context: RuntimeFunctionContext) -> object:
    value, low, high = context.arguments
    return min(max(value, low), high)


@native_function(native_functions, 'Nothing')
def nothing(*, context: RuntimeFunctionContext) -> object:
    return wirely.Unit()


# Violates the contract on purpose, to test how failures are reported.
@native_function(native_functions, 'Fail')
def fail(*, context: RuntimeFunctionContext) -> object:
    raise RuntimeError('Fail always fails')
