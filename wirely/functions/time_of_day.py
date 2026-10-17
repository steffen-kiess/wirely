"""Time-of-day arithmetic, wrapping at midnight."""

import wirely
from wirely.contracts import time_of_day as contracts
from wirely.functions import RegisteredFunction, native_function

native_functions: dict[str, RegisteredFunction] = {}

_DAY_TICKS = wirely.TimeOfDay.END_TICKS


@native_function(native_functions, contracts.Less)
def less(a: wirely.TimeOfDay, b: wirely.TimeOfDay) -> bool:
    return a.ticks < b.ticks


def _wrap(time: wirely.TimeOfDay, offset: int) -> wirely.TimeOfDay:
    # Whole days leave every time unchanged, including 24:00. Other results
    # lie before 24:00.
    if offset % _DAY_TICKS == 0:
        return time
    return wirely.TimeOfDay(ticks=(time.ticks + offset) % _DAY_TICKS)


@native_function(native_functions, contracts.WrappingAdd)
def wrapping_add(time: wirely.TimeOfDay, duration: wirely.Duration) -> wirely.TimeOfDay:
    return _wrap(time, duration.ticks)


@native_function(native_functions, contracts.WrappingSub)
def wrapping_sub(time: wirely.TimeOfDay, duration: wirely.Duration) -> wirely.TimeOfDay:
    return _wrap(time, -duration.ticks)


@native_function(native_functions, contracts.DurationUntil)
def duration_until(start: wirely.TimeOfDay, end: wirely.TimeOfDay) -> wirely.Duration:
    difference = end.ticks - start.ticks
    if difference < 0:
        difference += _DAY_TICKS
    return wirely.Duration(ticks=difference)
