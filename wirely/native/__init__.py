import importlib

from wirely.util import check_parameters


@check_parameters
def native_block_type(namespace_dict, *, has_native_state: bool):
    def native_block_type_impl(cls):
        cls_name = cls.__name__

        cls.native_block_has_native_state = has_native_state

        if cls_name in namespace_dict:
            raise Exception(f'Duplicate native block name {cls_name!r}')
        namespace_dict[cls_name] = cls

        return cls
    return native_block_type_impl


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
    ('Process',): 'process',
}


def get_native_block_type_by_name(type_name: tuple[str, ...]):
    pkg_name = native_block_pkgs.get(type_name[:-1])
    if pkg_name is None:
        raise ValueError(f'No native namespace for node type {".".join(type_name)!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    native_class = pkg.native_block_types.get(type_name[-1])
    if native_class is None:
        raise ValueError(f'Cannot find native class for node type {".".join(type_name)!r}')
    return native_class


def get_native_function_by_name(function_name: tuple[str, ...]):
    """Return the runtime implementation of a native function, validated for calling."""
    from .functions import validate_native_function
    description = '.'.join(function_name)
    pkg_name = native_block_pkgs.get(function_name[:-1])
    if pkg_name is None:
        raise ValueError(f'No native namespace for function {description!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    implementation = getattr(pkg, 'native_functions', {}).get(function_name[-1])
    if implementation is None:
        raise ValueError(f'Cannot find native function {description!r}')
    validate_native_function(implementation, function_name)
    return implementation
