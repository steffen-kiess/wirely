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


@check_parameters
def native_resource_type(namespace_dict, name: str):
    """Register a class as the Python type of a resource type in a module registry."""
    def native_resource_type_impl(cls):
        if name in namespace_dict:
            raise Exception(f'Duplicate native resource type name {name!r}')
        namespace_dict[name] = cls
        return cls
    return native_resource_type_impl


native_node_pkgs = {
    'Std': 'std',
    'Schedule': 'schedule',
    'Timer': 'timer',
    'KNX': 'knx',
    'ABB.JRA': 'abb_jra',
    'MQTT': 'mqtt',
    'CSV': 'csv',
    'TwoN': 'two_n',
    'GPIO': 'gpio',
    'TestLibrary': 'test_library',
    'Memory': 'memory',
    'Process': 'process',
}


def get_native_node_type_by_name(type_name: str):
    namespace, _, name = type_name.rpartition('.')
    pkg_name = native_node_pkgs.get(namespace)
    if pkg_name is None:
        raise ValueError(f'No native namespace for node type {type_name!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    native_class = pkg.native_node_types.get(name)
    if native_class is None:
        raise ValueError(f'Cannot find native class for node type {type_name!r}')
    return native_class



def get_native_resource_type_by_name(type_name: str):
    """Return the Python type of the resource type with this native name."""
    namespace, _, name = type_name.rpartition('.')
    pkg_name = native_node_pkgs.get(namespace)
    if pkg_name is None:
        raise ValueError(f'No native namespace for resource type {type_name!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    python_type = getattr(pkg, 'native_resource_types', {}).get(name)
    if python_type is None:
        raise ValueError(f'Cannot find native resource type {type_name!r}')
    return python_type
