import time
import fractions
import functools
import re
from dataclasses import dataclass
from typing import overload

import wirely
from .integers import UInt64, UInt128

# Time values count units of 2**-64 seconds.
TICKS_PER_SECOND = 2**64


def _ticks_from_seconds(seconds: fractions.Fraction | int | float | str) -> int:
    """Round exact seconds to the nearest unit, ties to even."""
    return round(fractions.Fraction(seconds) * TICKS_PER_SECOND)


def _seconds_text(ticks: int) -> str:
    """Format units as the shortest decimal seconds that round back to them."""
    # 20 fraction digits resolve every unit.
    for digits in range(21):
        scale = 10**digits
        number = round(fractions.Fraction(ticks * scale, TICKS_PER_SECOND))
        if _ticks_from_seconds(fractions.Fraction(number, scale)) == ticks:
            break
    seconds, fraction = divmod(number, scale)
    return f'{seconds}.{fraction:0{digits}}'.rstrip('0') if fraction else str(seconds)


# A point in time on a monotonic clock. Can be used to wait for a certain time.
# Keeps track of restarts by tracking the generation of the time. Times with
# different generations aren't comparable. A new generation is started whenever
# the value of the monotonic clock gets lost.
# Generation 0 is never current. The type's default value, generation 0 and time 0,
# therefore behaves like a time from a lost generation.
@dataclass(frozen=True, slots=True, init=False, eq=False)
class MonotonicTime:
    generation: int
    ticks: int

    # Largest generation and timestamp, the ranges of their uint64 and uint128 storage.
    MAX_GENERATION = 2**64 - 1
    MAX_TICKS = 2**128 - 1

    def __init__(self, *, generation: int, ticks: int):
        if not 0 <= generation <= MonotonicTime.MAX_GENERATION or not 0 <= ticks <= MonotonicTime.MAX_TICKS:
            raise ValueError(f'Monotonic time out of range: generation {generation}, {ticks} units')
        object.__setattr__(self, 'generation', generation)
        object.__setattr__(self, 'ticks', ticks)

    @staticmethod
    def current_time() -> 'MonotonicTime':
        # Note: Generation 0 is never current.
        # Note: Currently the generation is always 1 because values cannot be stored anyway
        # TODO: Keep track of generation values
        generation = 1
        # Don't use time.monotonic_ns() because it would be unclear which clock is used
        # now_ns = time.monotonic_ns()
        # TODO: Use CLOCK_BOOTTIME instead of CLOCK_MONOTONIC?
        now_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        return MonotonicTime(generation=generation, ticks=_ticks_from_seconds(fractions.Fraction(now_ns, 1_000_000_000)))

    # Returns True if time1 and time2 are comparable
    @staticmethod
    def is_comparable(time1: 'MonotonicTime', time2: 'MonotonicTime') -> bool:
        return time1.generation == time2.generation

    # Returns 0 if time1==time2, -1 if time1<time2, 1 if time1>time2 and throws if time1 and time2 are not comparable
    @staticmethod
    def compare(time1: 'MonotonicTime', time2: 'MonotonicTime') -> int:
        if time1.generation != time2.generation:
            raise Exception(f'Times {time1} and {time2} are not comparable')
        elif time1.ticks == time2.ticks:
            return 0
        elif time1.ticks < time2.ticks:
            return -1
        else:
            return 1

    def __repr__(self) -> str:
        return f'MonotonicTime(generation={self.generation}, ticks={self.ticks})'

    @staticmethod
    def from_fields(ticks: UInt128, generation: UInt64) -> 'MonotonicTime':
        """Return the time with the fields of the opaque struct Timer.MonotonicTime."""
        return MonotonicTime(generation=generation.value, ticks=ticks.value)

    def to_fields(self) -> tuple[UInt128, UInt64]:
        return UInt128(self.ticks), UInt64(self.generation)

    def to_text(self) -> str:
        """Format as MonotonicTime(generation, seconds), or MonotonicTime(invalid) for generation 0."""
        if self.generation == 0:
            return 'MonotonicTime(invalid)'
        return f'MonotonicTime({self.generation}, {_seconds_text(self.ticks)}s)'

    def __add__(self, duration: 'Duration') -> 'MonotonicTime':
        if not isinstance(duration, Duration):
            raise Exception(f'Cannot add Duration and {type(duration)}')
        return MonotonicTime(generation=self.generation, ticks=self.ticks + duration.ticks)

    @overload
    def __sub__(self, other: 'Duration') -> 'MonotonicTime': ...

    @overload
    def __sub__(self, other: 'MonotonicTime') -> 'Duration': ...

    def __sub__(self, other: 'Duration | MonotonicTime') -> 'MonotonicTime | Duration':
        if isinstance(other, Duration):
            return MonotonicTime(generation=self.generation, ticks=self.ticks - other.ticks)
        elif isinstance(other, MonotonicTime):
            if not MonotonicTime.is_comparable(self, other):
                raise Exception(f'MonotonicTimes {self} and {other} are not comparable')
            return Duration(ticks=self.ticks - other.ticks)
        else:
            raise Exception(f'Cannot subtract MonotonicTime and {type(other)}')

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MonotonicTime):
            return False
        return self.generation == other.generation and self.ticks == other.ticks

    def __hash__(self) -> int:
        return hash(('MonotonicTime', self.generation, self.ticks))


