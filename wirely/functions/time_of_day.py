"""Time-of-day arithmetic, wrapping at midnight."""

from collections.abc import Callable

import wirely
from wirely.functions import RuntimeFunctionContext, native_function

native_functions: dict[str, Callable[..., object]] = {}

_DAY_TICKS = wirely.TimeOfDay.END_TICKS


@native_function(native_functions, 'Less')
def less(*, context: RuntimeFunctionContext) -> object:
    a, b = context.arguments
    return a.ticks < b.ticks


def _wrap(time: wirely.TimeOfDay, offset: int) -> wirely.TimeOfDay:
    # Whole days leave every time unchanged, including 24:00. Other results
    # lie before 24:00.
    if offset % _DAY_TICKS == 0:
        return time
    return wirely.TimeOfDay(ticks=(time.ticks + offset) % _DAY_TICKS)


@native_function(native_functions, 'WrappingAdd')
def wrapping_add(*, context: RuntimeFunctionContext) -> object:
    time, duration = context.arguments
    return _wrap(time, duration.ticks)


@native_function(native_functions, 'WrappingSub')
def wrapping_sub(*, context: RuntimeFunctionContext) -> object:
    time, duration = context.arguments
    return _wrap(time, -duration.ticks)


@native_function(native_functions, 'DurationUntil')
def duration_until(*, context: RuntimeFunctionContext) -> object:
    start, end = context.arguments
    difference = end.ticks - start.ticks
    if difference < 0:
        difference += _DAY_TICKS
    return wirely.Duration(ticks=difference)
