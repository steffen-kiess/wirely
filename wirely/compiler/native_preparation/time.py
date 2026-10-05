"""Prepare native configuration during compilation."""

import wirely as blocks

from wirely.native.time import TriggerAtData
from wirely.compiler.native_configuration import register_configuration


def prepare_trigger_at_data(*, context):
    parameters = context.parameters
    par_type = parameters['OutputType']

    if 'OutputValue' not in parameters:
        value = blocks.Unit()
        if par_type != blocks.value_types.unit:
            raise context.error(f'Trigger type is not unit ({par_type}) but no value is given', parameter='OutputValue')
    else:
        value = parameters['OutputValue']

    try:
        value = par_type.validate(value)
    except ValueError as error:
        raise context.error(str(error), parameter='OutputValue') from error

    return TriggerAtData(value=value)


register_configuration(TriggerAtData, prepare_trigger_at_data)