# TODO: Add type which represents offset to monotonic time and can be used e.g. for KNX messages which contain the current time?


# TODO: Add to_csv_value and make json serializable?
# Duration as defined in ISO 8601-1:2019 5.5.2, but cannot contain years or months. Ignores things like DST changes and leap seconds, only stores the number of 2**-64 s units internally. Cannot be negative.
@functools.total_ordering
@dataclass(frozen=True, slots=True, init=False, eq=False)
class Duration:
    ticks: int

    # Largest duration, the range of the uint128 storage.
    MAX_TICKS = 2**128 - 1

    def __init__(self, *, ticks: int):
        if ticks < 0:
            raise Exception('Negative durations are not allowed')
        if ticks > Duration.MAX_TICKS:
            raise Exception(f'Durations above {Duration.MAX_TICKS} units are not allowed')

        object.__setattr__(self, 'ticks', ticks)

    def __repr__(self) -> str:
        return f'Duration(ticks={self.ticks})'

    @staticmethod
    def from_fields(ticks: UInt128) -> 'Duration':
        """Return the duration with the field of the opaque struct Std.Duration."""
        return Duration(ticks=ticks.value)

    def to_fields(self) -> tuple[UInt128]:
        return (UInt128(self.ticks),)

    def to_text(self) -> str:
        return self.to_iso()

    def to_iso(self) -> str:
        """Format in the canonical form accepted by parse(), such as PT2H30M or PT1.5S."""
        minutes, ticks = divmod(self.ticks, 60 * TICKS_PER_SECOND)
        hours, minute = divmod(minutes, 60)
        days, hour = divmod(hours, 24)
        time = ''.join(f'{amount}{unit}' for amount, unit in ((hour, 'H'), (minute, 'M')) if amount)
        if ticks or not (days or time):
            time += f'{_seconds_text(ticks)}S'
        return 'P' + (f'{days}D' if days else '') + (f'T{time}' if time else '')

    def __add__(self, other: 'Duration') -> 'Duration':
        if not isinstance(other, Duration):
            raise Exception(f'Cannot add Duration and {type(other)}')
        return Duration(ticks=self.ticks + other.ticks)

    def __sub__(self, other: 'Duration') -> 'Duration':
        if not isinstance(other, Duration):
            raise Exception(f'Cannot subtract Duration and {type(other)}')
        if other.ticks > self.ticks:
            raise Exception(f'Trying to substract larger from smaller duration, negative durations are not allowed')
        return Duration(ticks=self.ticks - other.ticks)

    # def __pos__(self):
    #     return self

    # def __neg__(self):
    #     return Duration(ticks=-self.ticks)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Duration):
            return False
        return self.ticks == other.ticks

    def __hash__(self) -> int:
        return hash(('Duration', self.ticks))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Duration):
            return NotImplemented
        return self.ticks < other.ticks

    @property
    def total_seconds(self) -> float:
        return self.ticks / TICKS_PER_SECOND

    @staticmethod
    def from_seconds(seconds: fractions.Fraction | int | float | str) -> 'Duration':
        return Duration(ticks=_ticks_from_seconds(seconds))

    # TODO: Parse according to ISO 8601-1:2019
    @staticmethod
    def parse(s: str) -> 'Duration':
        # Note: Compared To ISO 8601-1:2019
        # - Months and years are not allowed
        # - Only seconds may have a decimal fraction
        # - The decimal sign must be a point
        match = re.fullmatch('P(([0-9]+)D)?(T(([0-9]+)H)?(([0-9]+)M)?(([0-9]+(\\.[0-9]+)?)S)?)?', s)
        if match is None:
            raise Exception(f'Invalid duration string: {s!r}')
        days_part = match[2]
        hours_part = match[5]
        minutes_part = match[7]
        seconds_parts = match[9]

        if days_part is None and hours_part is None and minutes_part is None and seconds_parts is None:
            raise Exception(f'Invalid duration string {s!r}: At least one designator is needed')

        days = int(days_part) if days_part is not None else 0
        hours = int(hours_part) if hours_part is not None else 0
        minutes = int(minutes_part) if minutes_part is not None else 0
        # Exact decimal seconds, rounded to the nearest unit.
        seconds = fractions.Fraction(seconds_parts) if seconds_parts is not None else 0
        total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
        return Duration(ticks=_ticks_from_seconds(total_seconds))


