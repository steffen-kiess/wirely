"""Prepare native configuration during compilation."""

import wirely as blocks

from wirely.native.memory import ToggleFlipFlopTriggerData
from wirely.native_configuration import register_configuration


def prepare_toggle_flip_flop_trigger_data(*, context):
    parameters = context.parameters
    par_type = parameters['OutputType']

    if 'OutputValue' not in parameters:
        output_value = blocks.Unit()
        if par_type != blocks.value_types.unit:
            raise context.error(f'Trigger type is not unit ({par_type}) but no value is given', parameter='OutputValue')
    else:
        output_value = parameters['OutputValue']

    try:
        output_value = par_type.validate(output_value)
    except ValueError as error:
        raise context.error(str(error), parameter='OutputValue') from error

    minimum_delay = parameters['MinimumDelay']

    return ToggleFlipFlopTriggerData(output_value=output_value, minimum_delay=minimum_delay)


register_configuration(ToggleFlipFlopTriggerData, prepare_toggle_flip_flop_trigger_data)
