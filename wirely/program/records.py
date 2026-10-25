"""Binary encoding of the records of the binary program format."""

from __future__ import annotations

import enum
from typing import Any, TypeVar, cast

from .types import Type, TypeBoolean, TypeEnum, TypeInt, TypeStruct
from .generated.format import enum_types, record_types

R = TypeVar('R', bound=tuple[Any, ...])

# Record and enum classes by Wirely type name, to rebuild nested records and enum fields.
_record_classes = {value_type.name: record_class for record_class, value_type in record_types.items()}
_enum_classes = {value_type.name: enum_class for enum_class, value_type in enum_types.items()}


class _ScalarCodec:
    def __init__(self, value_type: Type) -> None:
        self.verify = value_type.verify_value
        self.enum_class = _enum_classes[value_type.name] if isinstance(value_type, TypeEnum) else None
        integer = value_type.underlying_type if isinstance(value_type, TypeEnum) else value_type
        self.boolean = isinstance(integer, TypeBoolean)
        if not self.boolean and not isinstance(integer, TypeInt):
            raise TypeError(f'Unsupported record field type `{value_type}`')
        self.signed = isinstance(integer, TypeInt) and integer.is_signed
        self.size = value_type.size

    def pack_into(self, value: Any, data: bytearray, offset: int) -> None:
        if isinstance(value, enum.IntEnum):
            value = int(value)
        value = self.verify(value)
        data[offset:offset + self.size] = value.to_bytes(self.size, 'little', signed=self.signed)

    def unpack_from(self, data: memoryview, offset: int) -> Any:
        value = int.from_bytes(data[offset:offset + self.size], 'little', signed=self.signed)
        if self.boolean:
            if value > 1:
                raise ValueError(f'Invalid boolean byte {value}')
            return bool(value)
        if self.enum_class is not None:
            return self.enum_class(self.verify(value))
        return value


class _RecordCodec:
    def __init__(self, record_class: type[tuple[Any, ...]], value_type: TypeStruct) -> None:
        self.record_class = record_class
        self.name = value_type.name
        self.size = value_type.size
        self.fields: list[tuple[int, _RecordCodec | _ScalarCodec]] = []
        self.padding: list[tuple[int, int]] = []
        end = 0
        for field_type in value_type.field_types:
            alignment = field_type.alignment
            offset = (end + alignment - 1) & ~(alignment - 1)
            if offset > end:
                self.padding.append((end, offset))
            codec = (_record_codec(_record_classes[field_type.name]) if isinstance(field_type, TypeStruct)
                     else _ScalarCodec(field_type))
            self.fields.append((offset, codec))
            end = offset + codec.size
        self.tail = end

    def pack_into(self, value: Any, data: bytearray, offset: int) -> None:
        # Nested record classes and plain tuples have always been accepted structurally.
        if not isinstance(value, tuple) or len(value) != len(self.fields):
            raise ValueError(f'Expected a value of struct type `{self.name}`, got {value!r}')
        for field, (position, codec) in zip(value, self.fields):
            codec.pack_into(field, data, offset + position)

    def unpack_from(self, data: memoryview, offset: int) -> tuple[Any, ...]:
        if any(data[offset + self.tail:offset + self.size]):
            raise ValueError('Record tail padding must be zero')
        for start, end in self.padding:
            if any(data[offset + start:offset + end]):
                raise ValueError('Struct padding must be zero')
        return self.record_class(*(codec.unpack_from(data, offset + position) for position, codec in self.fields))


# Only generated, fixed record schemas are cached; arbitrary Wirely types remain mutable.
_codecs: dict[type[tuple[Any, ...]], _RecordCodec] = {}


def _record_codec(record_class: type[tuple[Any, ...]]) -> _RecordCodec:
    if record_class not in _codecs:
        _codecs[record_class] = _RecordCodec(record_class, record_types[record_class])
    return _codecs[record_class]


for _record_class in record_types:
    _record_codec(_record_class)


def record_size(record_class: type[tuple[Any, ...]]) -> int:
    """Storage size of a record: its stride in tables, including tail padding."""
    return _codecs[record_class].size


def pack_record(record: tuple[Any, ...]) -> bytes:
    """Encode a record in its storage size, with zero padding."""
    codec = _codecs[type(record)]
    data = bytearray(codec.size)
    codec.pack_into(record, data, 0)
    return bytes(data)


def unpack_record(record_class: type[R], data: bytes | memoryview, offset: int = 0) -> R:
    """Decode the record at offset, rejecting noncanonical booleans and nonzero padding."""
    codec = _codecs[record_class]
    view = memoryview(data)
    if offset < 0 or offset + codec.size > len(view):
        raise ValueError(f'Record `{codec.name}` at offset {offset} extends past the end of the data')
    return cast(R, codec.unpack_from(view, offset))
