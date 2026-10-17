"""Native functions shared by the compiler and the Python runtime.

These functions are callable from procedure bodies and compile-time
expressions. Implementations must be pure: their result depends only on
their arguments, and they have no side effects.
"""

import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..runtime.native_api import FunctionBinding, FunctionContract
from ..types import InternalValue, Type


@dataclass(frozen=True, slots=True)
class FunctionImplementation:
    implementation: Callable[..., object]
    contract: FunctionContract[Any]


def get_native_function_by_name(function_name: str) -> FunctionImplementation:
    """Load the generated binding of a runtime-callable function."""
    from ..runtime.native_bindings import functions
    loader = functions.get(function_name)
    if loader is None:
        raise ValueError(f'Cannot find native function {function_name!r}')
    return loader()


def bind_native_function(function_name: str, parameter_types: Sequence[Type],
                         return_type: Type) -> Callable[[Sequence[InternalValue]], InternalValue]:
    """Return a function calling a native function's implementation with internal values.

    Implementations must be total; exceptions propagate to the caller."""
    implementation = get_native_function_by_name(function_name)
    binding = FunctionBinding(name=function_name, parameter_types=tuple(parameter_types), return_type=return_type)
    return functools.partial(implementation.contract.call, implementation.implementation, binding)
