"""Prepare native configuration during compilation."""

from wirely.native.gpio import GPIOData
from wirely.native_configuration import register_configuration


def prepare_gpio_data(*, context):
    parameters = context.parameters
    chip_device_file = parameters['ChipDeviceFile']
    name = parameters['Name']

    active_state = parameters['ActiveState']
    drive = parameters['Drive']
    bias = parameters['Bias']
    choices = {
        'ActiveState': ('High', 'Low'),
        'Drive': ('PushPull', 'OpenDrain', 'OpenSource'),
        'Bias': ('AsIs', 'Disable', 'PullDown', 'PullUp'),
    }
    for parameter_name, allowed in choices.items():
        if parameters[parameter_name] not in allowed:
            raise context.error(
                f'Unknown {parameter_name}: {parameters[parameter_name]!r}',
                parameter=parameter_name,
            )

    return GPIOData(chip_device_file=chip_device_file, name=name, active_state=active_state, drive=drive, bias=bias)


register_configuration(GPIOData, prepare_gpio_data)
