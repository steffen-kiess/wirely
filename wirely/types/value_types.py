"""Value type descriptors for native Python implementations."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .. import types as _types
from .option import Some


@dataclass(frozen=True, slots=True)
class NativeEnum:
    """What the runtime expects of a native enum; numbers are its contract, not names."""

    python_type: type  # IntEnum of the known members; its length is a closed enum's member count.
    is_open: bool
    underlying_type: str  # Integer type name, such as 'uint8'.


@dataclass(frozen=True, slots=True, eq=False)
class EnumType:
    """The native view of an enum type, compared by identity."""

    name: str  # Qualified name, such as 'KNX.Protocol'.
    underlying_type: 'ValueType'  # Integer type of the member numbers.
    members: Mapping[str, int]  # Member numbers by name, in declaration order.
    is_open: bool
    python_type: type | None  # Python enum from wirely.runtime.enums, if any.
    names: Mapping[int, str] = field(init=False, repr=False)  # Member names by number.

    def __post_init__(self):
        object.__setattr__(self, 'names', MappingProxyType({number: name for name, number in self.members.items()}))

    def from_name(self, name):
        """Return the value of the member with this name."""
        if name not in self.members:
            raise ValueError(f'Unknown member {name!r} of {self.name}')
        return EnumValue(self, self.members[name])

    def from_value(self, number):
        """Return the enum value with this number."""
        return EnumValue(self, number)

    def __repr__(self):
        return f'EnumType({self.name})'


@dataclass(frozen=True, slots=True)
class EnumValue:
    """An enum value; equal to another value of the same type and number."""

    type: EnumType
    value: int  # Underlying number, possibly unnamed for an open enum.

    def __post_init__(self):
        self.type.underlying_type.validate(self.value)
        if not self.type.is_open and not 0 <= self.value < len(self.type.members):
            raise ValueError(f'Expected a member number of {self.type.name}, got {self.value!r}')

    @property
    def name(self):
        """The member name."""
        try:
            return self.type.names[self.value]
        except KeyError:
            raise ValueError(f'Unnamed value {self.value} of {self.type.name}') from None

    @property
    def native(self):
        """The member of the enum type's Python enum."""
        if self.type.python_type is None:
            raise ValueError(f'Enum type {self.type.name!r} has no Python enum')
        return self.type.python_type(self.value)

    def __repr__(self):
        name = self.type.names.get(self.value)
        return (f'EnumValue({self.type.name}.{name})' if name is not None
                else f'EnumValue({self.type.name}({self.value}))')


@dataclass(frozen=True, slots=True, eq=False)
class StructType:
    """The native view of a struct type, compared by identity."""

    name: str  # Qualified name, such as 'MQTT.TemplateParts'.
    fields: tuple  # (name, ValueType) of each field, in declaration order.
    python_type: type | None  # NamedTuple from wirely.runtime.structs, if any.

    def index(self, name):
        """Return the position of a field."""
        for index, (field_name, _) in enumerate(self.fields):
            if field_name == name:
                return index
        raise ValueError(f'Unknown field {name!r} of {self.name}')

    def make(self, values):
        """Return the value with these field values in field order."""
        values = tuple(values)
        if len(values) != len(self.fields):
            raise ValueError(f'Expected {len(self.fields)} field values for {self.name}, got {len(values)}')
        return StructValue(self, tuple(field_type.validate(value)
                                       for (_, field_type), value in zip(self.fields, values)))

    def from_native(self, value):
        """Return the value of an instance of the struct type's Python type."""
        if self.python_type is None:
            raise ValueError(f'Struct type {self.name!r} has no Python type')
        if not isinstance(value, self.python_type):
            raise ValueError(f'Expected a {self.python_type.__qualname__}, got {value!r}')
        return self.make(tuple(value))

    def __repr__(self):
        return f'StructType({self.name})'


