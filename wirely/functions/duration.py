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
