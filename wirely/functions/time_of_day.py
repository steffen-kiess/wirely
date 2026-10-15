"""Time-of-day arithmetic, wrapping at midnight."""

import wirely
from wirely.functions import native_function

native_functions = {}

_DAY_TICKS = wirely.TimeOfDay.END_TICKS


@native_function(native_functions, 'Less')
def less(*, context):
    a, b = context.arguments
    return a.ticks < b.ticks


def _wrap(time, offset):
    # Whole days leave every time unchanged, including 24:00. Other results
    # lie before 24:00.
    if offset % _DAY_TICKS == 0:
        return time
    return wirely.TimeOfDay(ticks=(time.ticks + offset) % _DAY_TICKS)


@native_function(native_functions, 'WrappingAdd')
def wrapping_add(*, context):
    time, duration = context.arguments
    return _wrap(time, duration.ticks)


@native_function(native_functions, 'WrappingSub')
def wrapping_sub(*, context):
    time, duration = context.arguments
    return _wrap(time, -duration.ticks)


@native_function(native_functions, 'DurationUntil')
def duration_until(*, context):
    start, end = context.arguments
    difference = end.ticks - start.ticks
    if difference < 0:
        difference += _DAY_TICKS
    return wirely.Duration(ticks=difference)
