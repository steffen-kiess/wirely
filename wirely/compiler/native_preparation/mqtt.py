"""Prepare native configuration during compilation."""

from wirely.native.mqtt import MqttData
from wirely.compiler.native_configuration import register_configuration


def prepare_mqtt_data(*, context):
    parameters = context.parameters
    par_type = parameters['Type']
    par_topic = parameters['Topic']
    if 'Format' in parameters:
        # TODO: Verify format here
        par_format = parameters['Format']
    else:
        par_format = None

    if 'Template' in parameters:
        par_template = parameters['Template']
        try:
            template_prefix, template_suffix = MqttData.parse_template(par_template)
        except ValueError as error:
            raise context.error(str(error), parameter='Template') from error
    else:
        template_prefix = template_suffix = ''

    if not par_type.supports_json:
        raise context.error(f'Unsupported type for MQTT: {par_type}', parameter='Type')
    # TODO: Verify format here

    return MqttData(value_type=par_type, topic=par_topic, format=par_format, template_prefix=template_prefix, template_suffix=template_suffix)


register_configuration(MqttData, prepare_mqtt_data)
