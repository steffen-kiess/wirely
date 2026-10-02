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

        native_procedures = {}
        for name, value in cls.__dict__.items():
            if not inspect.isfunction(value):
                continue
            if not hasattr(value, 'is_native_procedure') or not value.is_native_procedure:
                continue

            if not has_native_state and not isinstance(inspect.getattr_static(cls, name), staticmethod):
                raise ValueError(f'Native procedure {name!r} on {cls_name!r} must be static when has_native_state=False')

            if name in native_procedures:
                raise Exception(f'Duplicate native procedure name {name!r} in block {cls_name!r}')
            native_procedures[name] = value
        cls.native_procedures = native_procedures

        if cls_name in namespace_dict:
            raise Exception(f'Duplicate native block name {cls_name!r}')
        namespace_dict[cls_name] = cls

        return cls
    return native_block_type_impl


def native_procedure(proc):
    # TODO: Check parameters of procedure?
    proc.is_native_procedure = True
    return proc


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
