"""Resolve and validate native implementations for a program."""

import inspect

from ..functions import get_native_function_by_name
from ..native import find_native_procedure, get_native_node_type_by_name, get_native_resource_type_by_name
from ..program import used_declared_types
from ..types import TypeEnum, TypeStruct
from .contexts import NativeProcedure
from .resources import ResourceType


def validate_native_procedure(native_class, name):
    """Return the NativeProcedure class registered under a name, checking its methods."""
    description = f'{native_class.__name__}:{name}'
    procedure_class = find_native_procedure(native_class, name)
    if procedure_class is None:
        raise ValueError(f'Cannot find native procedure {description!r}')
    if not issubclass(procedure_class, NativeProcedure):
        raise ValueError(f'Native procedure {description!r} must be a NativeProcedure subclass')

    call = inspect.getattr_static(procedure_class, '__call__')
    if not inspect.isfunction(call) or call is NativeProcedure.__call__:
        raise ValueError(f'Native procedure {description!r} must define __call__')
    implementation = inspect.unwrap(call)
    if (inspect.iscoroutinefunction(implementation)
            or inspect.isgeneratorfunction(implementation)
            or inspect.isasyncgenfunction(implementation)):
        raise ValueError(
            f'Native procedure {description!r} must be synchronous and cannot be a generator',
        )
    try:
        inspect.signature(call).bind(object(), context=object())
        inspect.signature(procedure_class).bind(context=object())
    except (TypeError, ValueError) as error:
        raise ValueError(f'Invalid native procedure {description!r}: {error}') from error
    return procedure_class


def link_native_classes(program):
    """Validate all required implementations before constructing native objects.

    Return the native state classes by type name and the procedure classes by procedure reference.
    """
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

    # Native procedures are nested classes; any registered class may hold them.
    methods = set()
    for schedule in program.procs.values():
        for procedure in schedule:
            if procedure.body is None:
                prefix, _, method = procedure.reference.rpartition(':')
                if prefix not in classes:
                    classes[prefix] = get_native_node_type_by_name(prefix)
                methods.add((prefix, method))

    procedures = {f'{prefix}:{method}': validate_native_procedure(classes[prefix], method)
                  for prefix, method in sorted(methods)}
    return classes, procedures


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
    for enum_type in used_declared_types(program):
        name = enum_type.native_name if isinstance(enum_type, TypeEnum) else None
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


def link_native_structs(program):
    """Check each native struct against the runtime's expectations; field names are not compared."""
    from .signatures import describe, matches
    from .structs import native_struct_types
    for struct_type in used_declared_types(program):
        name = struct_type.native_name if isinstance(struct_type, TypeStruct) else None
        if name is None:
            continue
        expected = native_struct_types.get(name)
        if expected is None:
            raise ValueError(f'Cannot find native struct type {name!r}')
        field_types = tuple(struct_type.fields.values())
        if len(field_types) != len(expected.fields):
            raise ValueError(f'Native struct type {name!r} must have {len(expected.fields)} fields, '
                             f'got {len(field_types)}')
        for index, (field_type, signature_type) in enumerate(zip(field_types, expected.fields)):
            if not matches(signature_type, field_type, {}):
                raise ValueError(f'Native struct type {name!r} field {index} has type {field_type}, '
                                 f'expected {describe(signature_type, {})}')


def link_native_signatures(program):
    """Check the native interfaces a program uses against the runtime's signatures."""
    from . import native_interfaces
    from .signatures import Builtin, Option, describe, matches

    def check_keys(where, kind, actual, expected):
        if set(actual) != set(expected):
            raise ValueError(f'{where}: {kind} {sorted(actual)!r} do not match the expected {sorted(expected)!r}')

    def check_type(where, label, signature_type, value_type, bindings):
        if not matches(signature_type, value_type, bindings):
            raise ValueError(f'{where}: {label} has type {value_type}, expected {describe(signature_type, bindings)}')

    def check_uses(where, signature, values):
        check_keys(where, 'uses bindings', values, signature.uses)
        # Each ValueType binding gives the type of the type variable with its key.
        bindings = {key: values[key].value for key, signature_type in signature.uses.items()
                    if signature_type == Builtin('ValueType') and matches(signature_type, values[key].value_type, {})}
        for key, signature_type in signature.uses.items():
            check_type(where, f'uses binding {key!r}', signature_type, values[key].value_type, bindings)
        return bindings

    def check_ports(where, kind, ports, value_types, resource_types, bindings):
        check_keys(where, kind, [*value_types, *resource_types], ports)
        for key, port in ports.items():
            if port.multi:
                raise ValueError(f'{where}: multi port {key!r} is not supported')
            if port.kind == 'resource':
                if resource_types.get(key) != port.type.native_name:
                    actual = resource_types.get(key, 'a value')
                    raise ValueError(f'{where}: {key!r} is {actual}, expected resource {port.type.native_name}')
            elif key not in value_types:
                raise ValueError(f'{where}: {key!r} is a resource, expected a {port.kind}')
            else:
                # Programs store an event of T like a value of Option[T].
                expected = Option(port.type) if port.kind == 'event' else port.type
                check_type(where, repr(key), expected, value_types[key], bindings)

    for node_id, instance in program.instances.items():
        if not instance.has_native_state:
            continue
        signature = native_interfaces.native_state_types.get(instance.type_name)
        if signature is None:
            raise ValueError(f'Cannot find the signature of native state type {instance.type_name!r}')
        where = f'Native state type {instance.type_name!r} of {node_id!r}'
        native_type = program.native_types[instance.type_name]
        bindings = check_uses(where, signature, instance.initializer_values)
        check_ports(where, 'inputs', signature.depends, {}, native_type.initializer_resources, bindings)
        check_ports(where, 'outputs', signature.produces, {}, native_type.resource_outputs, bindings)

    for stage, schedule in program.procs.items():
        for procedure in schedule:
            if procedure.body is not None:
                continue
            signature = native_interfaces.native_procedures.get(procedure.reference)
            if signature is None:
                raise ValueError(f'Cannot find the signature of native procedure {procedure.reference!r}')
            where = f'Native procedure {procedure.reference!r} of {procedure.node_id!r}'
            if stage.name.lower() != signature.stage:
                raise ValueError(f'{where}: runs in stage {stage.name.lower()}, expected {signature.stage}')
            inputs = {name: source.value_type for name, source in procedure.dependencies}
            if len(inputs) != len(procedure.dependencies):
                raise ValueError(f'{where}: multi inputs are not supported')
            outputs = {name: target.variable.var_type for name, target in procedure.outputs.items()}
            resources = program.native_procedures[procedure.reference].resources
            bindings = check_uses(where, signature, procedure.values)
            check_ports(where, 'inputs', signature.depends, inputs, resources, bindings)
            check_ports(where, 'outputs', signature.produces, outputs, {}, bindings)

    for function in program.functions:
        signature = native_interfaces.native_functions.get(function.name)
        if signature is None:
            raise ValueError(f'Cannot find the signature of native function {function.name!r}')
        where = f'Native function {function.name!r}'
        if len(function.parameter_types) != len(signature.parameters):
            raise ValueError(f'{where}: has {len(function.parameter_types)} parameters, '
                             f'expected {len(signature.parameters)}')
        for index, (value_type, signature_type) in enumerate(zip(function.parameter_types, signature.parameters)):
            check_type(where, f'parameter {index}', signature_type, value_type, {})
        check_type(where, 'result', signature.result, function.return_type, {})


def link_native_functions(program):
    """Resolve the implementation of every function in the program's function table."""
    return tuple(get_native_function_by_name(function.name) for function in program.functions)
