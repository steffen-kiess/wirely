import time
import functools
import re
from dataclasses import dataclass

import wirely as blocks
from wirely.util import check_parameters


# A point in time on a monotonic clock. Can be used to wait for a certain time.
# Keeps track of restarts by tracking the generation of the time. Times with
# different generations aren't comparable. A new generation is started whenever
# the value of the monotonic clock gets lost.
# TODO: Remove invalid value and use Option[MonotonicTime] for those cases instead? (At least if invalid means "never")
# Maybe: Allow creating MonotonicTime value which represent +inf? Should these be comparable to all other values?
@dataclass(frozen=True, slots=True, init=False, eq=False)
class MonotonicTime:
    generation: int
    time_ns: int

    @check_parameters
    def __init__(self, generation: int, time_ns: int):
        object.__setattr__(self, 'generation', generation)
        object.__setattr__(self, 'time_ns', time_ns)

    # Value which is not comparable to any other value, can be used as initializer for variables.
    @staticmethod
    def create_invalid():
        return MonotonicTime(0, 0)

    @property
    def is_invalid(self):
        return self.generation == 0 and self.time_ns == 0

    @property
    def is_valid(self):
        return not self.is_invalid

    @staticmethod
    def current_time():
        # Note: Generation 0 is reserved for the invalid value.
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
        if self.is_invalid:
            return 'MonotonicTime.invalid'
        return f'MonotonicTime(generation={self.generation}, time_ns={self.time_ns})'

    def __add__(self, duration):
        if not isinstance(duration, Duration):
            raise Exception(f'Cannot add Duration and {type(duration)}')
        if self.is_invalid:
            return self
        return MonotonicTime(generation=self.generation, time_ns=self.time_ns + duration.duration_ns)

    def __sub__(self, other):
        if isinstance(other, Duration):
            if self.is_invalid:
                return self
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


# Duration as defined in ISO 8601-1:2019 5.5.2, but cannot contain years or months. Ignores things like DST changes and leap seconds, only stores then number of nanoseconds internally. Cannot be negative.
@functools.total_ordering
@dataclass(frozen=True, slots=True, init=False, eq=False)
class Duration:
    duration_ns: int

    @check_parameters
    def __init__(self, *, duration_ns: int):
        duration_ns = int(duration_ns)
        if duration_ns < 0:
            raise Exception('Negative durations are not allowed')

        object.__setattr__(self, 'duration_ns', duration_ns)

    def __repr__(self):
        return f'Duration({self.duration_ns})'

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
        # TODO: Avoid float here?
        seconds = float(seconds_parts) if seconds_parts is not None else 0.0
        return Duration.from_seconds(days * 86400 + hours * 3600 + minutes * 60 + seconds)
