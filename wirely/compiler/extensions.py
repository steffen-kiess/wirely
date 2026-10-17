"""Constant native functions available to a compilation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..runtime.extensions import merge_entries
from ..runtime.signatures import Function

if TYPE_CHECKING:
    from .native_functions import ConstantFunctionImplementation


@dataclass(frozen=True, slots=True)
class NativeConstantFunction:
    signature: Function
    load: Callable[[], ConstantFunctionImplementation]


@dataclass(frozen=True, slots=True, kw_only=True)
class CompilerNativeExtension:
    """Constant functions and their signatures; native value classes belong to runtime extensions."""

    functions: Mapping[str, NativeConstantFunction] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'functions', MappingProxyType(dict(self.functions)))


def resolve_compiler_extensions(extensions: Iterable[CompilerNativeExtension] = (), *,
                                override_extensions: Iterable[CompilerNativeExtension] = (),
                                include_builtin_extensions: bool = True) -> CompilerNativeExtension:
    normal = list(extensions)
    if include_builtin_extensions:
        from .generated import extension
        normal.insert(0, extension)
    overrides = tuple(override_extensions)
    return CompilerNativeExtension(functions=merge_entries(
        'constant functions', (ext.functions for ext in normal), (ext.functions for ext in overrides)))
