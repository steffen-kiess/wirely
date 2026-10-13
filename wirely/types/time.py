import time
import fractions
import functools
import re
from dataclasses import dataclass

import wirely
from wirely.util import check_parameters


# A point in time on a monotonic clock. Can be used to wait for a certain time.
# Keeps track of restarts by tracking the generation of the time. Times with
# different generations aren't comparable. A new generation is started whenever
# the value of the monotonic clock gets lost.
# Generation 0 is never current. The type's default value, MonotonicTime(0, 0),
# therefore behaves like a time from a lost generation.
@dataclass(frozen=True, slots=True, init=False, eq=False)
class MonotonicTime:
    generation: int
    time_ns: int

    # Largest generation and timestamp, the range of their uint64 storage.
    MAX_NS = 2**64 - 1

    @check_parameters
    def __init__(self, generation: int, time_ns: int):
        if not 0 <= generation <= MonotonicTime.MAX_NS or not 0 <= time_ns <= MonotonicTime.MAX_NS:
            raise ValueError(f'Monotonic time out of range: generation {generation}, {time_ns} ns')
        object.__setattr__(self, 'generation', generation)
        object.__setattr__(self, 'time_ns', time_ns)

    @staticmethod
    def current_time():
        # Note: Generation 0 is never current.
        # Note: Currently the generation is always 1 because values cannot be stored anyway
        # TODO: Keep track of generation values
        generation = 1
        # Don't use time.monotonic_ns() because it would be unclear which clock is used
        # now_ns = time.monotonic_ns()
        # TODO: Use CLOCK_BOOTTIME instead of CLOCK_MONOTONIC?
        now_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        return MonotonicTime(generation, now_ns)

    # Returns True if time1 and time2 are comparable
    @staticmethod
    def is_comparable(time1, time2):
        return time1.generation == time2.generation

    # Returns 0 if time1==time2, -1 if time1<time2, 1 if time1>time2 and throws if time1 and time2 are not comparable
    @staticmethod
    def compare(time1, time2):
        if time1.generation != time2.generation:
            raise Exception(f'Times {time1} and {time2} are not comparable')
        elif time1.time_ns == time2.time_ns:
            return 0
        elif time1.time_ns < time2.time_ns:
            return -1
        else:
            return 1

    def __repr__(self):
        return f'MonotonicTime(generation={self.generation}, time_ns={self.time_ns})'

    def to_text(self):
        """Format as MonotonicTime(generation, seconds), or MonotonicTime(invalid) for generation 0."""
        if self.generation == 0:
            return 'MonotonicTime(invalid)'
        return f'MonotonicTime({self.generation}, {_seconds_text(self.time_ns)}s)'

    def __add__(self, duration):
        if not isinstance(duration, Duration):
            raise Exception(f'Cannot add Duration and {type(duration)}')
        return MonotonicTime(generation=self.generation, time_ns=self.time_ns + duration.duration_ns)

    def __sub__(self, other):
        if isinstance(other, Duration):
            return MonotonicTime(generation=self.generation, time_ns=self.time_ns - other.duration_ns)
        elif isinstance(other, MonotonicTime):
            if not MonotonicTime.is_comparable(self, other):
                raise Exception(f'MonotonicTimes {self} and {other} are not comparable')
            return Duration(duration_ns=self.time_ns - other.time_ns)
        else:
            raise Exception(f'Cannot subtract MonotonicTime and {type(other)}')

    def __eq__(self, other):
        if not isinstance(other, MonotonicTime):
            return False
        return self.generation == other.generation and self.time_ns == other.time_ns

    def __hash__(self):
        return hash(('MonotonicTime', self.generation, self.time_ns))


def _seconds_text(nanoseconds):
    """Format nanoseconds as decimal seconds without trailing fraction zeros."""
    seconds, fraction = divmod(nanoseconds, 1_000_000_000)
    return f'{seconds}.{fraction:09}'.rstrip('0') if fraction else str(seconds)


