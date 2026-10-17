"""Duration arithmetic on 2**-64 s units, limited to 0 ... Duration.MAX_TICKS."""

import fractions
from collections.abc import Callable

import wirely
from wirely.functions import RuntimeFunctionContext, native_function
from wirely.types.time import TICKS_PER_SECOND

native_functions: dict[str, Callable[..., object]] = {}


@native_function(native_functions, 'Less')
def less(*, context: RuntimeFunctionContext) -> object:
    a, b = context.arguments
    return a.ticks < b.ticks


def _saturating(ticks: int) -> wirely.Duration:
    return wirely.Duration(ticks=min(max(ticks, 0), wirely.Duration.MAX_TICKS))


def _checked(ticks: int) -> wirely.Some | None:
    if 0 <= ticks <= wirely.Duration.MAX_TICKS:
        return wirely.Some(wirely.Duration(ticks=ticks))
    return None


def _sum(a: wirely.Duration, b: wirely.Duration) -> int:
    return a.ticks + b.ticks


def _difference(a: wirely.Duration, b: wirely.Duration) -> int:
    return a.ticks - b.ticks


def _product(duration: wirely.Duration, factor: int) -> int:
    return duration.ticks * factor


@native_function(native_functions, 'SaturatingAdd')
def saturating_add(*, context: RuntimeFunctionContext) -> object:
    a, b = context.arguments
    return _saturating(_sum(a, b))


@native_function(native_functions, 'SaturatingSub')
def saturating_sub(*, context: RuntimeFunctionContext) -> object:
    a, b = context.arguments
    return _saturating(_difference(a, b))


@native_function(native_functions, 'SaturatingMul')
def saturating_mul(*, context: RuntimeFunctionContext) -> object:
    duration, factor = context.arguments
    return _saturating(_product(duration, factor))


@native_function(native_functions, 'CheckedAdd')
def checked_add(*, context: RuntimeFunctionContext) -> object:
    a, b = context.arguments
    return _checked(_sum(a, b))


@native_function(native_functions, 'CheckedSub')
def checked_sub(*, context: RuntimeFunctionContext) -> object:
    a, b = context.arguments
    return _checked(_difference(a, b))


@native_function(native_functions, 'CheckedMul')
def checked_mul(*, context: RuntimeFunctionContext) -> object:
    duration, factor = context.arguments
    return _checked(_product(duration, factor))


@native_function(native_functions, 'CheckedDiv')
def checked_div(*, context: RuntimeFunctionContext) -> object:
    duration, divisor = context.arguments
    if divisor == 0:
        return None
    return _checked(duration.ticks // divisor)




# Conversions to and from integer counts of a unit. Construction rounds to
# the nearest 2**-64 s, and the input types keep it within the range.
# Extraction returns the largest count whose constructed duration does not
# exceed the argument, so it inverts construction.
_MILLIS, _MICROS, _NANOS = 1_000, 1_000_000, 1_000_000_000  # Counts per second.


def _from_count(count: int, per_second: int) -> int:
    return round(fractions.Fraction(count * TICKS_PER_SECOND, per_second))


def _as_count(duration: wirely.Duration, per_second: int) -> int:
    count = duration.ticks * per_second // TICKS_PER_SECOND
    if _from_count(count + 1, per_second) <= duration.ticks:
        count += 1
    return count


@native_function(native_functions, 'FromSecs')
def from_secs(*, context: RuntimeFunctionContext) -> object:
    secs, = context.arguments
    return wirely.Duration(ticks=_from_count(secs, 1))


@native_function(native_functions, 'FromMillis')
def from_millis(*, context: RuntimeFunctionContext) -> object:
    millis, = context.arguments
    return wirely.Duration(ticks=_from_count(millis, _MILLIS))


@native_function(native_functions, 'FromMicros')
def from_micros(*, context: RuntimeFunctionContext) -> object:
    micros, = context.arguments
    return wirely.Duration(ticks=_from_count(micros, _MICROS))


@native_function(native_functions, 'FromNanos')
def from_nanos(*, context: RuntimeFunctionContext) -> object:
    nanos, = context.arguments
    return wirely.Duration(ticks=_from_count(nanos, _NANOS))


@native_function(native_functions, 'AsSecs')
def as_secs(*, context: RuntimeFunctionContext) -> object:
    duration, = context.arguments
    return _as_count(duration, 1)


@native_function(native_functions, 'AsMillis')
def as_millis(*, context: RuntimeFunctionContext) -> object:
    duration, = context.arguments
    return _as_count(duration, _MILLIS)


@native_function(native_functions, 'AsMicros')
def as_micros(*, context: RuntimeFunctionContext) -> object:
    duration, = context.arguments
    return _as_count(duration, _MICROS)


@native_function(native_functions, 'AsNanos')
def as_nanos(*, context: RuntimeFunctionContext) -> object:
    duration, = context.arguments
    return _as_count(duration, _NANOS)
