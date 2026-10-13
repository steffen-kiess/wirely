"""Value type descriptors for native Python implementations."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .. import types as _types
from .option import Some


@dataclass(frozen=True, slots=True, eq=False)
class EnumType:
    """The native view of an enum type, compared by identity."""

    name: str  # Qualified name, such as 'KNX.Protocol'.
    underlying_type: 'ValueType'  # Integer type of the member numbers.
    members: Mapping[str, int]  # Member numbers by name, in declaration order.
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
        """Return the value of the member with this number."""
        return EnumValue(self, number)

    def __repr__(self):
        return f'EnumType({self.name})'


@dataclass(frozen=True, slots=True)
class EnumValue:
    """A member of an enum type; equal to another value of the same type and number."""

    type: EnumType
    value: int  # Member number.

    def __post_init__(self):
        if type(self.value) is not int or self.value not in self.type.names:
            raise ValueError(f'Expected a member number of {self.type.name}, got {self.value!r}')

    @property
    def name(self):
        """The member name."""
        return self.type.names[self.value]

    @property
    def native(self):
        """The member of the enum type's Python enum."""
        if self.type.python_type is None:
            raise ValueError(f'Enum type {self.type.name!r} has no Python enum')
        return self.type.python_type(self.value)

    def __repr__(self):
        return f'EnumValue({self.type.name}.{self.name})'


def _to_native(value_type, value):
    if isinstance(value_type, _types.TypeStruct):
        if not isinstance(value, _types.StructValue) or value._type is not value_type:
            raise ValueError(f'Expected struct value of type {value_type}')
        if value.keys() != value_type.fields.keys():
            raise ValueError(f'Expected exactly the fields of {value_type}')
        return _types.StructValue(value_type, {name: _to_native(field_type, value[name])
                                               for name, field_type in value_type.fields.items()})
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
        if not isinstance(value, _types.StructValue) or value._type is not value_type:
            raise ValueError(f'Expected struct value of type {value_type}')
        if value.keys() != value_type.fields.keys():
            raise ValueError(f'Expected exactly the fields of {value_type}')
        return _types.StructValue(value_type, {name: _from_native(field_type, value[name])
                                               for name, field_type in value_type.fields.items()})
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
    def enum_type(self):
        """The EnumType of an enum type."""
        if not isinstance(self._type, _types.TypeEnum):
            raise ValueError(f'{self} is not an enum type')
        return self._type.enum_type

    def make_struct(self, fields):
        """Construct a struct from a mapping of native field values."""
        if not isinstance(self._type, _types.TypeStruct):
            raise ValueError(f'{self} is not a struct type')
        if not isinstance(fields, Mapping):
            raise ValueError('Struct fields must be a mapping')
        return self.validate(_types.StructValue(self._type, fields))

    def parse(self, text):
        try:
            return _to_native(self._type, self._type.parse(text))
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
uint8 = ValueType(_types.uint8)
uint16 = ValueType(_types.uint16)
uint32 = ValueType(_types.uint32)
uint64 = ValueType(_types.uint64)
string = ValueType(_types.String)
duration = ValueType(_types.Duration)
time_of_day = ValueType(_types.TimeOfDay)
monotonic_time = ValueType(_types.MonotonicTime)
value_type = ValueType(_types.ValueType)
