"""Build KNX connection configurations from named node parameters."""

import asyncio
import signal
import socket
import subprocess
from typing import Any

import xknx

from wirely.knx.connection_adapter import StreamConnectionConfig


class ConnectionConfigError(ValueError):
    pass


def _check_text(name, value):
    if value == '' or '\0' in value:
        raise ConnectionConfigError(f'{name} must be a non-empty string without NUL')


def _ssh_connect_callback(target, path):
    async def connect_ssh(loop, protocol_factory):
        local_socket, remote_socket = socket.socketpair()

        # Ignore SIGINT in ssh so the service can shut down cleanly.
        command = [
            'ssh', '--', target,
            'printf "X" && socat STDIO UNIX-CONNECT:\''
            + path.replace("'", "'\\''") + '\'',
        ]
        try:
            subprocess.Popen(
                command, stdin=remote_socket.fileno(), stdout=remote_socket.fileno(),
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
        except BaseException:
            local_socket.close()
            raise
        finally:
            remote_socket.close()

        result: list[Any] = [None, None]  # The error and the received byte.
        event = asyncio.Event()

        def reader():
            try:
                try:
                    result[1] = local_socket.recv(1)
                except Exception as error:
                    result[0] = error
            finally:
                event.set()

        try:
            loop.add_reader(local_socket, reader)
            try:
                await event.wait()
                if result[0] is not None:
                    raise result[0]
                if result[1] != b'X':
                    raise ConnectionError(
                        f'Got unexpected result from remote side: {result[1]!r}'
                    )
            finally:
                loop.remove_reader(local_socket)
            return await loop.create_unix_connection(
                protocol_factory, sock=local_socket
            )
        except BaseException:
            local_socket.close()
            raise

    return connect_ssh


def create_connection_config(*, host=None, protocol=None, port=None,
                             unix_path=None, ssh_target=None,
                             individual_address=None):
    """Select one connection mode from the supplied KNX node parameters."""
    for name, value in (
        ('Host', host), ('UnixPath', unix_path),
        ('SSHTarget', ssh_target), ('IndividualAddress', individual_address),
    ):
        if value is not None:
            _check_text(name, value)

    if unix_path is not None and not unix_path.startswith('/'):
        raise ConnectionConfigError('UnixPath must be an absolute path')
    if ssh_target is not None and ssh_target.startswith('-'):
        raise ConnectionConfigError('SSHTarget cannot start with a hyphen')

    if protocol is None:
        if unix_path is None:
            raise ConnectionConfigError('Protocol is required when UnixPath is absent')
        protocol = 'Unix'
    elif protocol not in ('TCP', 'UDP', 'Unix', 'Auto'):
        raise ConnectionConfigError(f'Unknown Protocol: {protocol!r}')

    if protocol == 'Auto':
        if any(value is not None for value in (
            host, port, unix_path, ssh_target, individual_address,
        )):
            raise ConnectionConfigError('Protocol Auto cannot be combined with other connection parameters')
        return xknx.io.ConnectionConfig(
            connection_type=xknx.io.ConnectionType.AUTOMATIC,
        )

    if protocol in ('TCP', 'UDP'):
        if host is None:
            raise ConnectionConfigError(f'Protocol {protocol} requires Host')
        if unix_path is not None or ssh_target is not None:
            raise ConnectionConfigError('Host cannot be combined with UnixPath or SSHTarget')
        if protocol == 'UDP' and individual_address is not None:
            raise ConnectionConfigError('IndividualAddress is not supported with UDP')
        if port is None:
            port = 3671
        if port < 1 or port > 65535:
            raise ConnectionConfigError('Port must be between 1 and 65535')
        return xknx.io.ConnectionConfig(
            connection_type=(xknx.io.ConnectionType.TUNNELING_TCP
                             if protocol == 'TCP'
                             else xknx.io.ConnectionType.TUNNELING),
            gateway_ip=host,
            gateway_port=port,
            individual_address=_parse_individual_address(individual_address),
        )

    if host is not None or port is not None:
        raise ConnectionConfigError('Host and Port are not supported with Protocol Unix')

    address = _parse_individual_address(individual_address)
    path = unix_path if unix_path is not None else '/run/knxnet'
    if ssh_target is not None:
        connect_cb = _ssh_connect_callback(ssh_target, path)
    else:
        connect_cb = lambda loop, factory: loop.create_unix_connection(
            factory, path=path
        )
    return StreamConnectionConfig(
        connect_cb=connect_cb,
        individual_address=address,
    )


def _parse_individual_address(value):
    if value is None:
        return None
    try:
        return xknx.telegram.address.IndividualAddress(value)
    except xknx.exceptions.exception.CouldNotParseAddress as error:
        raise ConnectionConfigError(f'Invalid IndividualAddress: {value!r}') from error
