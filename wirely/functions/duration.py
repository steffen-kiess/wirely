"""Duration arithmetic on 2**-64 s units, limited to 0 ... Duration.MAX_TICKS."""

import fractions

from wirely import Duration, Some, UInt32, UInt64, UInt128
from wirely.types.time import TICKS_PER_SECOND


def less(a: Duration, b: Duration) -> bool:
    return a.ticks < b.ticks


def _saturating(ticks: int) -> Duration:
    return Duration(ticks=min(max(ticks, 0), Duration.MAX_TICKS))


def _checked(ticks: int) -> Some[Duration] | None:
    if 0 <= ticks <= Duration.MAX_TICKS:
        return Some(Duration(ticks=ticks))
    return None


def saturating_add(a: Duration, b: Duration) -> Duration:
    return _saturating(a.ticks + b.ticks)


def saturating_sub(a: Duration, b: Duration) -> Duration:
    return _saturating(a.ticks - b.ticks)


def saturating_mul(duration: Duration, factor: UInt32) -> Duration:
    return _saturating(duration.ticks * factor.value)


def checked_add(a: Duration, b: Duration) -> Some[Duration] | None:
    return _checked(a.ticks + b.ticks)


def checked_sub(a: Duration, b: Duration) -> Some[Duration] | None:
    return _checked(a.ticks - b.ticks)


def checked_mul(duration: Duration, factor: UInt32) -> Some[Duration] | None:
    return _checked(duration.ticks * factor.value)


def checked_div(duration: Duration, divisor: UInt32) -> Some[Duration] | None:
    if divisor.value == 0:
        return None
    return _checked(duration.ticks // divisor.value)


# Conversions to and from integer counts of a unit. Construction rounds to
# the nearest 2**-64 s, and the input types keep it within the range.
# Extraction returns the largest count whose constructed duration does not
# exceed the argument, so it inverts construction.
_MILLIS, _MICROS, _NANOS = 1_000, 1_000_000, 1_000_000_000  # Counts per second.


def _from_count(count: int, per_second: int) -> int:
    return round(fractions.Fraction(count * TICKS_PER_SECOND, per_second))


def _as_count(duration: Duration, per_second: int) -> int:
    count = duration.ticks * per_second // TICKS_PER_SECOND
    if _from_count(count + 1, per_second) <= duration.ticks:
        count += 1
    return count


def from_secs(secs: UInt32) -> Duration:
    return Duration(ticks=_from_count(secs.value, 1))


def from_millis(millis: UInt32) -> Duration:
    return Duration(ticks=_from_count(millis.value, _MILLIS))


def from_micros(micros: UInt32) -> Duration:
    return Duration(ticks=_from_count(micros.value, _MICROS))


def from_nanos(nanos: UInt64) -> Duration:
    return Duration(ticks=_from_count(nanos.value, _NANOS))


def as_secs(duration: Duration) -> UInt64:
    return UInt64(_as_count(duration, 1))


def as_millis(duration: Duration) -> UInt128:
    return UInt128(_as_count(duration, _MILLIS))


def as_micros(duration: Duration) -> UInt128:
    return UInt128(_as_count(duration, _MICROS))


def as_nanos(duration: Duration) -> UInt128:
    return UInt128(_as_count(duration, _NANOS))
