"""Resolve and validate native implementations for a program."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from ..functions import bind_native_function, get_native_function_by_name
from ..native import find_native_procedure, get_native_node_type_by_name, get_native_resource_type_by_name
from ..program import Constant, Program, used_declared_types
from ..types import Type, TypeEnum, TypeStruct
from .native_api import InitializerAdapter, NativeProcedure, NativeState, ProcedureAdapter
from .procedures import NativeFunction
from .resources import ResourceType

if TYPE_CHECKING:
    from .signatures import Initializer, Port, Procedure as SignatureProcedure, SignatureType


class LinkedState(NamedTuple):
    """A native state class with the adapter and signature of its contract."""

    implementation: type[NativeState[Any]]
    adapter: InitializerAdapter
    signature: Initializer


class LinkedProcedure(NamedTuple):
    """A native procedure class with the adapter and signature of its contract."""

    implementation: type[NativeProcedure]
    adapter: ProcedureAdapter
    signature: SignatureProcedure


def validate_native_procedure(native_class: type, name: str) -> type[NativeProcedure]:
    """Return the NativeProcedure class registered under a name."""
    description = f'{native_class.__name__}:{name}'
    procedure_class = find_native_procedure(native_class, name)
    if procedure_class is None:
        raise ValueError(f'Cannot find native procedure {description!r}')
    if not issubclass(procedure_class, NativeProcedure):
        raise ValueError(f'Native procedure {description!r} must be a NativeProcedure subclass')
    return procedure_class


def resolve_contract(where: str, location: str | None) -> object:
    """Return the contract at a 'module:attribute' location of a native interface."""
    if location is None:
        raise ValueError(f'{where} has no contract')
    module, _, attribute = location.partition(':')
    try:
        return getattr(importlib.import_module(module), attribute)
    except (ImportError, AttributeError) as error:
        raise ValueError(f'{where}: cannot load contract {location!r}: {error}') from error


def check_implementation(description: str, implementation: type, contract: object, methods: Iterable[str]) -> None:
    """Check that a class implements a contract with synchronous methods and can be constructed."""
    assert isinstance(contract, type)
    if not issubclass(implementation, contract):
        raise ValueError(f'{description} must derive from {contract.__module__}.{contract.__qualname__}')
    if inspect.isabstract(implementation):
        missing = ', '.join(sorted(getattr(implementation, '__abstractmethods__')))
        raise ValueError(f'{description} must implement {missing}')
    for name in methods:
        method = inspect.unwrap(getattr(implementation, name))
        if (inspect.iscoroutinefunction(method) or inspect.isgeneratorfunction(method)
                or inspect.isasyncgenfunction(method)):
            raise ValueError(f'{description} must implement {name} synchronously, without a generator')
        # Adapters pass the keyword arguments of the contract's method.
        keywords = [parameter.name for parameter in inspect.signature(getattr(contract, name)).parameters.values()
                    if parameter.kind is inspect.Parameter.KEYWORD_ONLY]
        try:
            inspect.signature(method).bind(None, **dict.fromkeys(keywords))
        except TypeError as error:
            raise ValueError(f'{description} must accept the arguments of {name}: {error}') from error
    try:
        inspect.signature(implementation).bind()
    except (TypeError, ValueError) as error:
        raise ValueError(f'{description} must be constructible without arguments: {error}') from error


def link_native_classes(program: Program) -> tuple[dict[str, type], dict[str, type[NativeProcedure]]]:
    """Validate all required implementations before constructing native objects.

    Return the native state classes by type name and the procedure classes by procedure reference.
    """
    classes: dict[str, type] = {}  # By native state type name or stateless procedure prefix.

    def resolve(name: str, has_native_state: bool) -> None:
        if name not in classes:
            classes[name] = get_native_node_type_by_name(name)
        if getattr(classes[name], 'native_node_has_native_state', None) != has_native_state:
            raise ValueError(f'Node type {name!r} requires has_native_state={has_native_state}')

    for node_id in program.native_construction_order:
        instance = program.instances[node_id]
        if instance.type_name is not None:
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


def link_native_resource_types(program: Program) -> dict[str, ResourceType]:
    """Resolve the Python type of every resource type in the program's native interfaces."""
    names: set[str] = set()
    for native_type in program.native_types.values():
        names.update(native_type.initializer_resources.values())
        names.update(native_type.resource_outputs.values())
    for interface in program.native_procedures.values():
        names.update(interface.resources.values())
    return {name: ResourceType(name, get_native_resource_type_by_name(name)) for name in sorted(names)}


def link_native_enums(program: Program) -> None:
    """Check each native enum against the runtime's expectations; member names and numbers are not compared."""
    from .enums import native_enum_types
    for enum_type in used_declared_types(program):
        if not isinstance(enum_type, TypeEnum) or enum_type.native_name is None:
            continue
        name = enum_type.native_name
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


def link_native_structs(program: Program) -> None:
    """Check each native struct against the runtime's expectations; field names are not compared."""
    from .signatures import describe, matches
    from .structs import native_struct_types
    for struct_type in used_declared_types(program):
        if not isinstance(struct_type, TypeStruct) or struct_type.native_name is None:
            continue
        name = struct_type.native_name
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


