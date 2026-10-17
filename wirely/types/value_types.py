"""Value type descriptors for native Python implementations."""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Generic, NoReturn, TypeVar, cast

from .. import types as _types
from .integers import Int8, Int16, Int32, Int64, Int128, UInt8, UInt16, UInt32, UInt64, UInt128
from .option import Some
from .time import Duration, MonotonicTime, TimeOfDay
from .unit import Unit

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


@dataclass(frozen=True, slots=True, eq=False)
class EnumType:
    """The native view of an enum type, compared by identity."""

    name: str  # Qualified name, such as 'KNX.Protocol'.
    underlying_type: 'ValueType'  # Integer type of the member numbers.
    members: Mapping[str, int]  # Member numbers by name, in declaration order.
    is_open: bool
    python_type: type | None  # Python enum from wirely.runtime.enums, if any.
    names: Mapping[int, str] = field(init=False, repr=False)  # Member names by number.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'names', MappingProxyType({number: name for name, number in self.members.items()}))

    def from_name(self, name: str) -> 'EnumValue':
        """Return the value of the member with this name."""
        if name not in self.members:
            raise ValueError(f'Unknown member {name!r} of {self.name}')
        return EnumValue(self, self.members[name])

    def from_value(self, number: int) -> 'EnumValue':
        """Return the enum value with this number."""
        return EnumValue(self, number)

    def __repr__(self) -> str:
        return f'EnumType({self.name})'


@dataclass(frozen=True, slots=True)
class EnumValue:
    """An enum value; equal to another value of the same type and number."""

    type: EnumType
    value: int  # Underlying number, possibly unnamed for an open enum.

    def __post_init__(self) -> None:
        self.type.underlying_type._validate(self.value)
        if not self.type.is_open and not 0 <= self.value < len(self.type.members):
            raise ValueError(f'Expected a member number of {self.type.name}, got {self.value!r}')

    @property
    def name(self) -> str:
        """The member name."""
        try:
            return self.type.names[self.value]
        except KeyError:
            raise ValueError(f'Unnamed value {self.value} of {self.type.name}') from None

    @property
    def native(self) -> enum.IntEnum:
        """The member of a native enum's Python enum; unknown open numbers have no name."""
        if self.type.python_type is None:
            raise ValueError(f'Enum type {self.type.name!r} has no Python enum')
        return self.type.python_type(self.value)

    def __repr__(self) -> str:
        name = self.type.names.get(self.value)
        return (f'EnumValue({self.type.name}.{name})' if name is not None
                else f'EnumValue({self.type.name}({self.value}))')


@dataclass(frozen=True, slots=True, eq=False)
class StructType:
    """The native view of a struct type, compared by identity."""

    name: str  # Qualified name, such as 'MQTT.TemplateParts'.
    fields: tuple  # (name, ValueType) of each field, in declaration order.
    python_type: type[tuple] | None  # NamedTuple from wirely.runtime.structs, if any.

    def index(self, name: str) -> int:
        """Return the position of a field."""
        for index, (field_name, _) in enumerate(self.fields):
            if field_name == name:
                return index
        raise ValueError(f'Unknown field {name!r} of {self.name}')

    def make(self, values: Iterable[object]) -> 'StructValue':
        """Return the value with these field values in field order."""
        values = tuple(values)
        if len(values) != len(self.fields):
            raise ValueError(f'Expected {len(self.fields)} field values for {self.name}, got {len(values)}')
        return StructValue(self, tuple(field_type._validate(value)
                                       for (_, field_type), value in zip(self.fields, values)))

    def from_native(self, value: object) -> 'StructValue':
        """Return the value of an instance of the struct type's Python type."""
        if self.python_type is None:
            raise ValueError(f'Struct type {self.name!r} has no Python type')
        if not isinstance(value, self.python_type):
            raise ValueError(f'Expected a {self.python_type.__qualname__}, got {value!r}')
        return self.make(tuple(value))

    def __repr__(self) -> str:
        return f'StructType({self.name})'


