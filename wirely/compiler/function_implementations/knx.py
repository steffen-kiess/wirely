"""Compile-time KNX configuration functions."""

import wirely


def parse_group_address(*, context):
    import xknx

    try:
        return xknx.telegram.GroupAddress(context.arguments['address']).raw
    except xknx.exceptions.CouldNotParseAddress as error:
        raise context.error(str(error), argument='address') from error


def value_type_name(*, context):
    ty = context.arguments['type']
    names = {
        wirely.value_types.boolean: 'binary',
        wirely.value_types.int16: '2byte_signed',
        wirely.value_types.uint16: '2byte_unsigned',
        wirely.value_types.int32: '4byte_signed',
        wirely.value_types.uint32: '4byte_unsigned',
    }
    if ty not in names:
        raise context.error(f'Unsupported type for KNX: {ty}', argument='type')
    return names[ty]


def supports_value_type(*, context):
    return context.arguments['type'] in (
        wirely.value_types.boolean, wirely.value_types.uint16,
    )
