"""Connect xknx TCP tunnels through Wirely-provided stream connections."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import xknx
from xknx.io import ConnectionConfig, ConnectionType, KNXIPInterface, TCPTunnel
from xknx.io.transport import TCPTransport

# Opens the stream of a tunnel: (loop, protocol factory) -> (transport, protocol).
ConnectCallback = Callable[[asyncio.AbstractEventLoop, Callable[[], asyncio.Protocol]], Awaitable[tuple[Any, Any]]]


class StreamConnectionConfig(ConnectionConfig):
    """TCP tunnel configuration with a callback for opening its stream."""

    def __init__(self, connect_cb: ConnectCallback, individual_address: Any = None) -> None:
        super().__init__(
            connection_type=ConnectionType.TUNNELING_TCP,
            individual_address=individual_address,
        )
        self.connect_cb = connect_cb


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


class StreamKNXIPInterface(KNXIPInterface):
    """Start a TCP tunnel using a StreamConnectionConfig."""

    async def start(self) -> None:
        config = self.connection_config
        assert isinstance(config, StreamConnectionConfig)
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


def create_xknx(connection_config: ConnectionConfig) -> xknx.XKNX:
    """Create xknx with the stream interface when the configuration needs one."""
    knx = xknx.XKNX(connection_config=connection_config)
    if isinstance(connection_config, StreamConnectionConfig):
        knx.knxip_interface = StreamKNXIPInterface(knx, connection_config)
    return knx
