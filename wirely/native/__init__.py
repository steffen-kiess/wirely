import inspect
import importlib
import typing

import wirely as blocks
from wirely.util import check_parameters


@check_parameters
def native_block_type(namespace_dict, *, has_native_state: bool, data_type: typing.Optional[type] = None):
    def native_block_type_impl(cls):
        cls_name = cls.__name__

        cls.native_block_has_native_state = has_native_state
        cls.native_block_data_type = data_type

        if cls_name in namespace_dict:
            raise Exception(f'Duplicate native block name {cls_name!r}')
        namespace_dict[cls_name] = cls

        return cls
    return native_block_type_impl


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


native_block_pkgs = {
    ('Std',): 'std',
    ('Time',): 'time',
    ('KNX',): 'knx',
    ('ABB', 'JRA'): 'abb_jra',
    ('MQTT',): 'mqtt',
    ('CSV',): 'csv',
    ('TwoN',): 'two_n',
    ('GPIO',): 'gpio',
    ('TestLibrary',): 'test_library',
    ('Memory',): 'memory',
    ('ExtProg',): 'ext_prog',
}


@check_parameters
def get_native_block_type(block_type: blocks.BlockType):
    pkg_name = native_block_pkgs.get(block_type.fullname_tuple[:-1], None)
    if pkg_name is None:
        raise Exception(f'Block type {block_type.fullname!r} needs a native class but no native classes found for {block_type.fullname_tuple[:-1]}')

    pkg = importlib.import_module('.' + pkg_name, __name__)
    native_classes = pkg.native_block_types

    native_class = native_classes.get(block_type.name.id_str, None)
    if native_class is None:
        raise Exception(f'Block type {block_type.fullname!r} needs a native class but cannot find one')

    return native_class
