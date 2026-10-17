"""Compile-time KNX configuration functions."""

from typing import Any

from wirely import UInt16, ValueType
from wirely.compiler.contracts import knx as contracts
from wirely.knx.address import parse_group_address as parse_address
from wirely.knx.value_types import VALUE_TYPES, get_value_type

from ..native_functions import FunctionServices, RegisteredConstantFunction, constant_function

native_functions: dict[str, RegisteredConstantFunction] = {}


@constant_function(native_functions, contracts.ParseGroupAddress)
def parse_group_address(address: str, *, services: FunctionServices) -> UInt16:
    try:
        return parse_address(address)
    except ValueError as error:
        raise services.error(str(error), argument=0) from error


@constant_function(native_functions, contracts.ValueTypeName)
def value_type_name(ty: ValueType[Any], *, services: FunctionServices) -> str:
    try:
        return get_value_type(ty).name
    except ValueError as error:
        raise services.error(str(error), argument=0) from error


@constant_function(native_functions, contracts.SupportsValueType)
def supports_value_type(ty: ValueType[Any], *, services: FunctionServices) -> bool:
    return ty in VALUE_TYPES