# TODO: Add to_csv_value?
# A time within a day without date or timezone, in 2**-64 s units since midnight.
# 24:00 is the end of the day: it is distinct from 00:00 and ordered after all
# other times. Parsing creates it; wrapping arithmetic only keeps it when
# adding or subtracting whole days.
@functools.total_ordering
@dataclass(frozen=True, slots=True, init=False, eq=False)
class TimeOfDay:
    ticks: int

    # 24:00, the end of the day.
    END_TICKS = 86_400 * TICKS_PER_SECOND

    def __init__(self, *, ticks: int):
        if not 0 <= ticks <= TimeOfDay.END_TICKS:
            raise ValueError(f'Time of day must be between 00:00 and 24:00, got {ticks} units')
        object.__setattr__(self, 'ticks', ticks)

    @staticmethod
    def from_fields(ticks: UInt128) -> 'TimeOfDay':
        """Return the time with the field of the opaque struct Schedule.TimeOfDay."""
        return TimeOfDay(ticks=ticks.value)

    def to_fields(self) -> tuple[UInt128]:
        return (UInt128(self.ticks),)

    def to_text(self) -> str:
        return str(self)

    @staticmethod
    def from_parts(hour: int, minute: int, second: int = 0, ticks: int = 0) -> 'TimeOfDay':
        if not (0 <= minute < 60 and 0 <= second < 60 and 0 <= ticks < TICKS_PER_SECOND):
            raise ValueError(f'Invalid time of day {hour}:{minute}:{second} + {ticks} units')
        return TimeOfDay(ticks=((hour * 60 + minute) * 60 + second) * TICKS_PER_SECOND + ticks)

    @staticmethod
    def parse(s: str) -> 'TimeOfDay':
        """Parse HH:MM, HH:MM:SS, or HH:MM:SS.fraction, rounding to the nearest unit.

        Times before 24:00 stay before it, at the last unit if rounding would reach 24:00."""
        match = re.fullmatch(r'([0-9]{2}):([0-9]{2})(?::([0-9]{2})(\.[0-9]+)?)?', s)
        if match is None:
            raise ValueError(f'Invalid time of day {s!r}')
        hour, minute, second = int(match[1]), int(match[2]), int(match[3] or 0)
        fraction = fractions.Fraction('0' + (match[4] or ''))
        if not (0 <= minute < 60 and 0 <= second < 60) or hour > 24 or (
                hour == 24 and (minute, second, fraction) != (0, 0, 0)):
            raise ValueError(f'Invalid time of day {s!r}')
        ticks = _ticks_from_seconds((hour * 60 + minute) * 60 + second + fraction)
        if hour < 24:
            ticks = min(ticks, TimeOfDay.END_TICKS - 1)
        return TimeOfDay(ticks=ticks)

    def __str__(self) -> str:
        minutes, ticks = divmod(self.ticks, 60 * TICKS_PER_SECOND)
        hour, minute = divmod(minutes, 60)
        text = f'{hour:02}:{minute:02}'
        if ticks:
            seconds = _seconds_text(ticks)
            text += ':' + ('0' + seconds if ticks < 10 * TICKS_PER_SECOND else seconds)
        return text

    def __repr__(self) -> str:
        return f'TimeOfDay({str(self)!r})'

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TimeOfDay):
            return NotImplemented
        return self.ticks == other.ticks

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TimeOfDay):
            return NotImplemented
        return self.ticks < other.ticks

    def __hash__(self) -> int:
        return hash(('TimeOfDay', self.ticks))
