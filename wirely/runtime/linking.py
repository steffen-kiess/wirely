"""Resolve and validate native implementations for a program."""

import inspect

from ..functions import get_native_function_by_name
from ..native import get_native_block_type_by_name


def validate_native_method(native_class, name, *, has_native_state):
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
        arguments = (object(),) if has_native_state else ()
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
    classes = {}
    state_modes = {}

    def resolve(instance):
        name = instance.type_name
        if name not in classes:
            classes[name] = get_native_block_type_by_name(name)
        native_class = classes[name]
        if getattr(native_class, 'native_block_has_native_state', None) != instance.has_native_state:
            raise ValueError(
                f'Node type {".".join(name)!r} requires has_native_state={instance.has_native_state}',
            )
        state_modes[name] = instance.has_native_state

    for node_id in program.native_construction_order:
        instance = program.instances[node_id]
        if instance.has_native_state:
            resolve(instance)

    methods = set()
    for schedule in program.procs.values():
        for procedure in schedule:
            if procedure.body is None:
                resolve(procedure.node_context)
                methods.add(procedure.reference)

    for type_name, method_name in sorted(methods):
        validate_native_method(
            classes[type_name], method_name,
            has_native_state=state_modes[type_name],
        )
    return classes


def link_native_functions(program):
    """Resolve the implementation of every function in the program's function table."""
    return tuple(get_native_function_by_name(function.name) for function in program.functions)
