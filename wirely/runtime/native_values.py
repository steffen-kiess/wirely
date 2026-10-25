"""Conversion between internal values and the values native code receives.

A signature type from wirely.runtime.signatures marks where a native
interface is generic: its type variable positions are DynamicValues. Without
a signature, the whole value is concrete. The resolved Type gives the
structure in both cases.
"""

from __future__ import annotations

from typing import Any

from .. import types
from ..types import InternalValue, Some
from ..types.integers import integer_class
from ..types.value_types import DynamicValue, ValueType
from . import signatures
from .extensions import NativeTypes


def to_native(signature: signatures.SignatureType | None, value_type: types.Type,
              value: InternalValue, *, native_types: NativeTypes) -> object:
    """Return the native representation of a valid internal value; raise ValueError if there is none."""
    return _to_native(signature, value_type, value, (), native_types)


def from_native(signature: signatures.SignatureType | None, value_type: types.Type,
                value: object, *, native_types: NativeTypes) -> InternalValue:
    """Return the internal representation of a native value; raise ValueError if it does not have the type."""
    return _from_native(signature, value_type, value, (), native_types)


def check_opaque_values(value_type: types.Type, value: InternalValue, *, native_types: NativeTypes) -> None:
    """Raise ValueError if the implementation of an opaque struct in a valid internal value rejects it."""
    if not _contains_opaque(value_type):
        return
    # Only opaque structs need a native representation; declared structs around them need none.
    if isinstance(value_type, types.TypeStruct) and not value_type.is_opaque:
        assert isinstance(value, tuple)
        for field_type, field in zip(value_type.field_types, value):
            check_opaque_values(field_type, field, native_types=native_types)
    elif isinstance(value_type, types.TypeOption):
        if value is not None:
            assert isinstance(value, Some)
            check_opaque_values(value_type.inner_type, value.value, native_types=native_types)
    elif isinstance(value_type, types.TypeList):
        assert isinstance(value, tuple)
        for element in value:
            check_opaque_values(value_type.inner_type, element, native_types=native_types)
    else:
        to_native(None, value_type, value, native_types=native_types)


def _contains_opaque(value_type: types.Type) -> bool:
    if isinstance(value_type, types.TypeStruct):
        return value_type.is_opaque or any(_contains_opaque(field) for field in value_type.field_types)
    if isinstance(value_type, (types.TypeOption, types.TypeList)):
        return _contains_opaque(value_type.inner_type)
    return False


def _error(path: tuple[str, ...], message: str) -> ValueError:
    return ValueError(f'{" ".join(path)}: {message}' if path else message)


def _inner(signature: signatures.SignatureType | None) -> signatures.SignatureType | None:
    return signature.inner if isinstance(signature, (signatures.Option, signatures.List)) else None


def _native_enum(value_type: types.TypeEnum, path: tuple[str, ...], native_types: NativeTypes) -> type:
    native = None if value_type.native_name is None else native_types.enums.get(value_type.native_name)
    if native is None:
        raise _error(path, f'Enum `{value_type}` has no native representation')
    return native.python_type


def _native_struct(value_type: types.TypeStruct, path: tuple[str, ...], native_types: NativeTypes) -> type:
    table = native_types.opaque_types if value_type.is_opaque else native_types.structs
    native = None if value_type.native_name is None else table.get(value_type.native_name)
    if native is None:
        raise _error(path, f'{"Opaque struct" if value_type.is_opaque else "Struct"} `{value_type}` '
                     'has no native representation')
    return native.python_type


