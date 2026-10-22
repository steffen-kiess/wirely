"""Binary encoding of the records of the binary program format."""

from __future__ import annotations

import enum
from typing import Any, TypeVar

from ..types import TypeEnum, TypeStruct
from .generated.format import enum_types, record_types

R = TypeVar('R', bound=tuple[Any, ...])

# Record and enum classes by Wirely type name, to rebuild nested records and enum fields.
_record_classes = {value_type.name: record_class for record_class, value_type in record_types.items()}
_enum_classes = {value_type.name: enum_class for enum_class, value_type in enum_types.items()}


def record_size(record_class: type[tuple[Any, ...]]) -> int:
    """Storage size of a record: its stride in tables, including tail padding."""
    return record_types[record_class].size


def _plain(record: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return a record and its nested records as the plain tuples of Wirely struct values."""
    return tuple(_plain(field) if isinstance(field, tuple) else int(field) if isinstance(field, enum.IntEnum)
                 else field for field in record)


def _record(record_class: type[R], value_type: TypeStruct, value: tuple[Any, ...]) -> R:
    """Return a Wirely struct value as a record of record_class, with nested records and enum members."""
    fields: list[Any] = [_record(_record_classes[field_type.name], field_type, field) if isinstance(field_type, TypeStruct)
              else _enum_classes[field_type.name](field) if isinstance(field_type, TypeEnum)
              else field for field_type, field in zip(value_type.fields.values(), value)]
    return record_class(*fields)


def pack_record(record: tuple[Any, ...]) -> bytes:
    """Encode a record in its storage size, with zero padding."""
    value_type = record_types[type(record)]
    data = value_type.serialize(_plain(record))
    return data + bytes(value_type.size - len(data))


def unpack_record(record_class: type[R], data: bytes | memoryview, offset: int = 0) -> R:
    """Decode the record at offset, rejecting noncanonical booleans and nonzero padding."""
    value_type = record_types[record_class]
    record = memoryview(data)[offset:offset + value_type.size]
    if offset < 0 or len(record) != value_type.size:
        raise ValueError(f'Record `{value_type.name}` at offset {offset} extends past the end of the data')
    if any(record[value_type.data_size:]):
        raise ValueError('Record tail padding must be zero')
    return _record(record_class, value_type, value_type.deserialize(record[:value_type.data_size]))
