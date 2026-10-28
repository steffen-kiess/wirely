"""Connect xknx tunnels, also through Wirely-provided stream connections, and retry connecting."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import xknx
from xknx.exceptions import XKNXException
from xknx.io import ConnectionConfig, ConnectionType, KNXIPInterface, TCPTunnel
from xknx.io.transport import TCPTransport
from xknx.io.tunnel import _Tunnel

# Opens the stream of a tunnel: (loop, protocol factory) -> (transport, protocol).
ConnectCallback = Callable[[asyncio.AbstractEventLoop, Callable[[], asyncio.Protocol]], Awaitable[tuple[Any, Any]]]
# Releases what the last connect opened, such as a process.
CloseCallback = Callable[[], Awaitable[None]]


class StreamConnectionConfig(ConnectionConfig):
    """TCP tunnel configuration with a callback for opening its stream."""

    def __init__(self, connect_cb: ConnectCallback, individual_address: Any = None,
                 close_cb: CloseCallback | None = None, auto_reconnect: bool = True) -> None:
        super().__init__(
            connection_type=ConnectionType.TUNNELING_TCP,
            individual_address=individual_address,
            auto_reconnect=auto_reconnect,
        )
        self.connect_cb = connect_cb
        self.close_cb = close_cb


class StreamTCPTransport(TCPTransport):
    """Use xknx's TCP framing over a caller-provided stream."""

    __slots__ = ("_stream_connect_cb",)

    def __init__(self, connect_cb: ConnectCallback, connection_lost_cb: Callable[[], None] | None) -> None:
        super().__init__(
            remote_addr=("0.0.0.0", 0),
            connection_lost_cb=connection_lost_cb,
        )
        self._stream_connect_cb = connect_cb

    async def connect(self) -> None:
        protocol = self.TCPTransportFactory(
            data_received_callback=self.data_received_callback,
            connection_lost_callback=self._connection_lost,
        )
        self.transport, _ = await self._stream_connect_cb(
            asyncio.get_running_loop(), lambda: protocol
        )


class StreamTCPTunnel(TCPTunnel):
    """Create the stream transport while retaining xknx's tunnel logic."""

    __slots__ = ("_stream_connect_cb",)

    def __init__(self, xknx_instance: xknx.XKNX, connect_cb: ConnectCallback, individual_address: Any,
                 cemi_received_callback: Callable[..., Any], auto_reconnect: bool,
                 auto_reconnect_wait: int) -> None:
        self._stream_connect_cb = connect_cb
        super().__init__(
            xknx_instance,
            cemi_received_callback=cemi_received_callback,
            gateway_ip="0.0.0.0",
            gateway_port=0,
            individual_address=individual_address,
            auto_reconnect=auto_reconnect,
            auto_reconnect_wait=auto_reconnect_wait,
        )

    def _init_transport(self) -> None:
        self.transport = StreamTCPTransport(
            connect_cb=self._stream_connect_cb,
            connection_lost_cb=self._tunnel_lost,
        )


class WirelyKNXIPInterface(KNXIPInterface):
    """Connect a StreamConnectionConfig through its stream, and optionally connect only when asked to.

    With connect_later, xknx.start() does not connect; call connect() and stop() instead, also repeatedly.
    xknx.start() itself cannot be called again after a failure.
    """

    def __init__(self, xknx_instance: xknx.XKNX, connection_config: ConnectionConfig, *, connect_later: bool) -> None:
        super().__init__(xknx_instance, connection_config)
        self.connect_later = connect_later

    async def start(self) -> None:
        if not self.connect_later:
            await self.connect()

    async def connect(self) -> None:
        """Connect; raise XKNXException or OSError on failure, after which stop() must be called."""
        config = self.connection_config
        if not isinstance(config, StreamConnectionConfig):
            await super().start()
            return
        self.xknx.cemi_handler.data_secure_init(keyring=None)
        self._interface = StreamTCPTunnel(
            self.xknx,
            connect_cb=config.connect_cb,
            individual_address=config.individual_address,
            cemi_received_callback=self.cemi_received,
            auto_reconnect=config.auto_reconnect,
            auto_reconnect_wait=config.auto_reconnect_wait,
        )
        await self._interface.connect()

    async def stop(self) -> None:
        """Disconnect and release the connection, also after a failed, lost, or cancelled connect."""
        interface = self._interface
        if isinstance(interface, _Tunnel) and interface.transport.transport is None:
            # The tunnel was lost and xknx stopped its transport, so a disconnect request could only time out.
            interface.communication_channel = None
        try:
            await super().stop()
        except (XKNXException, OSError):
            # After a lost tunnel, the disconnect request goes to a stopped transport.
            pass
        finally:
            self._interface = None
            config = self.connection_config
            if isinstance(config, StreamConnectionConfig) and config.close_cb is not None:
                await config.close_cb()


def create_xknx(connection_config: ConnectionConfig, *, state_updater: bool = True,
                connect_later: bool = False) -> xknx.XKNX:
    """Create xknx with an interface that supports stream connections and connecting later."""
    knx = xknx.XKNX(connection_config=connection_config, state_updater=state_updater)
    knx.knxip_interface = WirelyKNXIPInterface(knx, connection_config, connect_later=connect_later)
    return knx
