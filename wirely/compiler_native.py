"""Native lookup and signature checks for compilation."""

import inspect

from . import compiler_model as blocks
from .native import get_native_block_type_by_name


def _validate_native_procedure(native_class, procedure, *, has_native_state):
    name = procedure.python_name
    source = procedure.alias or procedure.name
    try:
        method = inspect.getattr_static(native_class, name)
    except AttributeError:
        raise blocks.ProgramError(
            source, f'Cannot find native procedure {name!r} in native block type {native_class.__name__!r}',
        ) from None

    if type(method) is staticmethod:
        function = method.__func__
        arguments = ()
    elif type(method) is classmethod:
        function = method.__func__
        arguments = (native_class,)
    elif inspect.isfunction(method):
        function = method
        arguments = (object(),) if has_native_state else ()
    else:
        raise blocks.ProgramError(source, f'Unsupported native procedure method {name!r}')

    if not inspect.isfunction(function):
        raise blocks.ProgramError(source, f'Unsupported native procedure method {name!r}')
    try:
        implementation = inspect.unwrap(function)
        if (inspect.iscoroutinefunction(implementation)
                or inspect.isgeneratorfunction(implementation)
                or inspect.isasyncgenfunction(implementation)):
            raise blocks.ProgramError(source, f'Native procedure {name!r} must be synchronous and cannot be a generator')
        inspect.signature(function).bind(*arguments, context=object())
    except (TypeError, ValueError) as error:
        raise blocks.ProgramError(source, f'Invalid signature for native procedure {name!r}: {error}') from error


def get_native_block_type(block_type):
    return get_native_block_type_by_name(block_type.fullname_tuple)
