"""Explicit native implementations and value classes for an engine."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar

from ..types.value_types import NativeEnum, NativeOpaque, NativeStruct
from . import signatures

if TYPE_CHECKING:
    from ..functions import FunctionImplementation
    from .native_api import ProcedureImplementation, StateImplementation


@dataclass(frozen=True, slots=True)
class NativeTypes:
    """Native value classes selected for one compilation or engine."""

    enums: Mapping[str, NativeEnum] = field(default_factory=dict)
    structs: Mapping[str, NativeStruct] = field(default_factory=dict)
    opaque_types: Mapping[str, NativeOpaque] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for item in fields(self):
            object.__setattr__(self, item.name, MappingProxyType(dict(getattr(self, item.name))))


@dataclass(frozen=True, slots=True)
class NativeInitializer:
    signature: signatures.Initializer
    load: Callable[[], StateImplementation]


@dataclass(frozen=True, slots=True)
class NativeProcedure:
    signature: signatures.Procedure
    load: Callable[[], ProcedureImplementation]


@dataclass(frozen=True, slots=True)
class NativeFunction:
    signature: signatures.Function
    load: Callable[[], FunctionImplementation]


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeNativeExtension:
    """Native bindings; mappings are copied when the extension is created."""

    states: Mapping[str, NativeInitializer] = field(default_factory=dict)
    procedures: Mapping[str, NativeProcedure] = field(default_factory=dict)
    functions: Mapping[str, NativeFunction] = field(default_factory=dict)
    resources: Mapping[str, Callable[[], type]] = field(default_factory=dict)
    enums: Mapping[str, NativeEnum] = field(default_factory=dict)
    structs: Mapping[str, NativeStruct] = field(default_factory=dict)
    opaque_types: Mapping[str, NativeOpaque] = field(default_factory=dict)

    native_types: NativeTypes = field(init=False)

    def __post_init__(self) -> None:
        for item in fields(self):
            if item.init:
                object.__setattr__(self, item.name, MappingProxyType(dict(getattr(self, item.name))))
        object.__setattr__(self, 'native_types', NativeTypes(self.enums, self.structs, self.opaque_types))


T = TypeVar('T')


def merge_entries(category: str, normal: Iterable[Mapping[str, T]],
                  overrides: Iterable[Mapping[str, T]]) -> Mapping[str, T]:
    def merge(tables: Iterable[Mapping[str, T]]) -> dict[str, T]:
        result: dict[str, T] = {}
        for table in tables:
            for name in table.keys() & result.keys():
                raise ValueError(f'Duplicate native {category} {name!r}')
            result.update(table)
        return result

    return MappingProxyType(merge(normal) | merge(overrides))


def resolve_extensions(extensions: Iterable[RuntimeNativeExtension] = (), *,
                       override_extensions: Iterable[RuntimeNativeExtension] = (),
                       include_builtin_extensions: bool = True) -> RuntimeNativeExtension:
    normal = list(extensions)
    if include_builtin_extensions:
        from ..generated import extension
        normal.insert(0, extension)
    overrides = tuple(override_extensions)
    return RuntimeNativeExtension(**{
        item.name: merge_entries(item.name, (getattr(ext, item.name) for ext in normal),
                                 (getattr(ext, item.name) for ext in overrides))
        for item in fields(RuntimeNativeExtension) if item.init
    })
