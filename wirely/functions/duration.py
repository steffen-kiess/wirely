"""Duration arithmetic on nanoseconds, limited to 0 ... Duration.MAX_NS."""

import wirely
from wirely.functions import native_function

native_functions = {}


def _saturating(nanoseconds):
    return wirely.Duration(duration_ns=min(max(nanoseconds, 0), wirely.Duration.MAX_NS))


def _checked(nanoseconds):
    if 0 <= nanoseconds <= wirely.Duration.MAX_NS:
        return wirely.Some(wirely.Duration(duration_ns=nanoseconds))
    return None


def _sum(context):
    return context.arguments['a'].duration_ns + context.arguments['b'].duration_ns


def _difference(context):
    return context.arguments['a'].duration_ns - context.arguments['b'].duration_ns


def _product(context):
    return context.arguments['duration'].duration_ns * context.arguments['factor']


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
    return _checked(context.arguments['duration'].duration_ns // divisor)



# Conversions to and from integer counts of a unit. The input types keep
# construction within the range; extraction rounds down.
_SECOND_NS, _MILLISECOND_NS, _MICROSECOND_NS = 1_000_000_000, 1_000_000, 1_000


@native_function(native_functions, 'FromSecs')
def from_secs(*, context):
    return wirely.Duration(duration_ns=context.arguments['secs'] * _SECOND_NS)


@native_function(native_functions, 'FromMillis')
def from_millis(*, context):
    return wirely.Duration(duration_ns=context.arguments['millis'] * _MILLISECOND_NS)


@native_function(native_functions, 'FromMicros')
def from_micros(*, context):
    return wirely.Duration(duration_ns=context.arguments['micros'] * _MICROSECOND_NS)


@native_function(native_functions, 'FromNanos')
def from_nanos(*, context):
    return wirely.Duration(duration_ns=context.arguments['nanos'])


@native_function(native_functions, 'AsSecs')
def as_secs(*, context):
    return context.arguments['duration'].duration_ns // _SECOND_NS


@native_function(native_functions, 'AsMillis')
def as_millis(*, context):
    return context.arguments['duration'].duration_ns // _MILLISECOND_NS


@native_function(native_functions, 'AsMicros')
def as_micros(*, context):
    return context.arguments['duration'].duration_ns // _MICROSECOND_NS


@native_function(native_functions, 'AsNanos')
def as_nanos(*, context):
    return context.arguments['duration'].duration_ns
