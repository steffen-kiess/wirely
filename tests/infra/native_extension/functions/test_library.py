"""Native functions used by tests."""

import wirely
from ..generated.structs import TestLibraryOperatorValue
from ..generated.enums import TestLibraryOperatorOpen, TestLibraryOperatorClosed


def clamp(value: wirely.Int16, low: wirely.Int16, high: wirely.Int16) -> wirely.Int16:
    return min(max(value, low), high)


def nothing() -> wirely.Unit:
    return wirely.Unit()


# Violates the contract on purpose, to test how failures are reported.


def fail() -> bool:
    raise RuntimeError('Fail always fails')


def operator_combine(a: TestLibraryOperatorValue, b: wirely.Int16) -> wirely.Int16:
    return wirely.Int16(min(32767, max(-32768, a.value.value + b.value)))


def operator_less(a: TestLibraryOperatorValue, b: TestLibraryOperatorValue) -> bool:
    return a.value < b.value


def operator_open_less(a: TestLibraryOperatorOpen, b: TestLibraryOperatorOpen) -> bool:
    return a.value < b.value


def operator_closed_less(a: TestLibraryOperatorClosed, b: TestLibraryOperatorClosed) -> bool:
    return a.value < b.value
