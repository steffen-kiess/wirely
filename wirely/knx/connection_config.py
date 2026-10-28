"""Build KNX connection configurations from named node parameters."""

import asyncio
import signal
import socket
import subprocess
from collections.abc import Callable
from typing import Any

import xknx

from wirely.knx.connection_adapter import ConnectCallback, StreamConnectionConfig
from wirely.knx.settings import check_connection


def _ssh_connect_callback(target: str, path: str) -> ConnectCallback:
    async def connect_ssh(loop: asyncio.AbstractEventLoop,
                          protocol_factory: Callable[[], asyncio.Protocol]) -> tuple[Any, Any]:
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

        def reader() -> None:
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


def create_connection_config(*, host: str | None = None, protocol: str | None = None, port: int | None = None,
                             unix_path: str | None = None, ssh_target: str | None = None,
                             individual_address: str | None = None) -> xknx.io.ConnectionConfig:
    """Select one connection mode from the supplied KNX node parameters."""
    settings = check_connection(host=host, protocol=protocol, port=port, unix_path=unix_path,
                                ssh_target=ssh_target, individual_address=individual_address)
    address = (None if settings.individual_address is None
               else xknx.telegram.address.IndividualAddress(settings.individual_address.value))

    if settings.protocol == 'Auto':
        return xknx.io.ConnectionConfig(
            connection_type=xknx.io.ConnectionType.AUTOMATIC,
        )

    if settings.protocol == 'TCP' or settings.protocol == 'UDP':
        assert settings.port is not None
        return xknx.io.ConnectionConfig(
            connection_type=(xknx.io.ConnectionType.TUNNELING_TCP
                             if settings.protocol == 'TCP'
                             else xknx.io.ConnectionType.TUNNELING),
            gateway_ip=settings.host,
            gateway_port=settings.port,
            individual_address=address,
        )

    path = settings.unix_path
    assert path is not None
    if settings.ssh_target is not None:
        connect_cb = _ssh_connect_callback(settings.ssh_target, path)
    else:
        connect_cb = lambda loop, factory: loop.create_unix_connection(
            factory, path=path
        )
    return StreamConnectionConfig(
        connect_cb=connect_cb,
        individual_address=address,
    )
