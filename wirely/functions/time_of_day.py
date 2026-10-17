"""Time-of-day arithmetic, wrapping at midnight."""

import wirely


_DAY_TICKS = wirely.TimeOfDay.END_TICKS


def less(a: wirely.TimeOfDay, b: wirely.TimeOfDay) -> bool:
    return a.ticks < b.ticks


def _wrap(time: wirely.TimeOfDay, offset: int) -> wirely.TimeOfDay:
    # Whole days leave every time unchanged, including 24:00. Other results
    # lie before 24:00.
    if offset % _DAY_TICKS == 0:
        return time
    return wirely.TimeOfDay(ticks=(time.ticks + offset) % _DAY_TICKS)


def wrapping_add(time: wirely.TimeOfDay, duration: wirely.Duration) -> wirely.TimeOfDay:
    return _wrap(time, duration.ticks)


def wrapping_sub(time: wirely.TimeOfDay, duration: wirely.Duration) -> wirely.TimeOfDay:
    return _wrap(time, -duration.ticks)


def duration_until(start: wirely.TimeOfDay, end: wirely.TimeOfDay) -> wirely.Duration:
    difference = end.ticks - start.ticks
    if difference < 0:
        difference += _DAY_TICKS
    return wirely.Duration(ticks=difference)
