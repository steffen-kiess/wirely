"""Monotonic time arithmetic within a generation.

Times from different generations are not comparable, so operations relating
two times return None for them.
"""

import wirely
from wirely.functions import native_function

native_functions = {}

_MAX_TICKS = wirely.MonotonicTime.MAX_TICKS


def _offset(context, sign):
    time = context.arguments['time']
    return time.generation, time.ticks + sign * context.arguments['duration'].ticks


def _saturating(context, sign):
    generation, ticks = _offset(context, sign)
    return wirely.MonotonicTime(generation=generation, ticks=min(max(ticks, 0), _MAX_TICKS))


def _checked(context, sign):
    generation, ticks = _offset(context, sign)
    if 0 <= ticks <= _MAX_TICKS:
        return wirely.Some(wirely.MonotonicTime(generation=generation, ticks=ticks))
    return None


@native_function(native_functions, 'SaturatingAdd')
def saturating_add(*, context):
    return _saturating(context, 1)


@native_function(native_functions, 'SaturatingSub')
def saturating_sub(*, context):
    return _saturating(context, -1)


@native_function(native_functions, 'CheckedAdd')
def checked_add(*, context):
    return _checked(context, 1)


@native_function(native_functions, 'CheckedSub')
def checked_sub(*, context):
    return _checked(context, -1)


@native_function(native_functions, 'CheckedDurationSince')
def checked_duration_since(*, context):
    time, earlier = context.arguments['time'], context.arguments['earlier']
    if time.generation != earlier.generation or earlier.ticks > time.ticks:
        return None
    return wirely.Some(wirely.Duration(ticks=time.ticks - earlier.ticks))


@native_function(native_functions, 'CheckedLess')
def checked_less(*, context):
    a, b = context.arguments['a'], context.arguments['b']
    if a.generation != b.generation:
        return None
    return wirely.Some(a.ticks < b.ticks)
