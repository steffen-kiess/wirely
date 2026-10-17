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


def to_native(signature: signatures.SignatureType | None, value_type: types.Type,
              value: InternalValue) -> object:
    """Return the native representation of a valid internal value; raise ValueError if there is none."""
    return _to_native(signature, value_type, value, ())


def from_native(signature: signatures.SignatureType | None, value_type: types.Type,
                value: object) -> InternalValue:
    """Return the internal representation of a native value; raise ValueError if it does not have the type."""
    return _from_native(signature, value_type, value, ())


def _error(path: tuple[str, ...], message: str) -> ValueError:
    return ValueError(f'{" ".join(path)}: {message}' if path else message)


def _inner(signature: signatures.SignatureType | None) -> signatures.SignatureType | None:
    return signature.inner if isinstance(signature, (signatures.Option, signatures.List)) else None


def _native_enum(value_type: types.TypeEnum, path: tuple[str, ...]) -> type:
    from .enums import native_enum_types
    native = None if value_type.native_name is None else native_enum_types.get(value_type.native_name)
    if native is None:
        raise _error(path, f'Enum {value_type} has no native representation')
    return native.python_type


def _native_struct(value_type: types.TypeStruct, path: tuple[str, ...]) -> type:
    from .structs import native_struct_types
    native = None if value_type.native_name is None else native_struct_types.get(value_type.native_name)
    if native is None:
        raise _error(path, f'Struct {value_type} has no native representation')
    return native.python_type


def _to_native(signature: signatures.SignatureType | None, value_type: types.Type, value: Any,
               path: tuple[str, ...]) -> object:
    if isinstance(signature, signatures.TypeVar):
        return DynamicValue._make(value_type, value)
    if isinstance(value_type, types.TypeOption):
        if value is None:
            return None
        if type(value) is not Some:
            raise _error(path, f'Expected None or Some for {value_type}, got {value!r}')
        return Some(_to_native(_inner(signature), value_type.inner_type, value.value, path + ('in option',)))
    if isinstance(value_type, types.TypeList):
        if type(value) is not tuple:
            raise _error(path, f'Expected a tuple for {value_type}, got {value!r}')
        return tuple(_to_native(_inner(signature), value_type.inner_type, item, path + (f'in item {index}',))
                     for index, item in enumerate(value))
    if isinstance(value_type, types.TypeStruct):
        python_type = _native_struct(value_type, path)
        if type(value) is not tuple or len(value) != len(value_type.fields):
            raise _error(path, f'Expected a tuple of {len(value_type.fields)} fields for {value_type}, got {value!r}')
        return python_type(*(_to_native(None, field_type, field, path + (f'in field {name!r}',))
                             for (name, field_type), field in zip(value_type.fields.items(), value)))
    if isinstance(value_type, types.TypeEnum):
        return _native_enum(value_type, path)(value_type.verify_value(value))
    if isinstance(value_type, types.TypeInt):
        return integer_class(value_type.bits, value_type.is_signed)(value)
    if isinstance(value_type, types.TypeValueType):
        return ValueType(value)
    return value


def _from_native(signature: signatures.SignatureType | None, value_type: types.Type, value: object,
                 path: tuple[str, ...]) -> Any:
    if isinstance(signature, signatures.TypeVar):
        if not isinstance(value, DynamicValue):
            raise _error(path, f'Expected a DynamicValue of {value_type}, got {value!r}')
        if value._type != value_type:
            raise _error(path, f'Expected a DynamicValue of {value_type}, got one of {value._type}')
        return value._value
    if isinstance(value_type, types.TypeOption):
        if value is None:
            return None
        if not isinstance(value, Some):
            raise _error(path, f'Expected None or wirely.Some for {value_type}, got {value!r}')
        return Some(_from_native(_inner(signature), value_type.inner_type, value.value, path + ('in option',)))
    if isinstance(value_type, types.TypeList):
        if type(value) is not tuple:
            raise _error(path, f'Expected a tuple for {value_type}, got {value!r}')
        return tuple(_from_native(_inner(signature), value_type.inner_type, item, path + (f'in item {index}',))
                     for index, item in enumerate(value))
    if isinstance(value_type, types.TypeStruct):
        python_type = _native_struct(value_type, path)
        if type(value) is not python_type:
            raise _error(path, f'Expected a {python_type.__name__} for {value_type}, got {value!r}')
        assert isinstance(value, tuple)
        return tuple(_from_native(None, field_type, field, path + (f'in field {name!r}',))
                     for (name, field_type), field in zip(value_type.fields.items(), value))
    if isinstance(value_type, types.TypeEnum):
        python_type = _native_enum(value_type, path)
        if type(value) is not python_type:
            raise _error(path, f'Expected a {python_type.__name__} for {value_type}, got {value!r}')
        assert isinstance(value, int)
        return value_type.verify_value(int(value))
    if isinstance(value_type, types.TypeInt):
        cls = integer_class(value_type.bits, value_type.is_signed)
        if type(value) is not cls:
            raise _error(path, f'Expected a wirely.{cls.__name__} for {value_type}, got {value!r}')
        assert isinstance(value, cls)
        return value.value
    if isinstance(value_type, types.TypeValueType):
        if not isinstance(value, ValueType):
            raise _error(path, f'Expected a wirely.ValueType, got {value!r}')
        return value._type
    if isinstance(value_type, types.TypePrimitive) and type(value) is value_type.python_type:
        return value
    raise _error(path, f'Expected a value of {value_type}, got {value!r}')
