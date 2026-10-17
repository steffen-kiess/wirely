"""Native functions used by tests."""

import wirely


def clamp(value: wirely.Int16, low: wirely.Int16, high: wirely.Int16) -> wirely.Int16:
    return min(max(value, low), high)


def nothing() -> wirely.Unit:
    return wirely.Unit()


# Violates the contract on purpose, to test how failures are reported.


def fail() -> bool:
    raise RuntimeError('Fail always fails')
