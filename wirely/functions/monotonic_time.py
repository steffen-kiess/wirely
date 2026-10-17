"""Monotonic time arithmetic within a generation.

Times from different generations are not comparable, so operations relating
two times return None for them.
"""

from collections.abc import Callable

import wirely
from wirely.functions import RuntimeFunctionContext, native_function

native_functions: dict[str, Callable[..., object]] = {}

_MAX_TICKS = wirely.MonotonicTime.MAX_TICKS


def _offset(time: wirely.MonotonicTime, duration: wirely.Duration, sign: int) -> tuple[int, int]:
    return time.generation, time.ticks + sign * duration.ticks


def _saturating(time: wirely.MonotonicTime, duration: wirely.Duration, sign: int) -> wirely.MonotonicTime:
    generation, ticks = _offset(time, duration, sign)
    return wirely.MonotonicTime(generation=generation, ticks=min(max(ticks, 0), _MAX_TICKS))


def _checked(time: wirely.MonotonicTime, duration: wirely.Duration, sign: int) -> wirely.Some | None:
    generation, ticks = _offset(time, duration, sign)
    if 0 <= ticks <= _MAX_TICKS:
        return wirely.Some(wirely.MonotonicTime(generation=generation, ticks=ticks))
    return None


@native_function(native_functions, 'SaturatingAdd')
def saturating_add(*, context: RuntimeFunctionContext) -> object:
    time, duration = context.arguments
    return _saturating(time, duration, 1)


@native_function(native_functions, 'SaturatingSub')
def saturating_sub(*, context: RuntimeFunctionContext) -> object:
    time, duration = context.arguments
    return _saturating(time, duration, -1)


@native_function(native_functions, 'CheckedAdd')
def checked_add(*, context: RuntimeFunctionContext) -> object:
    time, duration = context.arguments
    return _checked(time, duration, 1)


@native_function(native_functions, 'CheckedSub')
def checked_sub(*, context: RuntimeFunctionContext) -> object:
    time, duration = context.arguments
    return _checked(time, duration, -1)


@native_function(native_functions, 'CheckedDurationSince')
def checked_duration_since(*, context: RuntimeFunctionContext) -> object:
    time, earlier = context.arguments
    if time.generation != earlier.generation or earlier.ticks > time.ticks:
        return None
    return wirely.Some(wirely.Duration(ticks=time.ticks - earlier.ticks))


@native_function(native_functions, 'CheckedLess')
def checked_less(*, context: RuntimeFunctionContext) -> object:
    a, b = context.arguments
    if a.generation != b.generation:
        return None
    return wirely.Some(a.ticks < b.ticks)
