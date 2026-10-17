"""Resolve and validate native implementations for a program."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from ..functions import bind_native_function
from ..program import Constant, Program, used_declared_types
from ..types import Type, TypeEnum, TypeStruct
from .extensions import RuntimeNativeExtension
from .native_api import InitializerAdapter, NativeProcedure, NativeState, ProcedureAdapter
from .procedures import NativeFunction
from .resources import ResourceType

if TYPE_CHECKING:
    from .signatures import Initializer, Port, Procedure as SignatureProcedure, SignatureType


class LinkedState(NamedTuple):
    """A native state class with the adapter and signature of its contract."""

    factory: Callable[[], NativeState[Any]]
    adapter: InitializerAdapter
    signature: Initializer


class LinkedProcedure(NamedTuple):
    """A native procedure class with the adapter and signature of its contract."""

    factory: Callable[[], NativeProcedure]
    adapter: ProcedureAdapter
    signature: SignatureProcedure


def link_native_resource_types(program: Program, extension: RuntimeNativeExtension) -> dict[str, ResourceType]:
    """Resolve the Python type of every resource type in the program's native interfaces."""
    names: set[str] = set()
    for native_type in program.native_types.values():
        names.update(native_type.initializer_resources.values())
        names.update(native_type.resource_outputs.values())
    for interface in program.native_procedures.values():
        names.update(interface.resources.values())
    result = {}
    for name in sorted(names):
        loader = extension.resources.get(name)
        if loader is None:
            raise ValueError(f'Cannot find native resource type {name!r}')
        result[name] = ResourceType(name, loader())
    return result


def link_native_enums(program: Program, extension: RuntimeNativeExtension) -> None:
    """Check each native enum against the runtime's expectations; member names and numbers are not compared."""
    for enum_type in used_declared_types(program):
        if not isinstance(enum_type, TypeEnum) or enum_type.native_name is None:
            continue
        name = enum_type.native_name
        expected = extension.enums.get(name)
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


def link_native_structs(program: Program, extension: RuntimeNativeExtension) -> None:
    """Check each native struct against the runtime's expectations; field names are not compared."""
    from .signatures import describe, matches
    for struct_type in used_declared_types(program):
        if not isinstance(struct_type, TypeStruct) or struct_type.native_name is None:
            continue
        name = struct_type.native_name
        expected = extension.structs.get(name)
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


def link_native_signatures(program: Program, extension: RuntimeNativeExtension) -> None:
    """Check the native interfaces a program uses against the runtime's signatures."""
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
        signature_entry = extension.states.get(instance.type_name)
        if signature_entry is None:
            raise ValueError(f'Cannot find the signature of native state type {instance.type_name!r}')
        signature = signature_entry.signature
        where = f'Native state type {instance.type_name!r} of {node_id!r}'
        native_type = program.native_types[instance.type_name]
        bindings = check_uses(where, signature, instance.initializer_values)
        check_ports(where, 'inputs', signature.depends, {}, native_type.initializer_resources, bindings)
        check_ports(where, 'outputs', signature.produces, {}, native_type.resource_outputs, bindings)

    for stage, schedule in program.procs.items():
        for procedure in schedule:
            if procedure.body is not None:
                continue
            procedure_signature_entry = extension.procedures.get(procedure.reference)
            if procedure_signature_entry is None:
                raise ValueError(f'Cannot find the signature of native procedure {procedure.reference!r}')
            procedure_signature = procedure_signature_entry.signature
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
        function_signature_entry = extension.functions.get(function.name)
        if function_signature_entry is None:
            raise ValueError(f'Cannot find the signature of native function {function.name!r}')
        function_signature = function_signature_entry.signature
        where = f'Native function {function.name!r}'
        if len(function.parameter_types) != len(function_signature.parameters):
            raise ValueError(f'{where}: has {len(function.parameter_types)} parameters, '
                             f'expected {len(function_signature.parameters)}')
        for index, (value_type, signature_type) in enumerate(zip(function.parameter_types, function_signature.parameters)):
            check_type(where, f'parameter {index}', signature_type, value_type, {})
        check_type(where, 'result', function_signature.result, function.return_type, {})


def link_native_contracts(program: Program, extension: RuntimeNativeExtension
                          ) -> tuple[dict[str, LinkedState], dict[str, LinkedProcedure]]:
    """Load the required typed factories and adapters before constructing any native objects."""

    states = {}
    for name in sorted({instance.type_name for instance in program.instances.values()
                        if instance.type_name is not None}):
        loader = extension.states.get(name)
        if loader is None:
            raise ValueError(f'Cannot find native initializer {name!r}')
        implementation = loader.load()
        states[name] = LinkedState(implementation.factory, implementation.adapter,
                                   loader.signature)
    procedures = {}
    for reference in sorted({procedure.reference for schedule in program.procs.values()
                             for procedure in schedule if procedure.body is None}):
        procedure_loader = extension.procedures.get(reference)
        if procedure_loader is None:
            raise ValueError(f'Cannot find native procedure {reference!r}')
        procedure = procedure_loader.load()
        procedures[reference] = LinkedProcedure(procedure.factory, procedure.adapter,
                                                procedure_loader.signature)
    return states, procedures


def link_native_functions(program: Program, extension: RuntimeNativeExtension) -> tuple[NativeFunction, ...]:
    """Bind the implementation of every function after checking the program's signatures."""
    return tuple(bind_native_function(function.name, function.parameter_types, function.return_type, extension=extension)
                 for function in program.functions)
