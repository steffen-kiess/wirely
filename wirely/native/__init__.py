import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from wirely.util import check_parameters

if TYPE_CHECKING:
    from wirely.types.value_types import ValueType

C = TypeVar('C', bound=type)


@check_parameters
def native_node_type(namespace_dict: dict, *, has_native_state: bool) -> Callable[[C], C]:
    def native_node_type_impl(cls: C) -> C:
        cls_name = cls.__name__

        setattr(cls, 'native_node_has_native_state', has_native_state)

        procedure_names: set[str] = set()
        for value in vars(cls).values():
            name = vars(value).get('native_procedure_name') if isinstance(value, type) else None
            if name in procedure_names:
                raise Exception(f'Duplicate native procedure name {name!r} in {cls_name!r}')
            if name is not None:
                procedure_names.add(name)

        if cls_name in namespace_dict:
            raise Exception(f'Duplicate native node type name {cls_name!r}')
        namespace_dict[cls_name] = cls

        return cls
    return native_node_type_impl


@check_parameters
def native_procedure(name: str) -> Callable[[C], C]:
    """Register a NativeProcedure class nested in a native node type class under a procedure name."""
    def native_procedure_impl(cls: C) -> C:
        setattr(cls, 'native_procedure_name', name)
        return cls
    return native_procedure_impl


def find_native_procedure(native_class: type, name: str) -> type | None:
    """Return the procedure class registered under a name in a class or its bases, or None."""
    for base in native_class.__mro__:
        for value in vars(base).values():
            if isinstance(value, type) and vars(value).get('native_procedure_name') == name:
                return value
    return None


def check_state_type(name: str, procedure_type: 'ValueType[Any]', state_type: 'ValueType[Any]') -> None:
    """Raise ValueError unless a procedure's ValueType matches its native state's; call it in prepare()."""
    if procedure_type != state_type:
        raise ValueError(f'{name}: procedure type {procedure_type} does not match state type {state_type}')


@check_parameters
def native_resource_type(namespace_dict: dict, name: str) -> Callable[[C], C]:
    """Register a class as the Python type of a resource type in a module registry."""
    def native_resource_type_impl(cls: C) -> C:
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
    'MQTT': 'mqtt',
    'CSV': 'csv',
    'TwoN': 'two_n',
    'GPIO': 'gpio',
    'TestLibrary': 'test_library',
    'Process': 'process',
}


def get_native_node_type_by_name(type_name: str) -> type:
    namespace, _, name = type_name.rpartition('.')
    pkg_name = native_node_pkgs.get(namespace)
    if pkg_name is None:
        raise ValueError(f'No native namespace for node type {type_name!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    native_class = pkg.native_node_types.get(name)
    if native_class is None:
        raise ValueError(f'Cannot find native class for node type {type_name!r}')
    return native_class



def get_native_resource_type_by_name(type_name: str) -> type:
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
