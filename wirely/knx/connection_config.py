"""Build KNX connection configurations from named node parameters."""

import asyncio
import signal
import socket
from collections.abc import Callable
from typing import Any

import xknx

from wirely.knx.connection_adapter import StreamConnectionConfig
from wirely.knx.settings import ConnectionSettings, check_connection


# How long ssh may take to exit after SIGTERM before it is killed, in seconds.
SSH_EXIT_TIMEOUT = 5.0


class _SSHConnector:
    """Open a Unix socket on a remote host through ssh and socat; close() ends the ssh process."""

    def __init__(self, target: str, path: str) -> None:
        self.target = target
        self.path = path
        self.process: asyncio.subprocess.Process | None = None

    async def __call__(self, loop: asyncio.AbstractEventLoop,
                       protocol_factory: Callable[[], asyncio.Protocol]) -> tuple[Any, Any]:
        await self.close()
        local_socket, remote_socket = socket.socketpair()

        # Ignore SIGINT in ssh so the service can shut down cleanly.
        command = [
            'ssh', '--', self.target,
            'printf "X" && socat STDIO UNIX-CONNECT:\''
            + self.path.replace("'", "'\\''") + '\'',
        ]
        try:
            self.process = await asyncio.create_subprocess_exec(
                *command, stdin=remote_socket.fileno(), stdout=remote_socket.fileno(),
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

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        self.process = None
        # Without a pty, the remote socat sees the end of its input and exits.
        if process.returncode is None:
            process.terminate()
        try:
            async with asyncio.timeout(SSH_EXIT_TIMEOUT):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()


def create_connection_config(*, host: str | None = None, protocol: str | None = None, port: int | None = None,
                             unix_path: str | None = None, ssh_target: str | None = None,
                             individual_address: str | None = None) -> xknx.io.ConnectionConfig:
    """Select one connection mode from the supplied KNX node parameters."""
    return connection_config_from_settings(check_connection(
        host=host, protocol=protocol, port=port, unix_path=unix_path, ssh_target=ssh_target,
        individual_address=individual_address))


def connection_config_from_settings(settings: ConnectionSettings) -> xknx.io.ConnectionConfig:
    """Build the xknx configuration; the connection reconnects itself, so xknx does not."""
    address = (None if settings.individual_address is None
               else xknx.telegram.address.IndividualAddress(settings.individual_address.value))

    if settings.protocol == 'Auto':
        # Routing is not used: without a connection, a router that disappears is never noticed.
        return xknx.io.ConnectionConfig(
            connection_type=xknx.io.ConnectionType.AUTOMATIC,
            auto_reconnect=False,
            scan_filter=xknx.io.GatewayScanFilter(routing=False, secure_routing=False, secure_tunnelling=False),
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
            auto_reconnect=False,
        )

    path = settings.unix_path
    assert path is not None
    if settings.ssh_target is not None:
        connector = _SSHConnector(settings.ssh_target, path)
        return StreamConnectionConfig(
            connect_cb=connector,
            individual_address=address,
            close_cb=connector.close,
            auto_reconnect=False,
        )
    return StreamConnectionConfig(
        connect_cb=lambda loop, factory: loop.create_unix_connection(
            factory, path=path
        ),
        individual_address=address,
        auto_reconnect=False,
    )
