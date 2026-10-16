"""Resolve and validate native implementations for a program."""

import inspect

from ..functions import get_native_function_by_name
from ..native import get_native_node_type_by_name, get_native_resource_type_by_name
from ..program import used_enum_types
from .resources import ResourceType


def validate_native_method(native_class, name):
    description = f'{native_class.__name__}.{name}'
    try:
        method = inspect.getattr_static(native_class, name)
    except AttributeError:
        raise ValueError(f'Cannot find native procedure {description!r}') from None

    if type(method) is staticmethod:
        function = method.__func__
        arguments = ()
    elif type(method) is classmethod:
        function = method.__func__
        arguments = (native_class,)
    elif inspect.isfunction(method):
        function = method
        arguments = ()
    else:
        raise ValueError(f'Unsupported native procedure method {description!r}')

    if not inspect.isfunction(function):
        raise ValueError(f'Unsupported native procedure method {description!r}')
    implementation = inspect.unwrap(function)
    if (inspect.iscoroutinefunction(implementation)
            or inspect.isgeneratorfunction(implementation)
            or inspect.isasyncgenfunction(implementation)):
        raise ValueError(
            f'Native procedure {description!r} must be synchronous and cannot be a generator',
        )
    try:
        inspect.signature(function).bind(*arguments, context=object())
    except (TypeError, ValueError) as error:
        raise ValueError(f'Invalid native procedure {description!r}: {error}') from error


def link_native_classes(program):
    """Validate all required implementations before constructing native objects."""
    classes = {}  # By native state type name or stateless procedure prefix.

    def resolve(name, has_native_state):
        if name not in classes:
            classes[name] = get_native_node_type_by_name(name)
        if getattr(classes[name], 'native_node_has_native_state', None) != has_native_state:
            raise ValueError(f'Node type {name!r} requires has_native_state={has_native_state}')

    for node_id in program.native_construction_order:
        instance = program.instances[node_id]
        if instance.has_native_state:
            resolve(instance.type_name, True)

    # Native procedures are static or class methods; any registered class may hold them.
    methods = set()
    for schedule in program.procs.values():
        for procedure in schedule:
            if procedure.body is None:
                prefix, _, method = procedure.reference.rpartition(':')
                if prefix not in classes:
                    classes[prefix] = get_native_node_type_by_name(prefix)
                methods.add((prefix, method))

    for prefix, method in sorted(methods):
        validate_native_method(classes[prefix], method)
    return classes


def link_native_resource_types(program):
    """Resolve the Python type of every resource type in the program's native interfaces."""
    names = set()
    for native_type in program.native_types.values():
        names.update(native_type.initializer_resources.values())
        names.update(native_type.resource_outputs.values())
    for interface in program.native_procedures.values():
        names.update(interface.resources.values())
    return {name: ResourceType(name, get_native_resource_type_by_name(name)) for name in sorted(names)}


def link_native_enums(program):
    """Check each native enum against the runtime's expectations; member names and numbers are not compared."""
    from .enums import native_enum_types
    for enum_type in used_enum_types(program):
        name = enum_type.native_name
        if name is None:
            continue
        expected = native_enum_types.get(name)
        if expected is None:
            raise ValueError(f'Cannot find native enum type {name!r}')
        kind, expected_kind = ('open' if is_open else 'closed' for is_open in (enum_type.is_open, expected.is_open))
        if kind != expected_kind:
            raise ValueError(f'Native enum type {name!r} must be {expected_kind}, got {kind}')
        if str(enum_type.underlying_type) != expected.underlying_type:
            raise ValueError(f'Native enum type {name!r} must have underlying type {expected.underlying_type}, '
                             f'got {enum_type.underlying_type}')
        if not enum_type.is_open and len(enum_type.members) != len(expected.python_type):
            raise ValueError(f'Native enum type {name!r} must have {len(expected.python_type)} members, '
                             f'got {len(enum_type.members)}')


def link_native_functions(program):
    """Resolve the implementation of every function in the program's function table."""
    return tuple(get_native_function_by_name(function.name) for function in program.functions)
