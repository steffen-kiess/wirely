"""Const values in the ProgramData representation of the binary program format.

Runtime types use their runtime binary representation. A `string` is a
StringRef to UTF-8 bytes, a `type` a ValueTypeValue referring to
ValueTypes, and a list a ListValue locating its element slots, all in the
same buffer. Structs and options apply the runtime layout rules to these
representations, also beyond the runtime limits.
"""

from collections.abc import Callable
from typing import Any

from ..types import Some, Type, TypeList, TypeOption, TypeResource, TypeString, TypeStruct, TypeValueType
from .generated.format import ListValue, Ref, StringRef, ValueTypeValue, record_types
from .records import pack_record, unpack_record

# Records representing the values of const-only types.
_RECORDS: dict[type[Type], type[tuple[Any, ...]]] = {
    TypeString: StringRef,
    TypeValueType: ValueTypeValue,
    TypeList: ListValue,
}


def _record_class(value_type: Type) -> type[tuple[Any, ...]] | None:
    for type_class, record_class in _RECORDS.items():
        if isinstance(value_type, type_class):
            return record_class
    return None


def _align(offset: int, alignment: int) -> int:
    return (offset + alignment - 1) & ~(alignment - 1)


def const_layout(value_type: Type) -> tuple[int, int, int]:
    """Return the alignment, data size, and storage size of a const value."""
    record_class = _record_class(value_type)
    if record_class is not None:
        record_type = record_types[record_class]
        return record_type.alignment, record_type.data_size, record_type.size
    if isinstance(value_type, TypeStruct):
        alignment = 1
        end = 0
        data_size = 0
        for field_type in value_type.field_types:
            field_alignment, field_data_size, field_size = const_layout(field_type)
            alignment = max(alignment, field_alignment)
            offset = _align(end, field_alignment)
            data_size = offset + field_data_size
            end = offset + field_size
    elif isinstance(value_type, TypeOption):
        alignment, inner_data_size, _ = const_layout(value_type.inner_type)
        data_size = inner_data_size + 1
    elif isinstance(value_type, TypeResource):
        raise ValueError('Resources are not const values')
    else:
        return value_type.alignment, value_type.data_size, value_type.size
    return alignment, data_size, _align(data_size, alignment)


def _field_offsets(value_type: TypeStruct) -> list[int]:
    """Return the const field offsets in declaration order."""
    offsets = []
    end = 0
    for field_type in value_type.field_types:
        alignment, _, size = const_layout(field_type)
        offset = _align(end, alignment)
        offsets.append(offset)
        end = offset + size
    return offsets


def _record_data(record: tuple[Any, ...]) -> bytes:
    # The data and storage sizes of these records are equal.
    return pack_record(record)


class ConstDataWriter:
    """Build a ProgramData buffer from const values.

    type_index returns the ValueTypes index of a type. Equal string bytes,
    list elements, and slots are stored once. The buffer depends only on
    the values added, in order.
    """

    def __init__(self, type_index: Callable[[Type], int]) -> None:
        self._type_index = type_index
        self._data = bytearray()
        self._stored: dict[tuple[int, bytes], int] = {}  # Offsets of stored bytes by alignment and content.

    @property
    def data(self) -> bytes:
        return bytes(self._data)

    def add(self, value_type: Type, value: Any) -> int:
        """Store a value in an aligned slot and return the slot's offset."""
        return self._store(self._slot(value_type, value_type.verify_value(value)), const_layout(value_type)[0])

    def _store(self, data: bytes, alignment: int) -> int:
        key = alignment, data
        if key not in self._stored:
            offset = _align(len(self._data), alignment)
            self._data += bytes(offset - len(self._data)) + data
            self._stored[key] = offset
        return self._stored[key]

    def _slot(self, value_type: Type, value: Any) -> bytes:
        """Encode a value in its storage size, storing referenced bytes first."""
        _, data_size, size = const_layout(value_type)
        return self._encode(value_type, value) + bytes(size - data_size)

    def _encode(self, value_type: Type, value: Any) -> bytes:
        """Encode a verified value in its data size."""
        if isinstance(value_type, TypeString):
            encoded = value.encode('utf-8')
            return _record_data(StringRef(self._store(encoded, 1), len(encoded)) if encoded else StringRef(0, 0))
        if isinstance(value_type, TypeValueType):
            return _record_data(ValueTypeValue(Ref(self._type_index(value))))
        if isinstance(value_type, TypeList):
            if not value:
                return _record_data(ListValue(0, 0))
            elements = b''.join(self._slot(value_type.inner_type, item) for item in value)
            offset = self._store(elements, const_layout(value_type.inner_type)[0])
            return _record_data(ListValue(offset, len(value)))
        if isinstance(value_type, TypeStruct):
            data = bytearray(const_layout(value_type)[1])
            for field_type, field, offset in zip(value_type.field_types, value, _field_offsets(value_type)):
                encoded = self._encode(field_type, field)
                data[offset:offset + len(encoded)] = encoded
            return bytes(data)
        if isinstance(value_type, TypeOption):
            if value is None:
                return bytes(const_layout(value_type)[1])
            return self._encode(value_type.inner_type, value.value) + b'\x01'
        if isinstance(value_type, TypeResource):
            raise ValueError('Resources are not const values')
        return value_type.serialize(value)


