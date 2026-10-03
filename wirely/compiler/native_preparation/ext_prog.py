"""Prepare native configuration during compilation."""

from wirely.native.ext_prog import LaunchOnEventData
from wirely.compiler.native_configuration import register_configuration


def prepare_launch_on_event_data(*, context):
    parameters = context.parameters
    par_shell_command = parameters['ShellCommand']

    return LaunchOnEventData(shell_command=par_shell_command)


register_configuration(LaunchOnEventData, prepare_launch_on_event_data)
