"""Fixed little-endian encodings for resolved runtime types."""

from . import (
    TypeBoolean, TypeDuration, TypeEnum, TypeInt, TypeMonotonicTime,
    TypeOption, TypeStruct, TypeTimeOfDay, TypeUnit,
)
from .option import Some
from .time import Duration, MonotonicTime, TimeOfDay
from .unit import Unit


def _check_runtime(value_type):
    """Reject types without an eligible runtime representation."""
    if not value_type.is_runtime_type:
        raise ValueError(f'Type {value_type} is not a runtime type: {value_type.runtime_type_error()}')


def _layout(value_type):
    """Calculate alignment, data size, and storage size without checking limits."""
    if isinstance(value_type, TypeStruct):
        alignment = 1
        end = 0
        data_size = 0
        for field in value_type.fields.values():
            field_alignment, field_data_size, field_size = _layout(field)
            alignment = max(alignment, field_alignment)
            offset = (end + field_alignment - 1) & ~(field_alignment - 1)
            data_size = offset + field_data_size
            end = offset + field_size
    elif isinstance(value_type, TypeOption):
        alignment, inner_data_size, _ = _layout(value_type.inner_type)
        data_size = inner_data_size + 1
    elif isinstance(value_type, TypeEnum):
        return _layout(value_type.underlying_type)
    elif isinstance(value_type, TypeUnit):
        alignment, data_size = 1, 0
    elif isinstance(value_type, TypeBoolean):
        alignment, data_size = 1, 1
    elif isinstance(value_type, TypeInt):
        if value_type.bits not in (8, 16, 32, 64):
            raise ValueError(f'Unsupported integer width {value_type.bits}')
        alignment = data_size = value_type.bits // 8
    elif isinstance(value_type, TypeMonotonicTime):
        alignment, data_size = 8, 16
    elif isinstance(value_type, (TypeDuration, TypeTimeOfDay)):
        alignment = data_size = 8
    else:
        raise ValueError(f'Unsupported runtime type {value_type}')
    size = (data_size + alignment - 1) & ~(alignment - 1)
    return alignment, data_size, size


def _alignment(value_type):
    """Return the required byte alignment of a runtime value."""
    _check_runtime(value_type)
    return _layout(value_type)[0]


def _data_size(value_type):
    """Return the encoded byte length, excluding tail padding."""
    _check_runtime(value_type)
    return _layout(value_type)[1]


def _struct_offsets(value_type):
    offsets = {}
    end = 0
    for name, field_type in value_type.fields.items():
        alignment, _, size = _layout(field_type)
        offset = (end + alignment - 1) & ~(alignment - 1)
        offsets[name] = offset
        end = offset + size
    return offsets


def _integer_bytes(value, size, *, signed=False):
    """Encode an integer, reporting values outside its wire range."""
    try:
        return value.to_bytes(size, 'little', signed=signed)
    except OverflowError as error:
        raise ValueError(f'Integer {value} does not fit in {size} bytes') from error


def _serialize(value_type, value):
    """Encode a validated value without tail padding."""
    length = value_type.data_size
    value = value_type.verify_value(value)
    if isinstance(value_type, TypeStruct):
        data = bytearray(length)
        for name, offset in _struct_offsets(value_type).items():
            encoded = value_type.fields[name].serialize(value[name])
            data[offset:offset + len(encoded)] = encoded
        return bytes(data)
    if isinstance(value_type, TypeOption):
        if value is None:
            return bytes(length)
        return value_type.inner_type.serialize(value.value) + b'\x01'
    if isinstance(value_type, TypeEnum):
        return value_type.underlying_type.serialize(value_type.verify_value(value))
    if isinstance(value_type, TypeUnit):
        return b''
    if isinstance(value_type, TypeBoolean):
        return bytes([int(value)])
    if isinstance(value_type, TypeInt):
        return _integer_bytes(value, length, signed=value_type.is_signed)
    if isinstance(value_type, TypeDuration):
        return _integer_bytes(value.duration_ns, 8)
    if isinstance(value_type, TypeMonotonicTime):
        return _integer_bytes(value.generation, 8) + _integer_bytes(value.time_ns, 8)
    if isinstance(value_type, TypeTimeOfDay):
        return _integer_bytes(value.nanoseconds, 8)
    raise ValueError(f'Unsupported runtime type {value_type}')


def _deserialize(value_type, data):
    """Decode exact data bytes, rejecting noncanonical flags and payloads."""
    length = value_type.data_size
    data = memoryview(data).tobytes()
    if len(data) != length:
        raise ValueError(f'Expected {length} bytes for {value_type}, got {len(data)}')
    if isinstance(value_type, TypeStruct):
        fields = {}
        end = 0
        for name, offset in _struct_offsets(value_type).items():
            if any(data[end:offset]):
                raise ValueError('Struct padding must be zero')
            field_type = value_type.fields[name]
            end = offset + field_type.data_size
            fields[name] = field_type.deserialize(data[offset:end])
        return value_type.make_value(fields)
    if isinstance(value_type, TypeOption):
        present = data[-1]
        if present > 1:
            raise ValueError(f'Invalid option presence byte {present}')
        if present == 0:
            if any(data[:-1]):
                raise ValueError('Absent option payload must be zero')
            return None
        return Some(value_type.inner_type.deserialize(data[:-1]))
    if isinstance(value_type, TypeEnum):
        return value_type.verify_value(value_type.underlying_type.deserialize(data))
    if isinstance(value_type, TypeUnit):
        return Unit()
    if isinstance(value_type, TypeBoolean):
        if data[0] > 1:
            raise ValueError(f'Invalid boolean byte {data[0]}')
        return bool(data[0])
    if isinstance(value_type, TypeInt):
        return int.from_bytes(data, 'little', signed=value_type.is_signed)
    if isinstance(value_type, TypeDuration):
        return Duration(duration_ns=int.from_bytes(data, 'little'))
    if isinstance(value_type, TypeMonotonicTime):
        return MonotonicTime(int.from_bytes(data[:8], 'little'), int.from_bytes(data[8:], 'little'))
    if isinstance(value_type, TypeTimeOfDay):
        nanoseconds = int.from_bytes(data, 'little')
        if nanoseconds > TimeOfDay.END_NS:
            raise ValueError('TimeOfDay requires a time up to 24:00')
        return TimeOfDay(nanoseconds)
    raise ValueError(f'Unsupported runtime type {value_type}')
