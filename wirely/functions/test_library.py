"""Native functions used by tests."""

import wirely
from wirely.contracts import test_library as contracts
from wirely.functions import RegisteredFunction, native_function

native_functions: dict[str, RegisteredFunction] = {}


@native_function(native_functions, contracts.Clamp)
def clamp(value: wirely.Int16, low: wirely.Int16, high: wirely.Int16) -> wirely.Int16:
    return min(max(value, low), high)


@native_function(native_functions, contracts.Nothing)
def nothing() -> wirely.Unit:
    return wirely.Unit()


# Violates the contract on purpose, to test how failures are reported.
@native_function(native_functions, contracts.Fail)
def fail() -> bool:
    raise RuntimeError('Fail always fails')
