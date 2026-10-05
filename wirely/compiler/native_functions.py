"""Implementations and invocation of declared compile-time functions."""

import importlib
from collections.abc import Mapping
import inspect
from dataclasses import dataclass
from types import MappingProxyType

from wirely.types import TypeStruct, StructValue

from . import model as blocks


native_function_impls = {
    ('KNX', 'ParseGroupAddress'): (
        'wirely.compiler.function_implementations.knx', 'parse_group_address',
    ),
    ('KNX', 'ValueTypeName'): (
        'wirely.compiler.function_implementations.knx', 'value_type_name',
    ),
    ('KNX', 'SupportsValueType'): (
        'wirely.compiler.function_implementations.knx', 'supports_value_type',
    ),
    ('MQTT', 'SupportsType'): (
        'wirely.compiler.function_implementations.mqtt', 'supports_type',
    ),
    ('MQTT', 'ParseTemplate'): (
        'wirely.compiler.function_implementations.mqtt', 'parse_template',
    ),
    ('TwoN', 'ReadCredentials'): (
        'wirely.compiler.function_implementations.two_n', 'read_credentials',
    ),
    ('Std', 'IsValidEdgeType'): (
        'wirely.compiler.function_implementations.std', 'is_valid_edge_type',
    ),
    ('GPIO', 'IsValidActiveState'): (
        'wirely.compiler.function_implementations.gpio', 'is_valid_active_state',
    ),
    ('GPIO', 'IsValidDrive'): (
        'wirely.compiler.function_implementations.gpio', 'is_valid_drive',
    ),
    ('GPIO', 'IsValidBias'): (
        'wirely.compiler.function_implementations.gpio', 'is_valid_bias',
    ),
    ('CSV', 'SupportsType'): (
        'wirely.compiler.function_implementations.csv', 'supports_type',
    ),
    ('Time', 'IsValidDailyInterval'): (
        'wirely.compiler.function_implementations.time', 'is_valid_daily_interval',
    ),
    ('TwoN', 'IsValidCode'): (
        'wirely.compiler.function_implementations.two_n', 'is_valid_code',
    ),
    ('Path', 'Join'): ('wirely.compiler.function_implementations.path', 'join'),
    ('Path', 'Parent'): ('wirely.compiler.function_implementations.path', 'parent'),
    ('Path', 'SourceFile'): ('wirely.compiler.function_implementations.path', 'source_file'),
    ('Test', 'ReplaceKNXPath'): (
        'wirely.compiler.function_implementations.test', 'replace_knx_path',
    ),
    ('Test', 'ReplaceMQTTURI'): (
        'wirely.compiler.function_implementations.test', 'replace_mqtt_uri',
    ),
    ('Test', 'ReplaceOutputPath'): (
        'wirely.compiler.function_implementations.test', 'replace_output_path',
    ),
    ('Test', 'ReplaceTwoNHost'): (
        'wirely.compiler.function_implementations.test', 'replace_twon_host',
    ),
    ('Test', 'ReplaceTwoNPort'): (
        'wirely.compiler.function_implementations.test', 'replace_twon_port',
    ),
    ('Test', 'ReplaceTwoNTLSA'): (
        'wirely.compiler.function_implementations.test', 'replace_twon_tlsa',
    ),
    ('TimeOfDay', 'AddWrap'): (
        'wirely.compiler.function_implementations.time', 'add_wrap',
    ),
}


@dataclass(frozen=True, slots=True)
class NativeFunctionContext:
    function_name: tuple[str, ...]
    arguments: Mapping[str, object]
    arguments_ast: Mapping[str, object]
    call: object
    services: Mapping[str, object]

    def error(self, message, *, argument=None):
        if argument is not None and argument not in self.arguments_ast:
            raise ValueError(f'Unknown function argument {argument!r}')
        source = self.call if argument is None else self.arguments_ast[argument]
        return blocks.ProgramError(source, message)


def validate_entry(name, declaration):
    if name not in native_function_impls:
        raise blocks.ProgramError(declaration, f'Missing implementation for function {".".join(name)!r}')
    entry = native_function_impls[name]
    if callable(entry):
        return
    if (isinstance(entry, tuple) and len(entry) == 2
            and all(isinstance(part, str) and part for part in entry)
            and all(part.isidentifier() for part in entry[0].split('.'))
            and entry[1].isidentifier()):
        return
    raise blocks.ProgramError(declaration, f'Invalid implementation entry for function {".".join(name)!r}')


def validate_collisions(declarations):
    scopes = {}

    def visit(items, scope):
        names = scopes.setdefault(scope, {})
        for item in items:
            name = getattr(item, 'name', None)
            if name is not None:
                key = name.id_str
                previous = names.get(key)
                if previous is not None and (isinstance(item, blocks.DeclarationNativeFunction)
                                             or isinstance(previous, blocks.DeclarationNativeFunction)):
                    raise blocks.ProgramError(item, f'Duplicate or conflicting function declaration {key!r}')
                names[key] = item
            if isinstance(item, blocks.DeclarationLibrary):
                visit(item.declarations, scope + (item.name.id_str,))
            elif isinstance(item, blocks.BlockType):
                visit(item.declarations, scope + (item,))

    visit(declarations, ())


def resolve_implementation(function, call):
    # Declaration objects belong to one compilation, so this cache cannot leak.
    if hasattr(function, '_resolved_implementation'):
        return function._resolved_implementation
    name = '.'.join(function.function_name)
    entry = native_function_impls[function.function_name]
    try:
        implementation = (entry if callable(entry)
                          else getattr(importlib.import_module(entry[0]), entry[1]))
        if not callable(implementation):
            raise ValueError('implementation is not callable')
        target = implementation if inspect.isroutine(implementation) else implementation.__call__
        if any(check(candidate) for candidate in (implementation, target)
               for check in (inspect.iscoroutinefunction, inspect.isgeneratorfunction,
                             inspect.isasyncgenfunction)):
            raise ValueError('implementation must be synchronous and return one value')
        inspect.signature(implementation).bind(context=None)
    except (ImportError, AttributeError, TypeError, ValueError, OSError) as error:
        raise blocks.ProgramError(call, f'Cannot load function {name!r}: {error}') from error
    function._resolved_implementation = implementation
    return implementation


def invoke(function, values, sources, call):
    implementation = resolve_implementation(function, call)
    arguments = {name: blocks.value_types._to_native(ty, value)
                 for name, ty, value in zip(function.argument_names, function.parameter_types, values)}
    context = NativeFunctionContext(
        function.function_name, MappingProxyType(arguments),
        MappingProxyType(dict(zip(function.argument_names, sources))), call,
        function.scope.services,
    )
    try:
        result = implementation(context=context)
    except blocks.ProgramError:
        raise
    except (ValueError, ArithmeticError, OSError) as error:
        raise blocks.ProgramError(call, str(error)) from error
    try:
        if (isinstance(function.return_type, TypeStruct)
                and isinstance(result, Mapping)
                and not isinstance(result, StructValue)):
            result = blocks.value_types.ValueType(function.return_type).make_struct(result)
        result = blocks.value_types._from_native(function.return_type, result)
        return function.return_type.verify_value(result)
    except Exception as error:
        name = '.'.join(function.function_name)
        raise blocks.ProgramError(call, f'Function {name!r} expected result of type {function.return_type}: {error}') from error
