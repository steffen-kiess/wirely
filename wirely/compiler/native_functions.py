"""Implementations and invocation of declared native functions."""

import importlib
from collections.abc import Mapping
import inspect
from dataclasses import dataclass
from types import MappingProxyType

from wirely.types import TypeStruct, StructValue

from . import model


native_function_impls = {
    'KNX.ParseGroupAddress': (
        'wirely.compiler.function_implementations.knx', 'parse_group_address',
    ),
    'KNX.ValueTypeName': (
        'wirely.compiler.function_implementations.knx', 'value_type_name',
    ),
    'KNX.SupportsValueType': (
        'wirely.compiler.function_implementations.knx', 'supports_value_type',
    ),
    'MQTT.SupportsType': (
        'wirely.compiler.function_implementations.mqtt', 'supports_type',
    ),
    'MQTT.ParseTemplate': (
        'wirely.compiler.function_implementations.mqtt', 'parse_template',
    ),
    'TwoN.ReadCredentials': (
        'wirely.compiler.function_implementations.two_n', 'read_credentials',
    ),
    'CSV.SupportsType': (
        'wirely.compiler.function_implementations.csv', 'supports_type',
    ),
    'Schedule.IsValidDailyInterval': (
        'wirely.compiler.function_implementations.schedule', 'is_valid_daily_interval',
    ),
    'TwoN.IsValidCode': (
        'wirely.compiler.function_implementations.two_n', 'is_valid_code',
    ),
    'Path.Join': ('wirely.compiler.function_implementations.path', 'join'),
    'Path.Parent': ('wirely.compiler.function_implementations.path', 'parent'),
    'Path.SourceFile': ('wirely.compiler.function_implementations.path', 'source_file'),
    'Test.ReplaceKNXPath': (
        'wirely.compiler.function_implementations.test', 'replace_knx_path',
    ),
    'Test.ReplaceMQTTURI': (
        'wirely.compiler.function_implementations.test', 'replace_mqtt_uri',
    ),
    'Test.ReplaceOutputPath': (
        'wirely.compiler.function_implementations.test', 'replace_output_path',
    ),
    'Test.ReplaceTwoNHost': (
        'wirely.compiler.function_implementations.test', 'replace_twon_host',
    ),
    'Test.ReplaceTwoNPort': (
        'wirely.compiler.function_implementations.test', 'replace_twon_port',
    ),
    'Test.ReplaceTwoNTLSA': (
        'wirely.compiler.function_implementations.test', 'replace_twon_tlsa',
    ),
}


@dataclass(frozen=True, slots=True)
class NativeFunctionContext:
    function_name: str  # Native name, such as 'KNX.ParseGroupAddress'.
    arguments: Mapping[str, object]
    arguments_ast: Mapping[str, object]
    call: object
    services: Mapping[str, object]

    def error(self, message, *, argument=None):
        if argument is not None and argument not in self.arguments_ast:
            raise ValueError(f'Unknown function argument {argument!r}')
        source = self.call if argument is None else self.arguments_ast[argument]
        return model.ProgramError(source, message)


def validate_entry(name, declaration):
    if name not in native_function_impls:
        raise model.ProgramError(declaration, f'Missing implementation for function {name!r}')
    entry = native_function_impls[name]
    if callable(entry):
        return
    if (isinstance(entry, tuple) and len(entry) == 2
            and all(isinstance(part, str) and part for part in entry)
            and all(part.isidentifier() for part in entry[0].split('.'))
            and entry[1].isidentifier()):
        return
    raise model.ProgramError(declaration, f'Invalid implementation entry for function {name!r}')