def link_native_signatures(program: Program) -> None:
    """Check the native interfaces a program uses against the runtime's signatures."""
    from . import native_interfaces
    from .signatures import Builtin, Option, ResourceType as SignatureResourceType, describe, matches

    def check_keys(where: str, kind: str, actual: Iterable[str], expected: Iterable[str]) -> None:
        if set(actual) != set(expected):
            raise ValueError(f'{where}: {kind} {sorted(actual)!r} do not match the expected {sorted(expected)!r}')

    def check_type(where: str, label: str, signature_type: SignatureType, value_type: Type,
                   bindings: Mapping[str, Type]) -> None:
        if not matches(signature_type, value_type, bindings):
            raise ValueError(f'{where}: {label} has type {value_type}, expected {describe(signature_type, bindings)}')

    def check_uses(where: str, signature: Initializer | SignatureProcedure,
                   values: Mapping[str, Constant]) -> dict[str, Type]:
        check_keys(where, 'uses bindings', values, signature.uses)
        # Each ValueType binding gives the type of the type variable with its key.
        bindings: dict[str, Type] = {}
        for key, signature_type in signature.uses.items():
            if signature_type == Builtin('ValueType') and matches(signature_type, values[key].value_type, {}):
                value = values[key].value
                assert isinstance(value, Type)
                bindings[key] = value
        for key, signature_type in signature.uses.items():
            check_type(where, f'uses binding {key!r}', signature_type, values[key].value_type, bindings)
        return bindings

    def check_ports(where: str, kind: str, ports: Mapping[str, Port], value_types: Mapping[str, Type],
                    resource_types: Mapping[str, str], bindings: Mapping[str, Type]) -> None:
        check_keys(where, kind, [*value_types, *resource_types], ports)
        for key, port in ports.items():
            if port.multi:
                raise ValueError(f'{where}: multi port {key!r} is not supported')
            if port.kind == 'resource':
                assert isinstance(port.type, SignatureResourceType)
                if resource_types.get(key) != port.type.native_name:
                    actual = resource_types.get(key, 'a value')
                    raise ValueError(f'{where}: {key!r} is {actual}, expected resource {port.type.native_name}')
            elif key not in value_types:
                raise ValueError(f'{where}: {key!r} is a resource, expected a {port.kind}')
            else:
                assert not isinstance(port.type, SignatureResourceType)
                # Programs store an event of T like a value of Option[T].
                expected = Option(port.type) if port.kind == 'event' else port.type
                check_type(where, repr(key), expected, value_types[key], bindings)

    for node_id, instance in program.instances.items():
        if instance.type_name is None:
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
            procedure_signature = native_interfaces.native_procedures.get(procedure.reference)
            if procedure_signature is None:
                raise ValueError(f'Cannot find the signature of native procedure {procedure.reference!r}')
            where = f'Native procedure {procedure.reference!r} of {procedure.node_id!r}'
            if stage.name.lower() != procedure_signature.stage:
                raise ValueError(f'{where}: runs in stage {stage.name.lower()}, expected {procedure_signature.stage}')
            inputs = {name: source.value_type for name, source in procedure.dependencies}
            if len(inputs) != len(procedure.dependencies):
                raise ValueError(f'{where}: multi inputs are not supported')
            outputs = {name: target.variable.var_type for name, target in procedure.outputs.items()}
            resources = program.native_procedures[procedure.reference].resources
            bindings = check_uses(where, procedure_signature, procedure.values)
            check_ports(where, 'inputs', procedure_signature.depends, inputs, resources, bindings)
            check_ports(where, 'outputs', procedure_signature.produces, outputs, {}, bindings)

    for function in program.functions:
        function_signature = native_interfaces.native_functions.get(function.name)
        if function_signature is None:
            raise ValueError(f'Cannot find the signature of native function {function.name!r}')
        where = f'Native function {function.name!r}'
        if len(function.parameter_types) != len(function_signature.parameters):
            raise ValueError(f'{where}: has {len(function.parameter_types)} parameters, '
                             f'expected {len(function_signature.parameters)}')
        for index, (value_type, signature_type) in enumerate(zip(function.parameter_types, function_signature.parameters)):
            check_type(where, f'parameter {index}', signature_type, value_type, {})
        check_type(where, 'result', function_signature.result, function.return_type, {})


def link_native_contracts(program: Program, state_classes: Mapping[str, type],
                          procedure_classes: Mapping[str, type[NativeProcedure]]) -> tuple[dict[str, LinkedState], dict[str, LinkedProcedure]]:
    """Check native implementations against their contracts; run after link_native_signatures().

    Return the linked native states by type name and procedures by reference.
    """
    from . import native_interfaces

    states = {}
    for type_name in sorted({instance.type_name for instance in program.instances.values()
                             if instance.type_name is not None}):
        signature = native_interfaces.native_state_types[type_name]
        contract = resolve_contract(f'Native state type {type_name!r}', signature.contract)
        check_implementation(f'Native state type {type_name!r}', state_classes[type_name], contract, ['initialize'])
        assert isinstance(contract, type) and issubclass(contract, NativeState)
        states[type_name] = LinkedState(state_classes[type_name], contract._adapter, signature)
    procedures = {}
    for reference, procedure_class in procedure_classes.items():
        procedure_signature = native_interfaces.native_procedures[reference]
        contract = resolve_contract(f'Native procedure {reference!r}', procedure_signature.contract)
        check_implementation(f'Native procedure {reference!r}', procedure_class, contract, ['prepare', 'run'])
        assert isinstance(contract, type) and issubclass(contract, NativeProcedure)
        procedures[reference] = LinkedProcedure(procedure_class, contract._adapter, procedure_signature)
    return states, procedures


def link_native_functions(program: Program) -> tuple[NativeFunction, ...]:
    """Bind the implementation of every function in the program's function table; run after link_native_signatures()."""
    from . import native_interfaces

    functions = []
    for function in program.functions:
        where = f'Native function {function.name!r}'
        registered = get_native_function_by_name(function.name)
        location = native_interfaces.native_functions[function.name].contract
        if registered.contract is not resolve_contract(where, location):
            raise ValueError(f'{where} must be registered with the contract {location}')
        functions.append(bind_native_function(function.name, function.parameter_types, function.return_type))
    return tuple(functions)