# Duration as defined in ISO 8601-1:2019 5.5.2, but cannot contain years or months. Ignores things like DST changes and leap seconds, only stores then number of nanoseconds internally. Cannot be negative.
@functools.total_ordering
@dataclass(frozen=True, slots=True, init=False, eq=False)
class Duration:
    duration_ns: int

    # Largest duration, the range of the uint64 storage.
    MAX_NS = 2**64 - 1

    @check_parameters
    def __init__(self, *, duration_ns: int):
        duration_ns = int(duration_ns)
        if duration_ns < 0:
            raise Exception('Negative durations are not allowed')
        if duration_ns > Duration.MAX_NS:
            raise Exception(f'Durations above {Duration.MAX_NS} nanoseconds are not allowed')

        object.__setattr__(self, 'duration_ns', duration_ns)

    def __repr__(self):
        return f'Duration({self.duration_ns})'

    def to_iso(self):
        """Format in the canonical form accepted by parse(), such as PT2H30M or PT1.5S."""
        minutes, nanoseconds = divmod(self.duration_ns, 60_000_000_000)
        hours, minute = divmod(minutes, 60)
        days, hour = divmod(hours, 24)
        time = ''.join(f'{amount}{unit}' for amount, unit in ((hour, 'H'), (minute, 'M')) if amount)
        if nanoseconds or not (days or time):
            time += f'{_seconds_text(nanoseconds)}S'
        return 'P' + (f'{days}D' if days else '') + (f'T{time}' if time else '')

    def __add__(self, other):
        if not isinstance(other, Duration):
            raise Exception(f'Cannot add Duration and {type(other)}')
        return Duration(duration_ns=self.duration_ns + other.duration_ns)

    def __sub__(self, other):
        if not isinstance(other, Duration):
            raise Exception(f'Cannot subtract Duration and {type(other)}')
        if other.duration_ns > self.duration_ns:
            raise Exception(f'Trying to substract larger from smaller duration, negative durations are not allowed')
        return Duration(duration_ns=self.duration_ns - other.duration_ns)

    # def __pos__(self):
    #     return self

    # def __neg__(self):
    #     return Duration(-self.duration_ns)

    def __eq__(self, other):
        if not isinstance(other, Duration):
            return False
        return self.duration_ns == other.duration_ns

    def __hash__(self):
        return hash(('Duration', self.duration_ns))

    def __lt__(self, other):
        if not isinstance(other, Duration):
            return NotImplemented
        return self.duration_ns < other.duration_ns

    @property
    def total_seconds(self):
        return self.duration_ns / 1_000_000_000

    @staticmethod
    def from_seconds(seconds):
        return Duration(duration_ns=round(seconds * 1_000_000_000))

    # TODO: Parse according to ISO 8601-1:2019
    @staticmethod
    def parse(s):
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
        # Exact decimal seconds, rounded to whole nanoseconds.
        seconds = fractions.Fraction(seconds_parts) if seconds_parts is not None else 0
        total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
        return Duration(duration_ns=round(total_seconds * 1_000_000_000))


# A time within a day without date or timezone, in nanoseconds since midnight.
# 24:00 is the end of the day: it is distinct from 00:00 and ordered after all
# other times. Parsing creates it; wrapping arithmetic only keeps it when
# adding or subtracting whole days.
@functools.total_ordering
@dataclass(frozen=True, slots=True, init=False, eq=False)
class TimeOfDay:
    nanoseconds: int

    # 24:00, the end of the day.
    END_NS = 86_400 * 1_000_000_000

    @check_parameters
    def __init__(self, nanoseconds: int):
        if not 0 <= nanoseconds <= TimeOfDay.END_NS:
            raise ValueError(f'Time of day must be between 00:00 and 24:00, got {nanoseconds} ns')
        object.__setattr__(self, 'nanoseconds', nanoseconds)

    @staticmethod
    def from_parts(hour, minute, second=0, nanosecond=0):
        if not (0 <= minute < 60 and 0 <= second < 60 and 0 <= nanosecond < 1_000_000_000):
            raise ValueError(f'Invalid time of day {hour}:{minute}:{second}.{nanosecond}')
        return TimeOfDay(((hour * 60 + minute) * 60 + second) * 1_000_000_000 + nanosecond)

    @staticmethod
    def parse(s):
        """Parse HH:MM, HH:MM:SS, or HH:MM:SS.fraction with up to nine fraction digits."""
        match = re.fullmatch(r'([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{1,9}))?)?', s)
        if match is None:
            raise ValueError(f'Invalid time of day {s!r}')
        hour, minute, second = int(match[1]), int(match[2]), int(match[3] or 0)
        nanosecond = int((match[4] or '').ljust(9, '0'))
        if hour > 24 or (hour == 24 and (minute, second, nanosecond) != (0, 0, 0)):
            raise ValueError(f'Invalid time of day {s!r}')
        return TimeOfDay.from_parts(hour, minute, second, nanosecond)

    def __str__(self):
        seconds, nanosecond = divmod(self.nanoseconds, 1_000_000_000)
        minutes, second = divmod(seconds, 60)
        hour, minute = divmod(minutes, 60)
        text = f'{hour:02}:{minute:02}'
        if second or nanosecond:
            text += f':{second:02}'
        if nanosecond:
            text += '.' + f'{nanosecond:09}'.rstrip('0')
        return text

    def __repr__(self):
        return f'TimeOfDay({str(self)!r})'

    def __eq__(self, other):
        if not isinstance(other, TimeOfDay):
            return NotImplemented
        return self.nanoseconds == other.nanoseconds

    def __lt__(self, other):
        if not isinstance(other, TimeOfDay):
            return NotImplemented
        return self.nanoseconds < other.nanoseconds

    def __hash__(self):
        return hash(('TimeOfDay', self.nanoseconds))
