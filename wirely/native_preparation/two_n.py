"""Prepare native configuration during compilation."""

from wirely.native.two_n import get_username_password, TwoNDeviceData, TwoNTimeoutData, TwoNKeyData, TwoNIOData, TwoNSwitchData, TwoNCodeData
from wirely.native_configuration import register_configuration


def prepare_two_n_device_data(*, context):
    parameters = context.parameters
    par_hostname = parameters['Host']
    par_port = parameters['Port']
    par_tlsa = parameters['TLSA']
    par_pwdfile = context.resolve_path(
        parameters['UsernamePasswordFile'], parameter='UsernamePasswordFile',
    )
    try:
        username, password = get_username_password(par_pwdfile)
    except (OSError, ValueError) as error:
        raise context.error(str(error), parameter='UsernamePasswordFile') from error

    return TwoNDeviceData(hostname=par_hostname, port=par_port, tlsa=par_tlsa, username=username, password=password)


def prepare_two_n_timeout_data(*, context):
    parameters = context.parameters
    par_timeout = parameters['Timeout']

    return TwoNTimeoutData(timeout=par_timeout)


def prepare_two_n_key_data(*, context):
    parameters = context.parameters
    par_key = parameters['Key']

    return TwoNKeyData(key=par_key)


def prepare_two_n_io_data(*, context):
    parameters = context.parameters
    par_port = parameters['Port']

    return TwoNIOData(port=par_port)


def prepare_two_n_switch_data(*, context):
    parameters = context.parameters
    par_switch_number = int(parameters['SwitchNumber'])

    return TwoNSwitchData(switch_number=par_switch_number)


def prepare_two_n_code_data(*, context):
    parameters = context.parameters
    par_code = parameters['Code']

    if len(par_code) > 15:
        raise context.error('Code exceeds 15 digits', parameter='Code')
    for c in par_code:
        if c < '0' or c > '9':
            raise context.error(f'Got non-digit in code {par_code!r}', parameter='Code')

    return TwoNCodeData(code=par_code)


register_configuration(TwoNDeviceData, prepare_two_n_device_data)
register_configuration(TwoNTimeoutData, prepare_two_n_timeout_data)
register_configuration(TwoNKeyData, prepare_two_n_key_data)
register_configuration(TwoNIOData, prepare_two_n_io_data)
register_configuration(TwoNSwitchData, prepare_two_n_switch_data)
register_configuration(TwoNCodeData, prepare_two_n_code_data)
