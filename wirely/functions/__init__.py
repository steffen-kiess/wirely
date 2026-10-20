"""Native functions shared by the compiler and the Python runtime.

These functions are callable from procedure bodies and compile-time
expressions. Implementations must be pure: their result depends only on
their arguments, and they have no side effects.
"""

import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..runtime.extensions import RuntimeNativeExtension
from ..runtime.native_api import FunctionBinding, FunctionContract
from ..types import InternalValue, Type


@dataclass(frozen=True, slots=True)
class FunctionImplementation:
    implementation: Callable[..., object]
    contract: FunctionContract[Any]


def bind_native_function(function_name: str, parameter_types: Sequence[Type],
                         return_type: Type, *,
                         extension: RuntimeNativeExtension) -> Callable[[Sequence[InternalValue]], InternalValue]:
    """Return a function calling a native function's implementation with internal values.

    Implementations must be total; exceptions propagate to the caller."""
    entry = extension.functions.get(function_name)
    if entry is None:
        raise ValueError(f'Cannot find native function `{function_name}`')
    from ..runtime.signatures import validate_function
    validate_function(function_name, entry.signature, parameter_types, return_type)
    implementation = entry.load()
    binding = FunctionBinding(native_types=extension.native_types, name=function_name,
                              parameter_types=tuple(parameter_types), return_type=return_type)
    return functools.partial(implementation.contract.call, implementation.implementation, binding)
