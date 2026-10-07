"""Native functions shared by the compiler and the Python runtime.

These functions are callable from procedure bodies and compile-time
expressions. Implementations must be pure: their result depends only on
their arguments, and they have no side effects.
"""

import importlib
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..types import StructValue, TypeStruct, value_types


# Maps namespaces to modules of this package, each with a native_functions registry.
function_pkgs = {
    ('TestLibrary',): 'test_library',
}


def native_function(registry, name):
    """Register a function implementation under its Wirely name in a package registry."""
    def register(implementation):
        if name in registry:
            raise ValueError(f'Duplicate native function name {name!r}')
        registry[name] = implementation
        return implementation
    return register


@dataclass(frozen=True, slots=True)
class RuntimeFunctionContext:
    function_name: tuple[str, ...]  # Qualified name components.
    arguments: Mapping[str, object]  # Native argument values by declared name.


def validate_native_function(implementation, function_name):
    """Reject implementations that cannot be called synchronously with context=."""
    description = '.'.join(function_name)
    if not callable(implementation):
        raise ValueError(f'Native function {description!r} is not callable')
    target = inspect.unwrap(implementation if inspect.isroutine(implementation) else implementation.__call__)
    if (inspect.iscoroutinefunction(target) or inspect.isgeneratorfunction(target)
            or inspect.isasyncgenfunction(target)):
        raise ValueError(f'Native function {description!r} must be synchronous and return one value')
    try:
        inspect.signature(implementation).bind(context=None)
    except (TypeError, ValueError) as error:
        raise ValueError(f'Invalid native function {description!r}: {error}') from error


def call_native_function(implementation, function_name, argument_names, parameter_types, return_type, values):
    """Call an implementation with runtime values and return its result as a runtime value.

    Implementations must be total; exceptions propagate to the caller."""
    arguments = {name: value_types._to_native(value_type, value)
                 for name, value_type, value in zip(argument_names, parameter_types, values)}
    result = implementation(context=RuntimeFunctionContext(tuple(function_name), MappingProxyType(arguments)))
    if isinstance(return_type, TypeStruct) and isinstance(result, Mapping) and not isinstance(result, StructValue):
        result = value_types.ValueType(return_type).make_struct(result)
    return return_type.verify_value(value_types._from_native(return_type, result))


def get_native_function_by_name(function_name: tuple[str, ...]):
    """Return the implementation of a native function, validated for calling."""
    description = '.'.join(function_name)
    pkg_name = function_pkgs.get(function_name[:-1])
    if pkg_name is None:
        raise ValueError(f'No native namespace for function {description!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    implementation = pkg.native_functions.get(function_name[-1])
    if implementation is None:
        raise ValueError(f'Cannot find native function {description!r}')
    validate_native_function(implementation, function_name)
    return implementation
