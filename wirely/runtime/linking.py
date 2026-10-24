"""Resolve and validate native implementations for a program."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from ..functions import bind_native_function
from ..program import Constant, InputSource, Program, ResourceSlot, VariableBank, used_declared_types
from ..types import Type, TypeEnum, TypeList, TypeOption, TypeStruct
from .extensions import NativeTypes, RuntimeNativeExtension
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
    names.update(slot.native_name for slot in program.resource_slots)
    result = {}
    for name in sorted(names):
        loader = extension.resources.get(name)
        if loader is None:
            raise ValueError(f'Cannot find native resource type `{name}`')
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
            raise ValueError(f'Cannot find native enum type `{name}`')
        kind, expected_kind = ('open' if is_open else 'closed' for is_open in (enum_type.is_open, expected.is_open))
        if kind != expected_kind:
            raise ValueError(f'Native enum type `{name}` must be {expected_kind}, got {kind}')
        if str(enum_type.underlying_type) != expected.underlying_type:
            raise ValueError(f'Native enum type `{name}` must have underlying type `{expected.underlying_type}`, '
                             f'got `{enum_type.underlying_type}`')
        if not enum_type.is_open and len(enum_type.members) != len(expected.python_type):
            raise ValueError(f'Native enum type `{name}` must have {len(expected.python_type)} members, '
                             f'got {len(enum_type.members)}')


def link_native_structs(program: Program, extension: RuntimeNativeExtension) -> None:
    """Check each native and opaque struct against the runtime's expectations; field names are not compared."""
    for struct_type in used_declared_types(program):
        if not isinstance(struct_type, TypeStruct) or struct_type.native_name is None:
            continue
        check_native_struct(struct_type, extension.native_types)


def check_native_struct(struct_type: TypeStruct, native_types: NativeTypes) -> None:
    """Check a native or opaque struct against an implementation; raise ValueError on a mismatch."""
    from .signatures import describe, matches
    name = struct_type.native_name
    assert name is not None
    kind = 'Opaque struct type' if struct_type.is_opaque else 'Native struct type'
    expected = (native_types.opaque_types if struct_type.is_opaque else native_types.structs).get(name)
    if expected is None:
        raise ValueError(f'Cannot find {kind.lower()} `{name}`')
    field_types = tuple(struct_type.fields.values())
    if len(field_types) != len(expected.fields):
        raise ValueError(f'{kind} `{name}` must have {len(expected.fields)} fields, got {len(field_types)}')
    for index, (field_type, signature_type) in enumerate(zip(field_types, expected.fields)):
        if not matches(signature_type, field_type, {}):
            raise ValueError(f'{kind} `{name}` field {index} has type `{field_type}`, '
                             f'expected {describe(signature_type, {})}')


def link_opaque_values(program: Program, extension: RuntimeNativeExtension) -> None:
    """Check static and initial values containing opaque structs with their implementations."""
    from .native_values import to_native
    values: list[tuple[Type, Any]] = [(constant.value_type, constant.value) for constant in program.constants]
    values += [(variable.var_type, value) for variable, value in program.initial_state.items()]
    for value_type, value in values:
        if _contains_opaque(value_type):
            to_native(None, value_type, value, native_types=extension.native_types)


def _contains_opaque(value_type: Type) -> bool:
    if isinstance(value_type, TypeStruct):
        return value_type.is_opaque or any(_contains_opaque(field) for field in value_type.fields.values())
    if isinstance(value_type, (TypeOption, TypeList)):
        return _contains_opaque(value_type.inner_type)
    return False


