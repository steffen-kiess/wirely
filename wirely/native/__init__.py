import importlib

from wirely.util import check_parameters


@check_parameters
def native_node_type(namespace_dict, *, has_native_state: bool):
    def native_node_type_impl(cls):
        cls_name = cls.__name__

        cls.native_node_has_native_state = has_native_state

        if cls_name in namespace_dict:
            raise Exception(f'Duplicate native node type name {cls_name!r}')
        namespace_dict[cls_name] = cls

        return cls
    return native_node_type_impl


native_node_pkgs = {
    ('Std',): 'std',
    ('Schedule',): 'schedule',
    ('Timer',): 'timer',
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


def get_native_node_type_by_name(type_name: tuple[str, ...]):
    pkg_name = native_node_pkgs.get(type_name[:-1])
    if pkg_name is None:
        raise ValueError(f'No native namespace for node type {".".join(type_name)!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    native_class = pkg.native_node_types.get(type_name[-1])
    if native_class is None:
        raise ValueError(f'Cannot find native class for node type {".".join(type_name)!r}')
    return native_class

