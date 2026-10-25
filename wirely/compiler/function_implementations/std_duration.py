"""Compile-time duration arithmetic."""

from wirely import Duration

from ..native_functions import FunctionServices


def add(a: Duration, b: Duration, *, services: FunctionServices) -> Duration:
    ticks = a.ticks + b.ticks
    if ticks > Duration.MAX_TICKS:
        raise services.error('Duration overflow: result exceeds the maximum duration')
    return Duration(ticks=ticks)


def sub(a: Duration, b: Duration, *, services: FunctionServices) -> Duration:
    if a.ticks < b.ticks:
        raise services.error('Negative duration')
    return Duration(ticks=a.ticks - b.ticks)