def link_native_signatures(program: Program, extension: RuntimeNativeExtension) -> None:
    """Check the native interfaces a program uses against the runtime's signatures."""
    from .signatures import Builtin, Option, TypeVar, ResourceType as SignatureResourceType, describe, matches

    def check_keys(where: str, kind: str, actual: Iterable[str], expected: Iterable[str]) -> None:
        if set(actual) != set(expected):
            raise ValueError(f'{where}: {kind} {sorted(actual)!r} do not match the expected {sorted(expected)!r}')

    def check_type(where: str, label: str, signature_type: SignatureType, value_type: Type,
                   bindings: Mapping[str, Type]) -> None:
        if not matches(signature_type, value_type, bindings):
            raise ValueError(f'{where}: {label} has type `{value_type}`, expected {describe(signature_type, bindings)}')

    def check_uses(where: str, signature: Initializer | SignatureProcedure,
                   sources: Mapping[str, InputSource]) -> dict[str, Type]:
        check_keys(where, 'compile-time reads', sources, signature.uses)
        values: dict[str, Constant] = {}
        for key, source in sources.items():
            if source.constant is None:
                raise ValueError(f'{where}: compile-time read `{key}` requires a constant')
            values[key] = source.constant
        # Each `type` binding gives the type of the type variable with its key.
        bindings: dict[str, Type] = {}
        for key, signature_type in signature.uses.items():
            if signature_type == Builtin('type') and matches(signature_type, values[key].value_type, {}):
                value = values[key].value
                assert isinstance(value, Type)
                bindings[key] = value
        for key, signature_type in signature.uses.items():
            check_type(where, f'compile-time read `{key}`', signature_type, values[key].value_type, bindings)
        return bindings

    def check_ports(where: str, kind: str, ports: Mapping[str, Port], value_types: Mapping[str, Type],
                    resource_types: Mapping[str, str], bindings: Mapping[str, Type]) -> None:
        check_keys(where, kind, [*value_types, *resource_types], ports)
        for key, port in ports.items():
            if port.multi:
                raise ValueError(f'{where}: multi port `{key}` is not supported')
            expected_resource = None
            if isinstance(port.type, SignatureResourceType):
                expected_resource = port.type.native_name
            elif isinstance(port.type, TypeVar) and port.kind == 'fixed':
                bound_type = bindings.get(port.type.name)
                if bound_type is not None and not bound_type.is_runtime_type:
                    expected_resource = getattr(bound_type, 'native_name', None)
            if expected_resource is not None:
                if resource_types.get(key) != expected_resource:
                    actual = f'`{resource_types[key]}`' if key in resource_types else 'a value'
                    raise ValueError(f'{where}: `{key}` is {actual}, expected resource `{expected_resource}`')
            elif key not in value_types:
                raise ValueError(f'{where}: `{key}` is a resource, expected a {port.kind}')
            else:
                assert not isinstance(port.type, SignatureResourceType)
                # Programs store an event of T like a value of Option[T].
                expected = Option(port.type) if port.kind == 'event' else port.type
                check_type(where, f'`{key}`', expected, value_types[key], bindings)

    for stage, schedule in program.procs.items():
        for procedure in schedule:
            if procedure.native is None:
                continue
            native_name = procedure.native.name
            procedure_signature_entry = (extension.states if procedure.initializer else extension.procedures).get(native_name)
            if procedure_signature_entry is None:
                raise ValueError(f'Cannot find the signature of native {"initializer" if procedure.initializer else "procedure"} `{native_name}`')
            procedure_signature = procedure_signature_entry.signature
            where = f'Native procedure `{native_name}` of `{procedure.node}`'
            expected_stage = getattr(procedure_signature, 'stage', 'load')
            if stage.name.lower() != expected_stage:
                raise ValueError(f'{where}: runs in stage {stage.name.lower()}, expected {expected_stage}')
            uses = {name: source for name, source in procedure.named_inputs() if name in procedure_signature.uses}
            ports = [(name, source) for name, source in procedure.named_inputs() if name not in uses]
            inputs = {name: source.value_type for name, source in ports if source.resource is None}
            outputs = {name: target.variable.var_type for name, target in procedure.named_outputs()
                       if not isinstance(target.variable, ResourceSlot)}
            resources = {name: source.resource.native_name for name, source in ports
                         if source.resource is not None}
            resource_outputs = {name: target.variable.native_name for name, target in procedure.named_outputs()
                                if isinstance(target.variable, ResourceSlot)}
            bindings = check_uses(where, procedure_signature, uses)
            check_ports(where, 'inputs', procedure_signature.depends, inputs, resources, bindings)
            for key, source in ports:
                port = procedure_signature.depends[key]
                if port.kind in ('fixed', 'resource') and source.bank not in (VariableBank.CONST_VALUES,
                                                                             VariableBank.FIXED_VALUES):
                    raise ValueError(f'{where}: fixed input `{key}` requires a constant or fixed source')
            check_ports(where, 'outputs', procedure_signature.produces, outputs, resource_outputs, bindings)

    for function in program.functions:
        function_signature_entry = extension.functions.get(function.name)
        if function_signature_entry is None:
            raise ValueError(f'Cannot find the signature of native function `{function.name}`')
        function_signature = function_signature_entry.signature
        where = f'Native function `{function.name}`'
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
    for name in sorted({procedure.native.name for schedule in program.procs.values()
                        for procedure in schedule if procedure.native is not None and procedure.initializer}):
        loader = extension.states.get(name)
        if loader is None:
            raise ValueError(f'Cannot find native initializer `{name}`')
        implementation = loader.load()
        states[name] = LinkedState(implementation.factory, implementation.adapter, loader.signature)
    procedures = {}
    for reference in sorted({procedure.native.name for schedule in program.procs.values()
                             for procedure in schedule if procedure.native is not None and not procedure.initializer}):
        procedure_loader = extension.procedures.get(reference)
        if procedure_loader is None:
            raise ValueError(f'Cannot find native procedure `{reference}`')
        procedure = procedure_loader.load()
        procedures[reference] = LinkedProcedure(procedure.factory, procedure.adapter,
                                                procedure_loader.signature)
    return states, procedures


def link_native_functions(program: Program, extension: RuntimeNativeExtension) -> dict[str, NativeFunction]:
    """Bind the implementation of every function by name after checking the program's signatures."""
    return {function.name: bind_native_function(function.name, function.parameter_types, function.return_type,
                                                extension=extension)
            for function in program.functions}
