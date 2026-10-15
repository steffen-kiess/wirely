"""Duration arithmetic on 2**-64 s units, limited to 0 ... Duration.MAX_TICKS."""

import fractions

import wirely
from wirely.functions import native_function
from wirely.types.time import TICKS_PER_SECOND

native_functions = {}


@native_function(native_functions, 'Less')
def less(*, context):
    return context.arguments['a'].ticks < context.arguments['b'].ticks


def _saturating(ticks):
    return wirely.Duration(ticks=min(max(ticks, 0), wirely.Duration.MAX_TICKS))


def _checked(ticks):
    if 0 <= ticks <= wirely.Duration.MAX_TICKS:
        return wirely.Some(wirely.Duration(ticks=ticks))
    return None


def _sum(context):
    return context.arguments['a'].ticks + context.arguments['b'].ticks


def _difference(context):
    return context.arguments['a'].ticks - context.arguments['b'].ticks


def _product(context):
    return context.arguments['duration'].ticks * context.arguments['factor']


@native_function(native_functions, 'SaturatingAdd')
def saturating_add(*, context):
    return _saturating(_sum(context))


@native_function(native_functions, 'SaturatingSub')
def saturating_sub(*, context):
    return _saturating(_difference(context))


@native_function(native_functions, 'SaturatingMul')
def saturating_mul(*, context):
    return _saturating(_product(context))


@native_function(native_functions, 'CheckedAdd')
def checked_add(*, context):
    return _checked(_sum(context))


@native_function(native_functions, 'CheckedSub')
def checked_sub(*, context):
    return _checked(_difference(context))


@native_function(native_functions, 'CheckedMul')
def checked_mul(*, context):
    return _checked(_product(context))


@native_function(native_functions, 'CheckedDiv')
def checked_div(*, context):
    divisor = context.arguments['divisor']
    if divisor == 0:
        return None
    return _checked(context.arguments['duration'].ticks // divisor)




# Conversions to and from integer counts of a unit. Construction rounds to
# the nearest 2**-64 s, and the input types keep it within the range.
# Extraction returns the largest count whose constructed duration does not
# exceed the argument, so it inverts construction.
_MILLIS, _MICROS, _NANOS = 1_000, 1_000_000, 1_000_000_000  # Counts per second.


def _from_count(count, per_second):
    return round(fractions.Fraction(count * TICKS_PER_SECOND, per_second))


def _as_count(duration, per_second):
    count = duration.ticks * per_second // TICKS_PER_SECOND
    if _from_count(count + 1, per_second) <= duration.ticks:
        count += 1
    return count


@native_function(native_functions, 'FromSecs')
def from_secs(*, context):
    return wirely.Duration(ticks=_from_count(context.arguments['secs'], 1))


@native_function(native_functions, 'FromMillis')
def from_millis(*, context):
    return wirely.Duration(ticks=_from_count(context.arguments['millis'], _MILLIS))


@native_function(native_functions, 'FromMicros')
def from_micros(*, context):
    return wirely.Duration(ticks=_from_count(context.arguments['micros'], _MICROS))


@native_function(native_functions, 'FromNanos')
def from_nanos(*, context):
    return wirely.Duration(ticks=_from_count(context.arguments['nanos'], _NANOS))


@native_function(native_functions, 'AsSecs')
def as_secs(*, context):
    return _as_count(context.arguments['duration'], 1)


@native_function(native_functions, 'AsMillis')
def as_millis(*, context):
    return _as_count(context.arguments['duration'], _MILLIS)


@native_function(native_functions, 'AsMicros')
def as_micros(*, context):
    return _as_count(context.arguments['duration'], _MICROS)


@native_function(native_functions, 'AsNanos')
def as_nanos(*, context):
    return _as_count(context.arguments['duration'], _NANOS)
