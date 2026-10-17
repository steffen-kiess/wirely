"""Compile-time KNX configuration functions."""

from typing import Any

import wirely
from wirely import UInt16, ValueType
from wirely.compiler.contracts import knx as contracts

from ..native_functions import FunctionServices, RegisteredConstantFunction, constant_function

native_functions: dict[str, RegisteredConstantFunction] = {}


@constant_function(native_functions, contracts.ParseGroupAddress)
def parse_group_address(address: str, *, services: FunctionServices) -> UInt16:
    import xknx

    try:
        return UInt16(xknx.telegram.GroupAddress(address).raw)
    except xknx.exceptions.CouldNotParseAddress as error:
        raise services.error(str(error), argument=0) from error


@constant_function(native_functions, contracts.ValueTypeName)
def value_type_name(ty: ValueType[Any], *, services: FunctionServices) -> str:
    names = {
        wirely.value_types.boolean: 'binary',
        wirely.value_types.int16: '2byte_signed',
        wirely.value_types.uint16: '2byte_unsigned',
        wirely.value_types.int32: '4byte_signed',
        wirely.value_types.uint32: '4byte_unsigned',
    }
    if ty not in names:
        raise services.error(f'Unsupported type for KNX: {ty}', argument=0)
    return names[ty]


@constant_function(native_functions, contracts.SupportsValueType)
def supports_value_type(ty: ValueType[Any], *, services: FunctionServices) -> bool:
    return ty in (
        wirely.value_types.boolean, wirely.value_types.uint16,
    )
