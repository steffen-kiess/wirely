"""Validate KNX connection settings, at compile time and when loading."""

import dataclasses
from typing import Literal

from wirely import UInt16
from wirely.knx.address import parse_individual_address


Protocol = Literal['TCP', 'UDP', 'Unix', 'Auto']


class ConnectionConfigError(ValueError):
    def __init__(self, message: str, parameter: str | None = None) -> None:
        super().__init__(message)
        # The parameter the error is about, None for an invalid combination.
        self.parameter = parameter


@dataclasses.dataclass(frozen=True)
class ConnectionSettings:
    protocol: Protocol
    # Set for TCP and UDP.
    host: str | None
    port: int | None
    # Set for Unix.
    unix_path: str | None
    ssh_target: str | None
    individual_address: UInt16 | None


def _check_text(name: str, value: str) -> None:
    if value == '' or '\0' in value:
        raise ConnectionConfigError(f'{name} must be a non-empty string without NUL', name)


def check_connection(*, host: str | None = None, protocol: str | None = None, port: int | None = None,
                     unix_path: str | None = None, ssh_target: str | None = None,
                     individual_address: str | None = None) -> ConnectionSettings:
    """Select one connection mode from the supplied KNX node parameters."""
    for name, value in (
        ('Host', host), ('UnixPath', unix_path),
        ('SSHTarget', ssh_target), ('IndividualAddress', individual_address),
    ):
        if value is not None:
            _check_text(name, value)

    if unix_path is not None and not unix_path.startswith('/'):
        raise ConnectionConfigError('UnixPath must be an absolute path', 'UnixPath')
    if ssh_target is not None and ssh_target.startswith('-'):
        raise ConnectionConfigError('SSHTarget cannot start with a hyphen', 'SSHTarget')
    address = None
    if individual_address is not None:
        try:
            address = parse_individual_address(individual_address)
        except ValueError as error:
            raise ConnectionConfigError(str(error), 'IndividualAddress') from error

    if protocol is None:
        if unix_path is None:
            raise ConnectionConfigError('Protocol is required when UnixPath is absent')
        protocol = 'Unix'

    if protocol == 'Auto':
        if any(value is not None for value in (
            host, port, unix_path, ssh_target, individual_address,
        )):
            raise ConnectionConfigError('Protocol Auto cannot be combined with other connection parameters')
        return ConnectionSettings('Auto', None, None, None, None, None)

    if protocol == 'TCP' or protocol == 'UDP':
        if host is None:
            raise ConnectionConfigError(f'Protocol {protocol} requires Host')
        if unix_path is not None or ssh_target is not None:
            raise ConnectionConfigError('Host cannot be combined with UnixPath or SSHTarget')
        if protocol == 'UDP' and individual_address is not None:
            raise ConnectionConfigError('IndividualAddress is not supported with UDP')
        if port is None:
            port = 3671
        if port < 1 or port > 65535:
            raise ConnectionConfigError('Port must be between 1 and 65535', 'Port')
        return ConnectionSettings('TCP' if protocol == 'TCP' else 'UDP', host, port, None, None, address)

    if protocol != 'Unix':
        raise ConnectionConfigError(f'Unknown Protocol: {protocol!r}', 'Protocol')
    if host is not None or port is not None:
        raise ConnectionConfigError('Host and Port are not supported with Protocol Unix')
    path = unix_path if unix_path is not None else '/run/knxnet'
    return ConnectionSettings('Unix', None, None, path, ssh_target, address)


def describe_connection(settings: ConnectionSettings) -> str:
    """Describe a connection for log messages."""
    if settings.protocol == 'Auto':
        return 'an automatically discovered interface'
    if settings.protocol == 'TCP' or settings.protocol == 'UDP':
        return f'{settings.host}:{settings.port} over {settings.protocol}'
    if settings.ssh_target is not None:
        return f'{settings.unix_path} on {settings.ssh_target}'
    return f'{settings.unix_path}'
