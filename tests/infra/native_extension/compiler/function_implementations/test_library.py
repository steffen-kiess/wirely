"""Compile-time functions used by tests."""

from wirely import MonotonicTime, UInt64, UInt128
from wirely.compiler.native_functions import FunctionServices


def monotonic_time(generation: UInt64, ticks: UInt128, *, services: FunctionServices) -> MonotonicTime:
    return MonotonicTime(generation=generation.value, ticks=ticks.value)
