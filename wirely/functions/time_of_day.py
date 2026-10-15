"""Time-of-day arithmetic, wrapping at midnight."""

import wirely
from wirely.functions import native_function

native_functions = {}

_DAY_TICKS = wirely.TimeOfDay.END_TICKS


def _wrap(time, offset):
    # Whole days leave every time unchanged, including 24:00. Other results
    # lie before 24:00.
    if offset % _DAY_TICKS == 0:
        return time
    return wirely.TimeOfDay(ticks=(time.ticks + offset) % _DAY_TICKS)


@native_function(native_functions, 'WrappingAdd')
def wrapping_add(*, context):
    return _wrap(context.arguments['time'], context.arguments['duration'].ticks)


@native_function(native_functions, 'WrappingSub')
def wrapping_sub(*, context):
    return _wrap(context.arguments['time'], -context.arguments['duration'].ticks)


@native_function(native_functions, 'DurationUntil')
def duration_until(*, context):
    difference = context.arguments['to'].ticks - context.arguments['from'].ticks
    if difference < 0:
        difference += _DAY_TICKS
    return wirely.Duration(ticks=difference)
