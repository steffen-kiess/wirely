"""Monotonic time arithmetic within a generation.

Times from different generations are not comparable, so operations relating
two times return None for them.
"""

import wirely
from wirely.functions import native_function

native_functions = {}

_MAX_TICKS = wirely.MonotonicTime.MAX_TICKS


def _offset(time, duration, sign):
    return time.generation, time.ticks + sign * duration.ticks


def _saturating(time, duration, sign):
    generation, ticks = _offset(time, duration, sign)
    return wirely.MonotonicTime(generation=generation, ticks=min(max(ticks, 0), _MAX_TICKS))


def _checked(time, duration, sign):
    generation, ticks = _offset(time, duration, sign)
    if 0 <= ticks <= _MAX_TICKS:
        return wirely.Some(wirely.MonotonicTime(generation=generation, ticks=ticks))
    return None


@native_function(native_functions, 'SaturatingAdd')
def saturating_add(*, context):
    time, duration = context.arguments
    return _saturating(time, duration, 1)


@native_function(native_functions, 'SaturatingSub')
def saturating_sub(*, context):
    time, duration = context.arguments
    return _saturating(time, duration, -1)


@native_function(native_functions, 'CheckedAdd')
def checked_add(*, context):
    time, duration = context.arguments
    return _checked(time, duration, 1)


@native_function(native_functions, 'CheckedSub')
def checked_sub(*, context):
    time, duration = context.arguments
    return _checked(time, duration, -1)


@native_function(native_functions, 'CheckedDurationSince')
def checked_duration_since(*, context):
    time, earlier = context.arguments
    if time.generation != earlier.generation or earlier.ticks > time.ticks:
        return None
    return wirely.Some(wirely.Duration(ticks=time.ticks - earlier.ticks))


@native_function(native_functions, 'CheckedLess')
def checked_less(*, context):
    a, b = context.arguments
    if a.generation != b.generation:
        return None
    return wirely.Some(a.ticks < b.ticks)
