"""Compile-time KNX configuration functions."""

from typing import Any

from wirely import Some, UInt16
from wirely.generated.enums import KNXProtocol
from wirely.runtime.value_types import ValueType
from wirely.knx.address import parse_group_address as parse_address
from wirely.knx.settings import ConnectionConfigError, check_connection as check_connection_settings
from wirely.knx.value_types import VALUE_TYPES, get_value_type

from ..native_functions import FunctionServices


def parse_group_address(address: str, *, services: FunctionServices) -> UInt16:
    try:
        return parse_address(address)
    except ValueError as error:
        raise services.error(str(error), argument=0) from error


def value_type_name(ty: ValueType[Any], *, services: FunctionServices) -> str:
    try:
        return get_value_type(ty).name
    except ValueError as error:
        raise services.error(str(error), argument=0) from error


def supports_value_type(ty: ValueType[Any], *, services: FunctionServices) -> bool:
    return ty in VALUE_TYPES


# The arguments of CheckConnection, by parameter name.
_CONNECTION_ARGUMENTS = {name: index for index, name in enumerate(
    ('Host', 'Protocol', 'Port', 'UnixPath', 'SSHTarget', 'IndividualAddress'))}


def check_connection(host: Some[str] | None, protocol: Some[KNXProtocol] | None, port: Some[UInt16] | None,
                     unix_path: Some[str] | None, ssh_target: Some[str] | None,
                     individual_address: Some[str] | None, *, services: FunctionServices) -> bool:
    def value(option: Some[str] | None) -> str | None:
        return None if option is None else option.value

    protocol_name = None
    if protocol is not None:
        protocol_name = protocol.value.name
        if protocol_name is None:
            raise services.error(f'Unsupported KNX protocol {int(protocol.value)}',
                                 argument=_CONNECTION_ARGUMENTS['Protocol'])
    try:
        check_connection_settings(
            host=value(host), protocol=protocol_name, port=None if port is None else port.value.value,
            unix_path=value(unix_path), ssh_target=value(ssh_target), individual_address=value(individual_address))
    except ConnectionConfigError as error:
        argument = None if error.parameter is None else _CONNECTION_ARGUMENTS[error.parameter]
        raise services.error(str(error), argument=argument) from error
    return True