@dataclass(frozen=True, slots=True)
class StructValue:
    """A value of a struct type; equal to another value of the same type and field values."""

    type: StructType
    values: tuple  # Field values in field order.

    def __post_init__(self):
        if type(self.values) is not tuple or len(self.values) != len(self.type.fields):
            raise ValueError(f'Expected a tuple of {len(self.type.fields)} field values for {self.type.name}')

    @property
    def native(self):
        """The value as an instance of the struct type's Python type."""
        if self.type.python_type is None:
            raise ValueError(f'Struct type {self.type.name!r} has no Python type')
        return self.type.python_type(*self.values)

    def __repr__(self):
        fields = ', '.join(f'{name}: {value!r}' for (name, _), value in zip(self.type.fields, self.values))
        return f'{self.type.name} {{ {fields} }}'


def _to_native(value_type, value):
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


def _from_native(value_type, value):
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


@dataclass(frozen=True, slots=True)
class ValueType:
    """An immutable descriptor of a resolved Wirely value type."""

    _type: _types.Type

    def __post_init__(self):
        if not isinstance(self._type, _types.Type) or self._type.contains_parameters:
            raise ValueError('ValueType requires a resolved value type')

    def __str__(self):
        return str(self._type)

    def __repr__(self):
        return f'ValueType({self})'

    def validate(self, value):
        try:
            value = self._type.verify_value(_from_native(self._type, value))
            return _to_native(self._type, value)
        except Exception as error:
            raise ValueError(str(error)) from error

    @property
    def struct_type(self):
        """The StructType of a struct type."""
        if not isinstance(self._type, _types.TypeStruct):
            raise ValueError(f'{self} is not a struct type')
        return self._type.struct_type

    @property
    def enum_type(self):
        """The EnumType of an enum type."""
        if not isinstance(self._type, _types.TypeEnum):
            raise ValueError(f'{self} is not an enum type')
        return self._type.enum_type

    def parse(self, text):
        try:
            return _to_native(self._type, self._type.parse(text))
        except Exception as error:
            raise ValueError(str(error)) from error

    def to_text(self, value):
        try:
            return self._type.to_text(_from_native(self._type, value))
        except Exception as error:
            raise ValueError(str(error)) from error

    @property
    def supports_json(self):
        return getattr(self._type, 'is_json_serializable', False)

    def json_encode(self, value, format=None):
        if not self.supports_json:
            raise ValueError(f'{self} does not support JSON conversion')
        try:
            value = self._type.verify_value(_from_native(self._type, value))
            return self._type.json_encode(value, format)
        except Exception as error:
            raise ValueError(str(error)) from error

    def json_decode(self, value, format=None):
        if not self.supports_json:
            raise ValueError(f'{self} does not support JSON conversion')
        try:
            value = self._type.verify_value(self._type.json_decode(value, format))
            return _to_native(self._type, value)
        except Exception as error:
            raise ValueError(str(error)) from error

    @property
    def supports_csv(self):
        return getattr(self._type, 'has_to_csv_value', False)

    def to_csv_value(self, value):
        if not self.supports_csv:
            raise ValueError(f'{self} does not support CSV conversion')
        try:
            value = self._type.verify_value(_from_native(self._type, value))
            return self._type.to_csv_value(value)
        except Exception as error:
            raise ValueError(str(error)) from error


unit = ValueType(_types.unit)
boolean = ValueType(_types.boolean)
int8 = ValueType(_types.int8)
int16 = ValueType(_types.int16)
int32 = ValueType(_types.int32)
int64 = ValueType(_types.int64)
int128 = ValueType(_types.int128)
uint8 = ValueType(_types.uint8)
uint16 = ValueType(_types.uint16)
uint32 = ValueType(_types.uint32)
uint64 = ValueType(_types.uint64)
uint128 = ValueType(_types.uint128)
string = ValueType(_types.String)
duration = ValueType(_types.Duration)
time_of_day = ValueType(_types.TimeOfDay)
monotonic_time = ValueType(_types.MonotonicTime)
value_type = ValueType(_types.ValueType)
