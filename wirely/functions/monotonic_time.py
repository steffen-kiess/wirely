"""Monotonic time arithmetic within a generation.

Times from different generations are not comparable, so operations relating
two times return None for them.
"""

import wirely
from wirely.functions import native_function

native_functions = {}

_MAX_NS = wirely.MonotonicTime.MAX_NS


def _offset(context, sign):
    time = context.arguments['time']
    return time.generation, time.time_ns + sign * context.arguments['duration'].duration_ns


def _saturating(context, sign):
    generation, time_ns = _offset(context, sign)
    return wirely.MonotonicTime(generation, min(max(time_ns, 0), _MAX_NS))


def _checked(context, sign):
    generation, time_ns = _offset(context, sign)
    if 0 <= time_ns <= _MAX_NS:
        return wirely.Some(wirely.MonotonicTime(generation, time_ns))
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
    if time.generation != earlier.generation or earlier.time_ns > time.time_ns:
        return None
    return wirely.Some(wirely.Duration(duration_ns=time.time_ns - earlier.time_ns))


@native_function(native_functions, 'CheckedLess')
def checked_less(*, context):
    a, b = context.arguments['a'], context.arguments['b']
    if a.generation != b.generation:
        return None
    return wirely.Some(a.time_ns < b.time_ns)
