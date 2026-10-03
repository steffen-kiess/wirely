"""Fixed little-endian encodings for resolved runtime types."""

import datetime

from . import (
    TypeBoolean, TypeDuration, TypeEnum, TypeInt, TypeMonotonicTime,
    TypeOption, TypeTimeOfDay, TypeUnit,
)
from .option import Some
from .time import Duration, MonotonicTime
from .unit import Unit


_DAY_NS = 86_400_000_000_000


def _check_runtime(value_type):
    """Reject types without a resolved runtime representation."""
    if value_type.contains_parameters or not value_type.is_runtime_type:
        raise ValueError(f'Type {value_type} has no runtime encoding')


def _alignment(value_type):
    """Return the required byte alignment of a runtime value."""
    _check_runtime(value_type)
    if isinstance(value_type, TypeOption):
        return value_type.inner_type.alignment
    if isinstance(value_type, TypeEnum):
        return value_type.underlying_type.alignment
    if isinstance(value_type, (TypeUnit, TypeBoolean)):
        return 1
    if isinstance(value_type, TypeInt):
        if value_type.bits not in (8, 16, 32, 64):
            raise ValueError(f'Unsupported integer width {value_type.bits}')
        return value_type.bits // 8
    if isinstance(value_type, (TypeDuration, TypeMonotonicTime, TypeTimeOfDay)):
        return 8
    raise ValueError(f'Unsupported runtime type {value_type}')


def _data_size(value_type):
    """Return the encoded byte length, excluding tail padding."""
    _check_runtime(value_type)
    if isinstance(value_type, TypeOption):
        return value_type.inner_type.data_size + 1
    if isinstance(value_type, TypeEnum):
        return value_type.underlying_type.data_size
    if isinstance(value_type, TypeUnit):
        return 0
    if isinstance(value_type, TypeMonotonicTime):
        return 16
    return _alignment(value_type)


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
    if isinstance(value_type, TypeOption):
        if value is None:
            return bytes(length)
        return value_type.inner_type.serialize(value.value) + b'\x01'
    if isinstance(value_type, TypeEnum):
        return value_type.underlying_type.serialize(value.value)
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
        if value.tzinfo is not None:
            raise ValueError('TimeOfDay requires a time without timezone information')
        nanoseconds = ((value.hour * 60 + value.minute) * 60 + value.second) * 1_000_000_000
        nanoseconds += value.microsecond * 1_000
        return _integer_bytes(nanoseconds, 8)
    raise ValueError(f'Unsupported runtime type {value_type}')


def _deserialize(value_type, data):
    """Decode exact data bytes, rejecting noncanonical flags and payloads."""
    length = value_type.data_size
    data = memoryview(data).tobytes()
    if len(data) != length:
        raise ValueError(f'Expected {length} bytes for {value_type}, got {len(data)}')
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
        return value_type.python_type(value_type.underlying_type.deserialize(data))
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
        if nanoseconds >= _DAY_NS:
            raise ValueError('TimeOfDay requires a time before 24:00')
        if nanoseconds % 1_000:
            raise ValueError('TimeOfDay requires microsecond precision')
        seconds, microsecond = divmod(nanoseconds // 1_000, 1_000_000)
        hours, seconds = divmod(seconds, 3_600)
        minutes, seconds = divmod(seconds, 60)
        return datetime.time(hours, minutes, seconds, microsecond)
    raise ValueError(f'Unsupported runtime type {value_type}')
