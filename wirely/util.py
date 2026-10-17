import functools
import inspect
import os
import types
import typing
from collections.abc import Callable

P = typing.ParamSpec('P')
R = typing.TypeVar('R')

# Arguments of these types are converted from subclasses, such as lark tokens, to the exact type.
_EXACT_TYPES = (str, int, bool)


class _Mismatch(Exception):
    """A value does not match an annotation."""


def _checker(annotation: typing.Any):
    """Return a function that returns a value matching an annotation, possibly converted.

    The function raises _Mismatch for other values.
    """
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if origin is None and isinstance(annotation, type):
        exact: typing.Any = annotation if annotation in _EXACT_TYPES else None

        def check_class(value):
            if not isinstance(value, annotation):
                raise _Mismatch
            if exact is not None and type(value) is not exact:
                return exact(value)
            return value
        return check_class
    if origin in (typing.Union, types.UnionType):
        members = [_checker(member) for member in arguments]

        def check_union(value):
            for member in members:
                try:
                    return member(value)
                except _Mismatch:
                    pass
            raise _Mismatch
        return check_union
    if origin is list and len(arguments) == 1:
        check_item = _checker(arguments[0])

        def check_list(value):
            if not isinstance(value, list):
                raise _Mismatch
            return [check_item(item) for item in value]
        return check_list
    if origin is tuple and len(arguments) == 2 and arguments[1] is Ellipsis:
        check_element = _checker(arguments[0])

        def check_variable_tuple(value):
            if not isinstance(value, tuple):
                raise _Mismatch
            return tuple(check_element(item) for item in value)
        return check_variable_tuple
    if origin is tuple:
        check_items = [_checker(argument) for argument in arguments]

        def check_tuple(value):
            if not isinstance(value, tuple) or len(value) != len(check_items):
                raise _Mismatch
            return tuple(check(item) for check, item in zip(check_items, value))
        return check_tuple
    raise TypeError(f'Unsupported parameter annotation {annotation!r}')


def check_parameters(f: Callable[P, R]) -> Callable[P, R]:
    """Check the arguments of a function against its parameter annotations when it is called.

    Arguments of str, int, or bool parameters are converted to the exact type. *args and **kwargs are
    not checked.
    """
    annotations = inspect.get_annotations(f)
    checks = {}
    positional = []  # Names of the parameters that positional arguments fill, in order.
    for parameter in inspect.signature(f).parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
            positional.append(parameter.name)
        if parameter.name in annotations:
            checks[parameter.name] = (_checker(annotations[parameter.name]), annotations[parameter.name])

    def check(name, value):
        checker, annotation = checks[name]
        try:
            return checker(value)
        except _Mismatch:
            expected = annotation.__name__ if isinstance(annotation, type) else str(annotation)
            raise TypeError(f'Got incorrect type for argument {name!r} of {f.__qualname__}: '
                            f'got {type(value).__name__}, expected {expected}') from None

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        arguments = list(args)
        for index, name in enumerate(positional[:len(arguments)]):
            if name in checks:
                arguments[index] = check(name, arguments[index])
        keywords = {name: check(name, value) if name in checks else value for name, value in kwargs.items()}
        return f(*arguments, **keywords)
    return typing.cast(Callable[P, R], wrapper)


def getEnvironmentBool(name, defVal):
    if type(defVal) != bool:
        raise Exception('type(defVal) != bool')

    if not name in os.environ:
        return defVal
    value = os.environ[name]
    s = value.lower()
    if s == '':
        return defVal
    elif s == '0' or s == 'false':
        return False
    elif s == '1' or s == 'true':
        return True
    else:
        raise Exception('Invalid value for environment variant %s: %s' % (repr(name), repr(value)))