def validate_collisions(declarations):
    scopes = {}

    def visit(items, scope):
        names = scopes.setdefault(scope, {})
        for item in items:
            name = getattr(item, 'name', None)
            if name is not None:
                key = name.id_str
                previous = names.get(key)
                if previous is not None and (isinstance(item, model.DeclarationNativeFunction)
                                             or isinstance(previous, model.DeclarationNativeFunction)):
                    raise model.ProgramError(item, f'Duplicate or conflicting function declaration {key!r}')
                names[key] = item
            if isinstance(item, model.DeclarationLibrary):
                visit(item.declarations, scope + (item.name.id_str,))
            elif isinstance(item, model.NodeType):
                visit(item.declarations, scope + (item,))

    visit(declarations, ())


def validate_native_names(declarations):
    """Reject native names shared by two node types, resource types, or functions."""
    names = {}

    def register(kind, item, path):
        name = item.alias.value if item.alias is not None else '.'.join(path + (item.name.id_str,))
        previous = names.setdefault((kind, name), item)
        # Wirely name checks report clashes between unkeyed declarations.
        if previous is not item and (item.alias is not None or previous.alias is not None):
            raise model.ProgramError(item.alias or item.name, f'Duplicate native name {name!r}', hints=[
                model.CompilerHint(previous.alias or previous.name, 'Previous declaration was here'),
            ])

    def visit(items, path):
        for item in items:
            if isinstance(item, model.DeclarationLibrary):
                visit(item.declarations, path + (item.name.id_str,))
            elif isinstance(item, model.NodeType):
                if item.is_native:
                    register('node type', item, path)
                elif item.alias is not None:
                    raise model.ProgramError(item.alias, 'Node type keys require native state')
                visit(item.declarations, path + (item.name.id_str,))
            elif isinstance(item, model.DeclarationResourceType):
                register('resource type', item, path)
            elif isinstance(item, model.DeclarationNativeFunction):
                register('function', item, path)

    visit(declarations, ())


def resolve_implementation(function, call):
    # Declaration objects belong to one compilation, so this cache cannot leak.
    if hasattr(function, '_resolved_implementation'):
        return function._resolved_implementation
    name = function.function_name
    entry = native_function_impls[function.native_name]
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
        raise model.ProgramError(call, f'Cannot load function {name!r}: {error}') from error
    function._resolved_implementation = implementation
    return implementation


def invoke(function, values, sources, call):
    implementation = resolve_implementation(function, call)
    arguments = {name: model.value_types._to_native(ty, value)
                 for name, ty, value in zip(function.argument_names, function.parameter_types, values)}
    context = NativeFunctionContext(
        function.native_name, MappingProxyType(arguments),
        MappingProxyType(dict(zip(function.argument_names, sources))), call,
        function.scope.services,
    )
    try:
        result = implementation(context=context)
    except model.ProgramError:
        raise
    except (ValueError, ArithmeticError, OSError) as error:
        raise model.ProgramError(call, str(error)) from error
    try:
        if (isinstance(function.return_type, TypeStruct)
                and isinstance(result, Mapping)
                and not isinstance(result, StructValue)):
            result = model.value_types.ValueType(function.return_type).make_struct(result)
        result = model.value_types._from_native(function.return_type, result)
        return function.return_type.verify_value(result)
    except Exception as error:
        name = function.function_name
        raise model.ProgramError(call, f'Function {name!r} expected result of type {function.return_type}: {error}') from error


def invoke_runtime_function(function, values, call):
    """Evaluate a runtime-callable native function during compilation."""
    from wirely.functions import call_native_function, get_native_function_by_name
    name = function.function_name
    if not hasattr(function, '_runtime_implementation'):
        try:
            function._runtime_implementation = get_native_function_by_name(function.native_name)
        except (ImportError, ValueError) as error:
            raise model.ProgramError(call, f'Cannot load function {name!r}: {error}') from error
    try:
        return call_native_function(function._runtime_implementation, function.native_name,
                                    function.argument_names, function.parameter_types,
                                    function.return_type, values)
    except Exception as error:
        # Runtime functions must not fail, so an exception is a library error.
        inner = RuntimeError(f'Internal error in function {name!r}: {type(error).__name__}: {error}')
        inner.__cause__ = error
        raise model.ast.InternalCompilerError(call, inner) from error
