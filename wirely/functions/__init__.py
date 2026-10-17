"""Native functions shared by the compiler and the Python runtime.

These functions are callable from procedure bodies and compile-time
expressions. Implementations must be pure: their result depends only on
their arguments, and they have no side effects.
"""

import functools
import importlib
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from ..runtime.native_api import FunctionBinding, FunctionContract
from ..types import InternalValue, Type

F = TypeVar('F', bound=Callable[..., object])


# Maps namespaces to modules of this package, each with a native_functions registry.
function_pkgs = {
    'Duration': 'duration',
    'MonotonicTime': 'monotonic_time',
    'TestLibrary': 'test_library',
    'TimeOfDay': 'time_of_day',
}


@dataclass(frozen=True, slots=True)
class RegisteredFunction:
    implementation: Callable[..., object]
    contract: FunctionContract[Any]


def native_function(registry: dict[str, RegisteredFunction], contract: FunctionContract[F]) -> Callable[[F], F]:
    """Register an implementation of a function contract in a package registry."""
    name = contract.name.rpartition('.')[2]

    def register(implementation: F) -> F:
        if name in registry:
            raise ValueError(f'Duplicate native function name {name!r}')
        registry[name] = RegisteredFunction(implementation, contract)
        return implementation
    return register


def validate_native_function(implementation: Callable[..., object], function_name: str, parameter_count: int) -> None:
    """Reject implementations that cannot be called synchronously with their positional arguments."""
    if not callable(implementation):
        raise ValueError(f'Native function {function_name!r} is not callable')
    target = inspect.unwrap(implementation if inspect.isroutine(implementation) else getattr(implementation, '__call__'))
    if (inspect.iscoroutinefunction(target) or inspect.isgeneratorfunction(target)
            or inspect.isasyncgenfunction(target)):
        raise ValueError(f'Native function {function_name!r} must be synchronous and return one value')
    try:
        inspect.signature(implementation).bind(*(None,) * parameter_count)
    except (TypeError, ValueError) as error:
        raise ValueError(f'Invalid native function {function_name!r}: {error}') from error


def get_native_function_by_name(function_name: str) -> RegisteredFunction:
    """Return the registered implementation of a native function and its contract."""
    namespace, _, name = function_name.rpartition('.')
    pkg_name = function_pkgs.get(namespace)
    if pkg_name is None:
        raise ValueError(f'No native namespace for function {function_name!r}')
    pkg = importlib.import_module('.' + pkg_name, __name__)
    registered = pkg.native_functions.get(name)
    if registered is None:
        raise ValueError(f'Cannot find native function {function_name!r}')
    if registered.contract.name != function_name:
        raise ValueError(f'Native function {function_name!r} is registered with the contract of '
                         f'{registered.contract.name!r}')
    return registered


def bind_native_function(function_name: str, parameter_types: Sequence[Type],
                         return_type: Type) -> Callable[[Sequence[InternalValue]], InternalValue]:
    """Return a function calling a native function's implementation with internal values.

    Implementations must be total; exceptions propagate to the caller."""
    registered = get_native_function_by_name(function_name)
    validate_native_function(registered.implementation, function_name, len(parameter_types))
    binding = FunctionBinding(name=function_name, parameter_types=tuple(parameter_types), return_type=return_type)
    return functools.partial(registered.contract.call, registered.implementation, binding)
