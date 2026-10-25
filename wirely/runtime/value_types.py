"""Value type descriptors for native Python implementations, and conversions.

ValueType and DynamicValue are the descriptors and values native code
receives for type parameters. The conversions translate between internal
values and the values native code receives. A signature type from
wirely.runtime.signatures marks where a native interface is generic: its
type variable positions are DynamicValues. Without a signature, the whole
value is concrete. The resolved Type gives the structure in both cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, NoReturn, TypeVar, cast

from ..program import types
from ..program.types import InternalValue
from ..types.integers import Int8, Int16, Int32, Int64, Int128, UInt8, UInt16, UInt32, UInt64, UInt128
from ..types.integers import integer_class
from ..types.option import Some
from ..types.unit import Unit
from . import signatures

if TYPE_CHECKING:
    from .extensions import NativeTypes

N = TypeVar('N')  # Native representation of a ValueType's values.


@dataclass(frozen=True, slots=True, init=False)
class ValueType(Generic[N]):
    """An immutable descriptor of a resolved Wirely value type.

    N is the native representation of the type's values; descriptors
    received from programs are ValueType[Any].
    """

    _type: types.Type

    def __init__(self: ValueType[Any], value_type: types.Type) -> None:
        if not isinstance(value_type, types.Type) or value_type.contains_parameters:
            raise ValueError('ValueType requires a resolved value type')
        object.__setattr__(self, '_type', value_type)

    def from_native(self, value: N, *, native_types: NativeTypes) -> DynamicValue:
        """Return the DynamicValue of a value in native representation."""
        return DynamicValue._make(self._type, from_native(None, self._type, value, native_types=native_types))

    def __str__(self) -> str:
        return str(self._type)

    def __repr__(self) -> str:
        return f'ValueType({self})'

    def parse(self, text: str, *, native_types: NativeTypes) -> DynamicValue:
        """Parse a value in the text format."""
        return DynamicValue._make(self._type, self._type.parse(text, native_types=native_types))

    @property
    def supports_json(self) -> bool:
        return getattr(self._type, 'is_json_serializable', False)

    def json_decode(self, value: Any, format: str | None = None) -> DynamicValue:
        """Convert a JSON-compatible Python value."""
        if not self.supports_json:
            raise ValueError(f'`{self}` does not support JSON conversion')
        try:
            value = self._type.json_decode(value, format)
        except Exception as error:
            raise ValueError(str(error)) from error
        return DynamicValue._make(self._type, value)

    @property
    def supports_csv(self) -> bool:
        return getattr(self._type, 'has_to_csv_value', False)


class DynamicValue:
    """An immutable value of a type known only at runtime, with its ValueType.

    Create values with ValueType methods such as from_native(). Values are
    equal when their types are equal and their values are equal in Wirely.
    """

    __slots__ = ('_type', '_value')

    _type: types.Type
    _value: types.InternalValue

    def __init__(self) -> None:
        raise TypeError('Create DynamicValues with ValueType methods')

    @classmethod
    def _make(cls, value_type: types.Type, value: types.InternalValue) -> DynamicValue:
        """Return a value from its internal representation, which is validated."""
        descriptor = ValueType(value_type)
        try:
            value = value_type.verify_value(value)
        except Exception as error:
            raise ValueError(f'Invalid `{descriptor}` value {value!r}: {error}') from error
        result = object.__new__(cls)
        object.__setattr__(result, '_type', value_type)
        object.__setattr__(result, '_value', value)
        return result

    @property
    def type(self) -> ValueType[Any]:
        return ValueType(self._type)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError('DynamicValue is immutable')

    def __delattr__(self, name: str) -> None:
        raise AttributeError('DynamicValue is immutable')

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DynamicValue):
            return NotImplemented
        return self._type == other._type and self._type.equals(self._value, other._value)

    def __hash__(self) -> int:
        return hash((self._type, self._value))

    def __repr__(self) -> str:
        try:
            text = self._type.to_text(self._value)
        except Exception:
            text = repr(self._value)
        return f'DynamicValue({self._type}: {text})'

    def __reduce__(self) -> NoReturn:
        raise TypeError('DynamicValue cannot be pickled')

    def to_text(self, *, native_types: NativeTypes) -> str:
        """Format the value in the text format."""
        try:
            return self._type.to_text(self._value, native_types=native_types)
        except Exception as error:
            raise ValueError(str(error)) from error

    def json_encode(self, format: str | None = None) -> Any:
        """Return the value as a JSON-compatible Python value."""
        if not self.type.supports_json:
            raise ValueError(f'`{self._type}` does not support JSON conversion')
        try:
            return self._type.json_encode(self._value, format)
        except Exception as error:
            raise ValueError(str(error)) from error

    def to_csv_value(self) -> tuple[str, bool]:
        """Return the CSV text of the value and whether it needs quotes."""
        if not self.type.supports_csv:
            raise ValueError(f'`{self._type}` does not support CSV conversion')
        try:
            return self._type.to_csv_value(self._value)
        except Exception as error:
            raise ValueError(str(error)) from error

    def extract(self, value_type: ValueType[N], *, native_types: NativeTypes) -> N:
        """Return the native representation, requiring the value to have this type."""
        if value_type._type != self._type:
            raise ValueError(f'Expected a value of type `{value_type}`, got `{self._type}`')
        return cast(N, self.to_native(native_types=native_types))

    def to_native(self, *, native_types: NativeTypes) -> object:
        """Return the native representation; not every type has one."""
        return to_native(None, self._type, self._value, native_types=native_types)


T = TypeVar('T')


def option(inner: ValueType[T]) -> ValueType[Some[T] | None]:
    """Return the descriptor of Option[inner]."""
    return ValueType(types.TypeOption(inner._type))


def list_of(inner: ValueType[T]) -> ValueType[tuple[T, ...]]:
    """Return the descriptor of List[inner]."""
    return ValueType(types.TypeList(inner._type))


unit: ValueType[Unit] = ValueType(types.unit)
boolean: ValueType[bool] = ValueType(types.boolean)
int8: ValueType[Int8] = ValueType(types.int8)
int16: ValueType[Int16] = ValueType(types.int16)
int32: ValueType[Int32] = ValueType(types.int32)
int64: ValueType[Int64] = ValueType(types.int64)
int128: ValueType[Int128] = ValueType(types.int128)
uint8: ValueType[UInt8] = ValueType(types.uint8)
uint16: ValueType[UInt16] = ValueType(types.uint16)
uint32: ValueType[UInt32] = ValueType(types.uint32)
uint64: ValueType[UInt64] = ValueType(types.uint64)
uint128: ValueType[UInt128] = ValueType(types.uint128)
string: ValueType[str] = ValueType(types.String)
value_type: ValueType[ValueType[Any]] = ValueType(types.ValueType)


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
            raise _error(path, f'Expected a ValueType, got {value!r}')
        return value._type
    if isinstance(value_type, types.TypePrimitive) and type(value) is value_type.python_type:
        return value
    raise _error(path, f'Expected a value of `{value_type}`, got {value!r}')
