"""Explicit native implementations and value classes for an engine."""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar

from ..program.types import InternalValue, Type
from . import signatures
from . import value_types

if TYPE_CHECKING:
    from .native_api import FunctionImplementation, ProcedureImplementation, StateImplementation


@dataclass(frozen=True, slots=True)
class NativeEnum:
    """What the runtime expects of a native enum; numbers are its contract, not names."""

    python_type: type[enum.IntEnum]  # IntEnum of the known members; its length is a closed enum's member count.
    is_open: bool
    underlying_type: str  # Integer type name, such as 'uint8'.


@dataclass(frozen=True, slots=True)
class NativeStruct:
    """What the runtime expects of a native struct; field types in order are its contract, not names."""

    python_type: type  # NamedTuple built positionally from the field values.
    fields: tuple  # Signature type of each field (wirely.runtime.signatures), in order.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'fields', tuple(self.fields))


@dataclass(frozen=True, slots=True)
class NativeOpaque:
    """What the runtime expects of an opaque struct; field types in order are its contract, not names.

    The class builds a value with from_fields(*fields), raising ValueError
    for invalid fields, and returns them with to_fields(). Both use the
    native representation of the fields. Optionally, the class method
    parse(text) and the method to_text() convert values from and to text.
    The value with all fields zero must be valid.
    """

    fields: tuple  # Signature type of each field (wirely.runtime.signatures), in order.
    load: Callable[[], type]  # Returns the class implementing the values.
    _loaded: list = field(default_factory=list, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'fields', tuple(self.fields))

    @property
    def python_type(self) -> type:
        if not self._loaded:
            self._loaded.append(self.load())
        return self._loaded[0]


@dataclass(frozen=True, slots=True)
class NativeTypes:
    """Native value classes selected for one compilation or engine."""

    enums: Mapping[str, NativeEnum] = field(default_factory=dict)
    structs: Mapping[str, NativeStruct] = field(default_factory=dict)
    opaque_types: Mapping[str, NativeOpaque] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for item in fields(self):
            object.__setattr__(self, item.name, MappingProxyType(dict(getattr(self, item.name))))

    def to_native(self, value_type: Type, value: InternalValue) -> Any:
        """Return the native representation of a valid internal value; raise ValueError if there is none."""
        return value_types.to_native(None, value_type, value, native_types=self)

    def from_native(self, value_type: Type, value: object) -> InternalValue:
        """Return the internal representation of a native value; raise ValueError if it does not have the type."""
        return value_types.from_native(None, value_type, value, native_types=self)


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
                raise ValueError(f'Duplicate native {category} `{name}`')
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
