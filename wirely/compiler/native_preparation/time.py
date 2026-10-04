"""Prepare native configuration during compilation."""

import wirely as blocks

from wirely.native.time import DebounceStableData, IsInDailyIntervalData, TriggerAtData
from wirely.compiler.native_configuration import register_configuration


def prepare_debounce_stable_data(*, context):
    parameters = context.parameters
    time = parameters['Duration']

    return DebounceStableData(time=time)


def prepare_is_in_daily_interval_data(*, context):
    parameters = context.parameters
    par_start = parameters['Start']
    par_end = parameters['End']

    if par_start == par_end:
        raise context.error('Start and end must differ', parameter='End')
    return IsInDailyIntervalData(start=par_start, end=par_end)


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


register_configuration(DebounceStableData, prepare_debounce_stable_data)
register_configuration(IsInDailyIntervalData, prepare_is_in_daily_interval_data)
register_configuration(TriggerAtData, prepare_trigger_at_data)
