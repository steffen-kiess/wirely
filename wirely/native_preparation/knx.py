"""Prepare native configuration during compilation."""

import wirely as blocks

from wirely.native.knx import KnxData
from wirely.native_configuration import register_configuration


def prepare_knx_data(*, context):
    parameters = context.parameters
    group_address_str = parameters['GroupAddress']
    par_type = parameters['Type']

    import xknx
    try:
        group_address = xknx.telegram.GroupAddress(group_address_str)
    except xknx.exceptions.CouldNotParseAddress as error:
        raise context.error(str(error), parameter='GroupAddress') from error
    group_address_raw = group_address.raw

    if par_type == blocks.value_types.boolean:
        value_type_knx = "binary"
    elif par_type == blocks.value_types.int16:
        value_type_knx = "2byte_signed"
    elif par_type == blocks.value_types.uint16:
        value_type_knx = "2byte_unsigned"
    elif par_type == blocks.value_types.int32:
        value_type_knx = "4byte_signed"
    elif par_type == blocks.value_types.uint32:
        value_type_knx = "4byte_unsigned"
    else:
        raise context.error(f'Unsupported type for KNX: {par_type}', parameter='Type')

    return KnxData(group_address_raw=group_address_raw, value_type=par_type, value_type_knx=value_type_knx)


register_configuration(KnxData, prepare_knx_data)
