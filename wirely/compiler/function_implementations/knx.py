"""Compile-time KNX configuration functions."""

import wirely

from ..native_functions import NativeFunctionContext


def parse_group_address(*, context: NativeFunctionContext) -> int:
    import xknx

    address, = context.arguments
    try:
        return xknx.telegram.GroupAddress(address).raw
    except xknx.exceptions.CouldNotParseAddress as error:
        raise context.error(str(error), argument=0) from error


def value_type_name(*, context: NativeFunctionContext) -> str:
    ty, = context.arguments
    names = {
        wirely.value_types.boolean: 'binary',
        wirely.value_types.int16: '2byte_signed',
        wirely.value_types.uint16: '2byte_unsigned',
        wirely.value_types.int32: '4byte_signed',
        wirely.value_types.uint32: '4byte_unsigned',
    }
    if ty not in names:
        raise context.error(f'Unsupported type for KNX: {ty}', argument=0)
    return names[ty]


def supports_value_type(*, context: NativeFunctionContext) -> bool:
    ty, = context.arguments
    return ty in (
        wirely.value_types.boolean, wirely.value_types.uint16,
    )