@dataclass(frozen=True, slots=True)
class StructValue:
    """A value of a struct type; equal to another value of the same type and field values."""

    type: StructType
    values: tuple  # Field values in field order.

    def __post_init__(self) -> None:
        if type(self.values) is not tuple or len(self.values) != len(self.type.fields):
            raise ValueError(f'Expected a tuple of {len(self.type.fields)} field values for {self.type.name}')

    @property
    def native(self) -> tuple:
        """The value as an instance of the struct type's Python type."""
        if self.type.python_type is None:
            raise ValueError(f'Struct type {self.type.name!r} has no Python type')
        return self.type.python_type(*self.values)

    def __repr__(self) -> str:
        fields = ', '.join(f'{name}: {value!r}' for (name, _), value in zip(self.type.fields, self.values))
        return f'{self.type.name} {{ {fields} }}'


def _to_native(value_type: _types.Type, value: Any) -> Any:
    if isinstance(value_type, _types.TypeStruct):
        value = value_type.verify_value(value)
        return StructValue(value_type.struct_type, tuple(
            _to_native(field_type, field) for field_type, field in zip(value_type.fields.values(), value)))
    if value_type == _types.ValueType:
        return ValueType(value)
    if isinstance(value_type, _types.TypeEnum):
        return EnumValue(value_type.enum_type, value_type.verify_value(value))
    if isinstance(value_type, _types.TypeOption) and type(value) is Some:
        return Some(_to_native(value_type.inner_type, value.value))
    if isinstance(value_type, _types.TypeList) and type(value) in (list, tuple):
        return tuple(_to_native(value_type.inner_type, item) for item in value)
    return value


def _from_native(value_type: _types.Type, value: Any) -> Any:
    if isinstance(value_type, _types.TypeStruct):
        if not isinstance(value, StructValue) or value.type is not value_type.struct_type:
            raise ValueError(f'Expected a StructValue of {value_type}, got {value!r}')
        return tuple(_from_native(field_type, field)
                     for field_type, field in zip(value_type.fields.values(), value.values))
    if value_type == _types.ValueType:
        if not isinstance(value, ValueType):
            raise ValueError(f'Expected wirely.ValueType, got {value!r}')
        return value._type
    if isinstance(value_type, _types.TypeEnum):
        if not isinstance(value, EnumValue) or value.type is not value_type.enum_type:
            raise ValueError(f'Expected an EnumValue of {value_type}, got {value!r}')
        return value.value
    if isinstance(value_type, _types.TypeOption) and type(value) is Some:
        return Some(_from_native(value_type.inner_type, value.value))
    if isinstance(value_type, _types.TypeList) and type(value) in (list, tuple):
        return tuple(_from_native(value_type.inner_type, item) for item in value)
    return value


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

    def from_native(self, value: N) -> DynamicValue:
        """Return the DynamicValue of a value in native representation."""
        from ..runtime.native_values import from_native
        return DynamicValue._make(self._type, from_native(None, self._type, value))

    def __str__(self) -> str:
        return str(self._type)

    def __repr__(self) -> str:
        return f'ValueType({self})'

    def _validate(self, value: Any) -> Any:
        """Normalize a value in the representation of compile-time functions."""
        try:
            value = self._type.verify_value(_from_native(self._type, value))
            return _to_native(self._type, value)
        except Exception as error:
            raise ValueError(str(error)) from error

    @property
    def struct_type(self) -> StructType:
        """The StructType of a struct type."""
        if not isinstance(self._type, _types.TypeStruct):
            raise ValueError(f'{self} is not a struct type')
        return self._type.struct_type

    @property
    def enum_type(self) -> EnumType:
        """The EnumType of an enum type."""
        if not isinstance(self._type, _types.TypeEnum):
            raise ValueError(f'{self} is not an enum type')
        return self._type.enum_type

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

    def extract(self, value_type: ValueType[N]) -> N:
        """Return the native representation, requiring the value to have this type."""
        if value_type._type != self._type:
            raise ValueError(f'Expected a value of type {value_type}, got {self._type}')
        return cast(N, self.to_native())

    def to_native(self) -> object:
        """Return the native representation; not every type has one."""
        from ..runtime.native_values import to_native
        return to_native(None, self._type, self._value)


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
