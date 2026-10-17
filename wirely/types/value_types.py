"""Value type descriptors for native Python implementations."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, NoReturn, TypeVar, cast

from .. import types as _types
from .integers import Int8, Int16, Int32, Int64, Int128, UInt8, UInt16, UInt32, UInt64, UInt128
from .option import Some
from .time import Duration, MonotonicTime, TimeOfDay
from .unit import Unit

if TYPE_CHECKING:
    from ..runtime.extensions import NativeTypes

N = TypeVar('N')  # Native representation of a ValueType's values.


class OpenIntEnum(enum.IntEnum):
    """An IntEnum that also represents unnamed numbers of its underlying type."""

    _range: range

    def __init_subclass__(cls, *, minimum: int, maximum: int, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._range = range(minimum, maximum + 1)

    @classmethod
    def _missing_(cls, value: object) -> 'OpenIntEnum | None':
        # Unnamed numbers become uncached pseudo-members whose name is None.
        if type(value) is not int:
            return None
        if value not in cls._range:
            raise ValueError(f'{value} does not fit {cls.__name__}')
        member = int.__new__(cls, value)
        member._name_ = None  # type: ignore[assignment]
        member._value_ = value  # type: ignore[assignment]
        return member

    def __repr__(self) -> str:
        if self._name_ is None:
            return f'<{type(self).__name__}: {self._value_}>'
        return super().__repr__()


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


@dataclass(frozen=True, slots=True, init=False)
class ValueType(Generic[N]):
    """An immutable descriptor of a resolved Wirely value type.

    N is the native representation of the type's values; descriptors
    received from programs are ValueType[Any].
    """

    _type: _types.Type

    def __init__(self: ValueType[Any], value_type: _types.Type) -> None:
        if not isinstance(value_type, _types.Type) or value_type.contains_parameters:
            raise ValueError('ValueType requires a resolved value type')
        object.__setattr__(self, '_type', value_type)

    def from_native(self, value: N, *, native_types: NativeTypes) -> DynamicValue:
        """Return the DynamicValue of a value in native representation."""
        from ..runtime.native_values import from_native
        return DynamicValue._make(self._type, from_native(None, self._type, value, native_types=native_types))

    def __str__(self) -> str:
        return str(self._type)

    def __repr__(self) -> str:
        return f'ValueType({self})'

    def parse(self, text: str) -> DynamicValue:
        """Parse a value in the text format."""
        try:
            value = self._type.parse(text)
        except Exception as error:
            raise ValueError(str(error)) from error
        return DynamicValue._make(self._type, value)

    @property
    def supports_json(self) -> bool:
        return getattr(self._type, 'is_json_serializable', False)

    def json_decode(self, value: Any, format: str | None = None) -> DynamicValue:
        """Convert a JSON-compatible Python value."""
        if not self.supports_json:
            raise ValueError(f'{self} does not support JSON conversion')
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

    _type: _types.Type
    _value: _types.InternalValue

    def __init__(self) -> None:
        raise TypeError('Create DynamicValues with ValueType methods')

    @classmethod
    def _make(cls, value_type: _types.Type, value: _types.InternalValue) -> DynamicValue:
        """Return a value from its internal representation, which is validated."""
        descriptor = ValueType(value_type)
        try:
            value = value_type.verify_value(value)
        except Exception as error:
            raise ValueError(f'Invalid {descriptor} value {value!r}: {error}') from error
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
            text = self.to_text()
        except ValueError:
            text = repr(self._value)
        return f'DynamicValue({self._type}: {text})'

    def __reduce__(self) -> NoReturn:
        raise TypeError('DynamicValue cannot be pickled')

    def to_text(self) -> str:
        """Format the value in the text format."""
        try:
            return self._type.to_text(self._value)
        except Exception as error:
            raise ValueError(str(error)) from error

    def json_encode(self, format: str | None = None) -> Any:
        """Return the value as a JSON-compatible Python value."""
        if not self.type.supports_json:
            raise ValueError(f'{self._type} does not support JSON conversion')
        try:
            return self._type.json_encode(self._value, format)
        except Exception as error:
            raise ValueError(str(error)) from error

    def to_csv_value(self) -> tuple[str, bool]:
        """Return the CSV text of the value and whether it needs quotes."""
        if not self.type.supports_csv:
            raise ValueError(f'{self._type} does not support CSV conversion')
        try:
            return self._type.to_csv_value(self._value)
        except Exception as error:
            raise ValueError(str(error)) from error

    def extract(self, value_type: ValueType[N], *, native_types: NativeTypes) -> N:
        """Return the native representation, requiring the value to have this type."""
        if value_type._type != self._type:
            raise ValueError(f'Expected a value of type {value_type}, got {self._type}')
        return cast(N, self.to_native(native_types=native_types))

    def to_native(self, *, native_types: NativeTypes) -> object:
        """Return the native representation; not every type has one."""
        from ..runtime.native_values import to_native
        return to_native(None, self._type, self._value, native_types=native_types)


T = TypeVar('T')


def option(inner: ValueType[T]) -> ValueType[Some[T] | None]:
    """Return the descriptor of Option[inner]."""
    return ValueType(_types.TypeOption(inner._type))


def list_of(inner: ValueType[T]) -> ValueType[tuple[T, ...]]:
    """Return the descriptor of List[inner]."""
    return ValueType(_types.TypeList(inner._type))


unit: ValueType[Unit] = ValueType(_types.unit)
boolean: ValueType[bool] = ValueType(_types.boolean)
int8: ValueType[Int8] = ValueType(_types.int8)
int16: ValueType[Int16] = ValueType(_types.int16)
int32: ValueType[Int32] = ValueType(_types.int32)
int64: ValueType[Int64] = ValueType(_types.int64)
int128: ValueType[Int128] = ValueType(_types.int128)
uint8: ValueType[UInt8] = ValueType(_types.uint8)
uint16: ValueType[UInt16] = ValueType(_types.uint16)
uint32: ValueType[UInt32] = ValueType(_types.uint32)
uint64: ValueType[UInt64] = ValueType(_types.uint64)
uint128: ValueType[UInt128] = ValueType(_types.uint128)
string: ValueType[str] = ValueType(_types.String)
duration: ValueType[Duration] = ValueType(_types.Duration)
time_of_day: ValueType[TimeOfDay] = ValueType(_types.TimeOfDay)
monotonic_time: ValueType[MonotonicTime] = ValueType(_types.MonotonicTime)
value_type: ValueType[ValueType[Any]] = ValueType(_types.ValueType)