def _slice(data: memoryview, offset: int, length: int, what: str) -> memoryview:
    if offset < 0 or offset + length > len(data):
        raise ValueError(f'{what} at offset {offset} with {length} bytes extends past the end of the data')
    return data[offset:offset + length]


def read_const(data: bytes | memoryview, offset: int, value_type: Type, type_at: Callable[[int], Type]) -> Any:
    """Decode the const value in the slot at offset.

    type_at returns the type with a ValueTypes index. Values of opaque
    structs are not checked; linking checks them.
    """
    return ConstDataReader(data, type_at).read(offset, value_type)


class ConstDataReader:
    """Read const slots with an optional shared budget for decoded values."""

    def __init__(self, data: bytes | memoryview, type_at: Callable[[int], Type], *,
                 max_values: int | None = None) -> None:
        if max_values is not None and max_values < 0:
            raise ValueError('max_values must be nonnegative')
        self._reader = _Reader(memoryview(data), type_at, max_values)

    def read(self, offset: int, value_type: Type) -> Any:
        """Decode an aligned slot, consuming the shared value budget."""
        alignment, data_size, size = const_layout(value_type)
        if offset % alignment:
            raise ValueError(f'Slot at offset {offset} is not aligned for `{value_type}`')
        slot = _slice(self._reader.data, offset, size, 'Slot')
        if any(slot[data_size:]):
            raise ValueError('Slot tail padding must be zero')
        return self._reader.decode(value_type, slot[:data_size])


class _Reader:
    def __init__(self, data: memoryview, type_at: Callable[[int], Type], remaining: int | None) -> None:
        self.data = data
        self.type_at = type_at
        self.remaining = remaining

    def decode(self, value_type: Type, data: memoryview) -> Any:
        """Decode a value from exactly its data bytes."""
        if self.remaining is not None:
            if self.remaining == 0:
                raise ValueError('Decoded value budget exhausted')
            self.remaining -= 1
        if isinstance(value_type, TypeString):
            string = unpack_record(StringRef, data)
            if string.length_bytes == 0:
                if string.offset != 0:
                    raise ValueError('An empty string must have offset zero')
                return ''
            try:
                return str(_slice(self.data, string.offset, string.length_bytes, 'String'), 'utf-8')
            except UnicodeDecodeError as error:
                raise ValueError(f'Invalid UTF-8 in string: {error}') from None
        if isinstance(value_type, TypeValueType):
            reference = unpack_record(ValueTypeValue, data)
            return self.type_at(reference.value_type.value)
        if isinstance(value_type, TypeList):
            list_value = unpack_record(ListValue, data)
            if list_value.element_count == 0:
                if list_value.offset != 0:
                    raise ValueError('An empty list must have offset zero')
                return ()
            alignment, data_size, size = const_layout(value_type.inner_type)
            if list_value.offset % alignment:
                raise ValueError(f'List elements at offset {list_value.offset} are not aligned '
                                 f'for `{value_type.inner_type}`')
            elements = _slice(self.data, list_value.offset, list_value.element_count * size, 'List')
            if self.remaining is not None and list_value.element_count > self.remaining:
                raise ValueError('Decoded value budget exhausted')
            items = []
            for index in range(list_value.element_count):
                start = index * size
                if any(elements[start + data_size:start + size]):
                    raise ValueError('List element tail padding must be zero')
                items.append(self.decode(value_type.inner_type, elements[start:start + data_size]))
            return tuple(items)
        if isinstance(value_type, TypeStruct):
            fields = []
            end = 0
            for field_type, offset in zip(value_type.field_types, _field_offsets(value_type)):
                if any(data[end:offset]):
                    raise ValueError('Struct padding must be zero')
                end = offset + const_layout(field_type)[1]
                fields.append(self.decode(field_type, data[offset:end]))
            return tuple(fields)
        if isinstance(value_type, TypeOption):
            present = data[-1]
            if present > 1:
                raise ValueError(f'Invalid option presence byte {present}')
            if present == 0:
                if any(data[:-1]):
                    raise ValueError('Absent option payload must be zero')
                return None
            return Some(self.decode(value_type.inner_type, data[:-1]))
        if isinstance(value_type, TypeResource):
            raise ValueError('Resources are not const values')
        return value_type.deserialize(data)
