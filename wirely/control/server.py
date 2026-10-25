"""Control protocol server on Unix domain sockets."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import ssl
import stat
from collections.abc import AsyncIterator, Coroutine
from typing import TYPE_CHECKING, Any

from .controller import RuntimeController

if TYPE_CHECKING:
    from ..runtime.engine import Engine
from .framing import (
    SERIAL_WINDOW, STREAM_PREFACE, STREAM_WINDOW, TLS_HANDSHAKE_BYTE, FramingError,
    SerialFraming, StreamFraming,
)
from .messages import (
    MIN_MESSAGE_SIZE, PROTOCOL_VERSION, ControlError, ErrorCode, Kind, Message, Op, decode, encode,
    get_field,
)

logger = logging.getLogger(__name__)

STREAM_SOCKET_NAME = 'control'
SERIAL_SOCKET_NAME = 'control-serial'


class Session:
    """Protocol state of one connection, which serves requests until it closes."""

    def __init__(self, framing: StreamFraming | SerialFraming, controller: RuntimeController) -> None:
        self.framing = framing
        self.controller = controller
        self.window = STREAM_WINDOW if framing.reliable else SERIAL_WINDOW
        self.active = False
        self.client_nonce: bytes | None = None
        self.peer_max_message_size = MIN_MESSAGE_SIZE
        self.counter = 0
        self.hello_response: bytes | None = None
        # The controller activation this session is bound to, or None when unbound.
        self.bound_activation: int | None = None
        # On unreliable transports, the last response for retransmitted requests.
        self.last_response: bytes | None = None

    async def serve(self) -> None:
        while (data := await self.framing.read()) is not None:
            response = self.handle(data)
            if response is not None:
                await self.framing.write(response)

    def handle(self, data: bytes) -> bytes | None:
        """Return the encoded response to a message, or None to send nothing."""
        try:
            message = decode(data)
        except ControlError as error:
            if len(data) < 3:
                return None
            return self._error(data[1], data[2], error)
        if message.kind != Kind.REQUEST:
            return None
        if message.op == Op.HELLO:
            return self._hello(message)
        if not self.active:
            return self._error(message.seq, message.op, ControlError(ErrorCode.NO_SESSION))
        if message.seq == (self.counter + 1) & 0xff:
            self.counter += 1
            response = self._run(message)
            if not self.framing.reliable:
                self.last_response = response
            return response
        if message.seq == self.counter & 0xff and self.last_response is not None:
            return self.last_response
        return self._error(message.seq, message.op, ControlError(ErrorCode.BAD_SEQUENCE))

    def _hello(self, message: Message) -> bytes:
        body = message.body
        if (self.hello_response is not None and isinstance(body, dict)
                and body.get(2) == self.client_nonce):
            return self.hello_response
        self.active = False
        try:
            if message.seq != 0:
                raise ControlError(ErrorCode.BAD_SEQUENCE, 'Hello must use sequence number 0')
            max_message_size = get_field(body, 1, int, required=True)
            client_nonce = get_field(body, 2, bytes, required=True)
            versions = get_field(body, 3, list, required=True)
            if max_message_size < MIN_MESSAGE_SIZE or len(client_nonce) != 8:
                raise ControlError(ErrorCode.INVALID_ARGUMENT)
            if PROTOCOL_VERSION not in versions:
                raise ControlError(ErrorCode.UNSUPPORTED_VERSION)
        except ControlError as error:
            return self._error(message.seq, message.op, error)
        self.active = True
        self.client_nonce = client_nonce
        self.peer_max_message_size = max_message_size
        self.counter = 0
        self.last_response = None
        self.bound_activation = None
        self.hello_response = encode(Kind.RESPONSE, 0, Op.HELLO, {
            1: self.framing.max_message_size,
            2: os.urandom(8),
            3: PROTOCOL_VERSION,
            4: False,
            5: self.window,
            8: [int(op) for op in self.controller.supported_ops],
        })
        return self.hello_response

    def _run(self, message: Message) -> bytes:
        try:
            if message.op == Op.BIND_PROGRAM:
                # A failed attempt leaves the session unbound.
                self.bound_activation = None
                body, self.bound_activation = self.controller.bind(message.body)
            else:
                bound = self.bound_activation == self.controller.activation
                body = self.controller.handle(message.op, message.body, bound=bound)
        except ControlError as error:
            return self._error(message.seq, message.op, error)
        except Exception as error:
            logger.exception('Control request %#04x failed', message.op)
            return self._error(message.seq, message.op, ControlError(ErrorCode.INTERNAL, str(error)))
        response = encode(Kind.RESPONSE, message.seq, message.op, body)
        if len(response) > self.peer_max_message_size:
            return self._error(message.seq, message.op, ControlError(ErrorCode.TOO_LARGE))
        return response

    def _error(self, seq: int, op: int, error: ControlError) -> bytes:
        response = encode(Kind.ERROR, seq, op, error.body())
        if len(response) > self.peer_max_message_size:
            response = encode(Kind.ERROR, seq, op, {1: int(error.code)})
        return response


class ControlServer:
    """Serves the control protocol for an engine on sockets in a state directory.

    The `control` socket uses stream framing, optionally inside TLS. The
    `control-serial` socket uses serial framing. The caller holds the state
    directory lock, see `wirely.misc.state_directory`.
    """

    def __init__(self, engine: Engine, state_directory: str, tls_context: ssl.SSLContext | None = None, *,
                 build_id: bytes) -> None:
        self.controller = RuntimeController(engine, build_id)
        self.state_directory = state_directory
        self.tls_context = tls_context
        self._sockets: list[tuple[socket.socket, str]] = []  # Listening sockets and their paths.
        self._tasks: set[asyncio.Task] = set()

    @property
    def stream_path(self) -> str:
        return os.path.join(self.state_directory, STREAM_SOCKET_NAME)

    @property
    def serial_path(self) -> str:
        return os.path.join(self.state_directory, SERIAL_SOCKET_NAME)

    async def __aenter__(self) -> ControlServer:
        try:
            stream_socket = self._listen(self.stream_path)
            serial_socket = self._listen(self.serial_path)
        except BaseException:
            self._close_sockets()
            raise
        self._start(self._accept_stream(stream_socket))
        self._start(self._accept_serial(serial_socket))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._close_sockets()

    def _listen(self, path: str) -> socket.socket:
        with contextlib.suppress(FileNotFoundError):
            if not stat.S_ISSOCK(os.lstat(path).st_mode):
                raise FileExistsError(f'{path} exists and is not a socket')
            os.unlink(path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sockets.append((sock, path))
        sock.bind(path)
        os.chmod(path, 0o600)
        sock.listen()
        sock.setblocking(False)
        return sock

    def _close_sockets(self) -> None:
        for sock, path in self._sockets:
            sock.close()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
        self._sockets.clear()

    def _start(self, coroutine: Coroutine[Any, Any, object]) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _accept_stream(self, listener: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        while True:
            conn, _ = await loop.sock_accept(listener)
            self._start(self._serve_stream(conn))

    async def _accept_serial(self, listener: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        while True:
            conn, _ = await loop.sock_accept(listener)
            self._start(self._serve_serial(conn))

    async def _serve_stream(self, conn: socket.socket) -> None:
        """Serve a TLS or plain connection, distinguished by its first byte."""
        try:
            first = await _peek_byte(conn)
        except OSError:
            first = None
        use_tls = first == TLS_HANDSHAKE_BYTE
        if first is None or use_tls and self.tls_context is None:
            conn.close()
            return
        streams = await _open_accepted(conn, self.tls_context if use_tls else None)
        if streams is None:
            return
        reader, writer = streams
        async with _closing(writer):
            try:
                preface = await reader.readexactly(len(STREAM_PREFACE))
            except (asyncio.IncompleteReadError, OSError):
                return
            if preface == STREAM_PREFACE:
                await self._serve(StreamFraming(reader, writer))

    async def _serve_serial(self, conn: socket.socket) -> None:
        streams = await _open_accepted(conn, None)
        if streams is None:
            return
        reader, writer = streams
        async with _closing(writer):
            await self._serve(SerialFraming(reader, writer))

    async def _serve(self, framing: StreamFraming | SerialFraming) -> None:
        try:
            await Session(framing, self.controller).serve()
        except (FramingError, OSError) as error:
            logger.info('Control connection closed: %s', error)


async def _peek_byte(conn: socket.socket) -> int | None:
    """Wait for the first byte of a connection without consuming it."""
    loop = asyncio.get_running_loop()
    conn.setblocking(False)
    readable = loop.create_future()

    def set_readable() -> None:
        if not readable.done():
            readable.set_result(None)

    loop.add_reader(conn.fileno(), set_readable)
    try:
        await readable
    finally:
        loop.remove_reader(conn.fileno())
    data = conn.recv(1, socket.MSG_PEEK)
    return data[0] if data else None


async def _open_accepted(conn: socket.socket, ssl_context: ssl.SSLContext | None,
                         ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Return streams for an accepted socket, or None if the TLS handshake fails."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    try:
        transport, _ = await loop.connect_accepted_socket(lambda: protocol, conn, ssl=ssl_context)
    except OSError as error:
        logger.info('Control connection failed: %r', error)
        conn.close()
        return None
    return reader, asyncio.StreamWriter(transport, protocol, reader, loop)


@contextlib.asynccontextmanager
async def _closing(writer: asyncio.StreamWriter) -> AsyncIterator[None]:
    try:
        yield
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
