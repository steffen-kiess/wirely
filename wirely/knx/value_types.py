"""KNX datapoint types and their Wirely conversions."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from wirely import DynamicValue, Int16, Int32, UInt16, UInt32, ValueType, value_types
from wirely.types.integers import Integer


I = TypeVar('I', bound=Integer)


@dataclass(frozen=True)
class KNXValueType:
    name: str
    from_knx: Callable[[Any], DynamicValue]
    to_knx: Callable[[DynamicValue], bool | int]


def _integer(value_type: ValueType[I], integer_class: type[I], name: str) -> KNXValueType:
    def from_knx(value: Any) -> DynamicValue:
        return value_type.from_native(integer_class(value))

    def to_knx(value: DynamicValue) -> int:
        native = value.extract(value_type)
        return native.value

    return KNXValueType(name, from_knx, to_knx)


VALUE_TYPES = {
    value_types.boolean: KNXValueType('binary', value_types.boolean.from_native,
                                    lambda value: value.extract(value_types.boolean)),
    value_types.int16: _integer(value_types.int16, Int16, '2byte_signed'),
    value_types.uint16: _integer(value_types.uint16, UInt16, '2byte_unsigned'),
    value_types.int32: _integer(value_types.int32, Int32, '4byte_signed'),
    value_types.uint32: _integer(value_types.uint32, UInt32, '4byte_unsigned'),
}


def get_value_type(value_type: ValueType[Any], name: str | None = None) -> KNXValueType:
    try:
        entry = VALUE_TYPES[value_type]
    except KeyError:
        raise ValueError(f'Unsupported type for KNX: {value_type}') from None
    if name is not None and name != entry.name:
        raise ValueError(f'Expected KNX type {entry.name!r} for {value_type}, got {name!r}')
    return entry