def _to_native(signature: signatures.SignatureType | None, value_type: types.Type, value: Any,
               path: tuple[str, ...], native_types: NativeTypes) -> object:
    if isinstance(signature, signatures.TypeVar):
        return DynamicValue._make(value_type, value)
    if isinstance(value_type, types.TypeOption):
        if value is None:
            return None
        if type(value) is not Some:
            raise _error(path, f'Expected None or Some for `{value_type}`, got {value!r}')
        return Some(_to_native(_inner(signature), value_type.inner_type, value.value, path + ('in option',), native_types))
    if isinstance(value_type, types.TypeList):
        if type(value) is not tuple:
            raise _error(path, f'Expected a tuple for `{value_type}`, got {value!r}')
        return tuple(_to_native(_inner(signature), value_type.inner_type, item, path + (f'in item {index}',), native_types)
                     for index, item in enumerate(value))
    if isinstance(value_type, types.TypeStruct):
        python_type = _native_struct(value_type, path, native_types)
        if type(value) is not tuple or len(value) != len(value_type.field_types):
            raise _error(path, f'Expected a tuple of {len(value_type.field_types)} fields for `{value_type}`, got {value!r}')
        fields = (_to_native(None, field_type, field, path + (f'in field `{value_type.field_label(index)}`',), native_types)
                  for index, (field_type, field) in enumerate(zip(value_type.field_types, value)))
        if not value_type.is_opaque:
            return python_type(*fields)
        try:
            return python_type.from_fields(*fields)  # type: ignore[attr-defined]
        except ValueError as error:
            raise _error(path, f'Invalid `{value_type}` value {value!r}: {error}') from error
    if isinstance(value_type, types.TypeEnum):
        return _native_enum(value_type, path, native_types)(value_type.verify_value(value))
    if isinstance(value_type, types.TypeInt):
        return integer_class(value_type.bits, value_type.is_signed)(value)
    if isinstance(value_type, types.TypeValueType):
        return ValueType(value)
    return value


def _from_native(signature: signatures.SignatureType | None, value_type: types.Type, value: object,
                 path: tuple[str, ...], native_types: NativeTypes) -> Any:
    if isinstance(signature, signatures.TypeVar):
        if not isinstance(value, DynamicValue):
            raise _error(path, f'Expected a DynamicValue of `{value_type}`, got {value!r}')
        if value._type != value_type:
            raise _error(path, f'Expected a DynamicValue of `{value_type}`, got one of `{value._type}`')
        return value._value
    if isinstance(value_type, types.TypeOption):
        if value is None:
            return None
        if not isinstance(value, Some):
            raise _error(path, f'Expected None or wirely.Some for `{value_type}`, got {value!r}')
        return Some(_from_native(_inner(signature), value_type.inner_type, value.value, path + ('in option',), native_types))
    if isinstance(value_type, types.TypeList):
        if type(value) is not tuple:
            raise _error(path, f'Expected a tuple for `{value_type}`, got {value!r}')
        return tuple(_from_native(_inner(signature), value_type.inner_type, item, path + (f'in item {index}',), native_types)
                     for index, item in enumerate(value))
    if isinstance(value_type, types.TypeStruct):
        python_type = _native_struct(value_type, path, native_types)
        if type(value) is not python_type:
            raise _error(path, f'Expected a {python_type.__name__} for `{value_type}`, got {value!r}')
        fields = value.to_fields() if value_type.is_opaque else value  # type: ignore[attr-defined]
        assert isinstance(fields, tuple)
        if len(fields) != len(value_type.field_types):
            raise _error(path, f'Expected {len(value_type.field_types)} fields from {python_type.__name__}, got {fields!r}')
        return tuple(_from_native(None, field_type, field, path + (f'in field `{value_type.field_label(index)}`',),
                                  native_types)
                     for index, (field_type, field) in enumerate(zip(value_type.field_types, fields)))
    if isinstance(value_type, types.TypeEnum):
        python_type = _native_enum(value_type, path, native_types)
        if type(value) is not python_type:
            raise _error(path, f'Expected a {python_type.__name__} for `{value_type}`, got {value!r}')
        assert isinstance(value, int)
        return value_type.verify_value(int(value))
    if isinstance(value_type, types.TypeInt):
        cls = integer_class(value_type.bits, value_type.is_signed)
        if type(value) is not cls:
            raise _error(path, f'Expected a wirely.{cls.__name__} for `{value_type}`, got {value!r}')
        assert isinstance(value, cls)
        return value.value
    if isinstance(value_type, types.TypeValueType):
        if not isinstance(value, ValueType):
            raise _error(path, f'Expected a wirely.ValueType, got {value!r}')
        return value._type
    if isinstance(value_type, types.TypePrimitive) and type(value) is value_type.python_type:
        return value
    raise _error(path, f'Expected a value of `{value_type}`, got {value!r}')
