"""Prepare native configuration during compilation."""

import os

from wirely.native.two_n import get_username_password, TwoNDeviceData
from wirely.compiler.native_configuration import register_configuration


def prepare_two_n_device_data(*, context):
    parameters = context.parameters
    par_hostname = parameters['Host']
    par_port = parameters['Port']
    par_tlsa = parameters['TLSA']
    par_pwdfile = parameters['UsernamePasswordFile']
    if not os.path.isabs(par_pwdfile):
        raise context.error(
            'UsernamePasswordFile must be absolute; use '
            'Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt")',
            parameter='UsernamePasswordFile',
        )
    try:
        username, password = get_username_password(par_pwdfile)
    except (OSError, ValueError) as error:
        raise context.error(str(error), parameter='UsernamePasswordFile') from error

    return TwoNDeviceData(hostname=par_hostname, port=par_port, tlsa=par_tlsa, username=username, password=password)


register_configuration(TwoNDeviceData, prepare_two_n_device_data)
