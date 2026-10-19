"""Monotonic time arithmetic within a generation.

Times from different generations are not comparable, so operations relating
two times return None for them.
"""

import wirely


_MAX_TICKS = wirely.MonotonicTime.MAX_TICKS


def _offset(time: wirely.MonotonicTime, duration: wirely.Duration, sign: int) -> tuple[int, int]:
    return time.generation, time.ticks + sign * duration.ticks


def _saturating(time: wirely.MonotonicTime, duration: wirely.Duration, sign: int) -> wirely.MonotonicTime:
    generation, ticks = _offset(time, duration, sign)
    return wirely.MonotonicTime(generation=generation, ticks=min(max(ticks, 0), _MAX_TICKS))


def _checked(time: wirely.MonotonicTime, duration: wirely.Duration, sign: int) -> wirely.Some[wirely.MonotonicTime] | None:
    generation, ticks = _offset(time, duration, sign)
    if 0 <= ticks <= _MAX_TICKS:
        return wirely.Some(wirely.MonotonicTime(generation=generation, ticks=ticks))
    return None


def saturating_add(time: wirely.MonotonicTime, duration: wirely.Duration) -> wirely.MonotonicTime:
    return _saturating(time, duration, 1)


def saturating_sub(time: wirely.MonotonicTime, duration: wirely.Duration) -> wirely.MonotonicTime:
    return _saturating(time, duration, -1)


def checked_add(time: wirely.MonotonicTime, duration: wirely.Duration) -> wirely.Some[wirely.MonotonicTime] | None:
    return _checked(time, duration, 1)


def checked_sub(time: wirely.MonotonicTime, duration: wirely.Duration) -> wirely.Some[wirely.MonotonicTime] | None:
    return _checked(time, duration, -1)


def checked_duration_since(time: wirely.MonotonicTime, earlier: wirely.MonotonicTime) -> wirely.Some[wirely.Duration] | None:
    if time.generation != earlier.generation or earlier.ticks > time.ticks:
        return None
    return wirely.Some(wirely.Duration(ticks=time.ticks - earlier.ticks))


def checked_less(a: wirely.MonotonicTime, b: wirely.MonotonicTime) -> wirely.Some[bool] | None:
    if a.generation != b.generation:
        return None
    return wirely.Some(a.ticks < b.ticks)
