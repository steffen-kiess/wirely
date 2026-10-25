"""KNX datapoint types and their Wirely conversions."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from wirely import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32
from wirely.runtime import value_types
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.types.integers import Integer
from wirely.runtime.extensions import NativeTypes


I = TypeVar('I', bound=Integer)


@dataclass(frozen=True)
class KNXValueType:
    name: str
    from_knx: Callable[[Any, NativeTypes], DynamicValue]
    to_knx: Callable[[DynamicValue, NativeTypes], bool | int]


def _integer(value_type: ValueType[I], integer_class: type[I], name: str) -> KNXValueType:
    def from_knx(value: Any, native_types: NativeTypes) -> DynamicValue:
        return value_type.from_native(integer_class(value), native_types=native_types)

    def to_knx(value: DynamicValue, native_types: NativeTypes) -> int:
        native = value.extract(value_type, native_types=native_types)
        return native.value

    return KNXValueType(name, from_knx, to_knx)


VALUE_TYPES = {
    value_types.boolean: KNXValueType('binary', lambda value, context: value_types.boolean.from_native(value, native_types=context),
                                    lambda value, context: value.extract(value_types.boolean, native_types=context)),
    value_types.int8: _integer(value_types.int8, Int8, '1byte_signed'),
    value_types.uint8: _integer(value_types.uint8, UInt8, '1byte_unsigned'),
    value_types.int16: _integer(value_types.int16, Int16, '2byte_signed'),
    value_types.uint16: _integer(value_types.uint16, UInt16, '2byte_unsigned'),
    value_types.int32: _integer(value_types.int32, Int32, '4byte_signed'),
    value_types.uint32: _integer(value_types.uint32, UInt32, '4byte_unsigned'),
    value_types.int64: _integer(value_types.int64, Int64, '8byte_signed'),
}


def get_value_type(value_type: ValueType[Any], name: str | None = None) -> KNXValueType:
    try:
        entry = VALUE_TYPES[value_type]
    except KeyError:
        raise ValueError(f'Unsupported type for KNX: `{value_type}`') from None
    if name is not None and name != entry.name:
        raise ValueError(f'Expected KNX type `{entry.name}` for `{value_type}`, got `{name}`')
    return entry
