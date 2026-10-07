"""Time-of-day arithmetic, wrapping at midnight."""

import wirely
from wirely.functions import native_function

native_functions = {}

_DAY_NS = wirely.TimeOfDay.END_NS


def _wrap(time, offset_ns):
    # Whole days leave every time unchanged, including 24:00. Other results
    # lie in 00:00 ... 23:59:59.999999999.
    if offset_ns % _DAY_NS == 0:
        return time
    return wirely.TimeOfDay((time.nanoseconds + offset_ns) % _DAY_NS)


@native_function(native_functions, 'WrappingAdd')
def wrapping_add(*, context):
    return _wrap(context.arguments['time'], context.arguments['duration'].duration_ns)


@native_function(native_functions, 'WrappingSub')
def wrapping_sub(*, context):
    return _wrap(context.arguments['time'], -context.arguments['duration'].duration_ns)


@native_function(native_functions, 'DurationUntil')
def duration_until(*, context):
    difference = context.arguments['to'].nanoseconds - context.arguments['from'].nanoseconds
    if difference < 0:
        difference += _DAY_NS
    return wirely.Duration(duration_ns=difference)
