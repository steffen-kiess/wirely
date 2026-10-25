"""Control protocol server on Unix domain sockets and in-process connections."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import ssl
import stat
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
from typing import Any

from .controller import RuntimeController
from .framing import (
    SERIAL_NOTIFICATION_INTERVAL, SERIAL_NOTIFICATION_TIMEOUT, SERIAL_WINDOW, STREAM_NOTIFICATION_INTERVAL,
    STREAM_PREFACE, STREAM_WINDOW, TLS_HANDSHAKE_BYTE, FramingError, MemoryFraming, SerialFraming, StreamFraming,
)
from .messages import (
    MIN_MESSAGE_SIZE, PROTOCOL_VERSION, STATUS_DEFAULT_MASK, ControlError, ErrorCode, Kind, Message, Op,
    decode, encode, get_field,
)

logger = logging.getLogger(__name__)

STREAM_SOCKET_NAME = 'control'
SERIAL_SOCKET_NAME = 'control-serial'


@dataclass
class StatusSubscription:
    """A session's subscription to Status changes."""

    mask: int
    acknowledged: dict[int, Any]  # Values the client has: the response and acknowledged notifications.
    in_flight: tuple[int, dict[int, Any]] | None = None  # Seq and values of the unacknowledged notification.
    started: bool = False  # Whether the subscribing response was sent; notifications follow it.


class Session:
    """Protocol state of one connection, which serves requests until it closes."""

    def __init__(self, framing: StreamFraming | SerialFraming | MemoryFraming, controller: RuntimeController) -> None:
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
        self.subscription: StatusSubscription | None = None
        self.notification_counter = 0
        self.status_changed = asyncio.Event()

    async def serve(self) -> None:
        notifier = asyncio.create_task(self._notify())
        try:
            while (data := await self.framing.read()) is not None:
                response = self.handle(data)
                if response is not None:
                    await self.framing.write(response)
                if self.subscription is not None and not self.subscription.started:
                    self.subscription.started = True
                    self.status_changed.set()
        finally:
            self._subscribe(None)
            notifier.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await notifier

    def handle(self, data: bytes) -> bytes | None:
        """Return the encoded response to a message, or None to send nothing."""
        try:
            message = decode(data)
        except ControlError as error:
            if len(data) < 3:
                return None
            return self._error(data[1], data[2], error)
        if message.kind == Kind.ACK and self.active:
            self._acknowledge(message)
            return None
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
        self._subscribe(None)
        self.notification_counter = 0
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
                if message.op == Op.STATUS and 2 in message.body:
                    mask = get_field(message.body, 1, int, default=STATUS_DEFAULT_MASK)
                    self._subscribe(StatusSubscription(mask, dict(body)) if message.body[2] else None)
        except ControlError as error:
            return self._error(message.seq, message.op, error)
        except Exception as error:
            logger.exception('Control request %#04x failed', message.op)
            return self._error(message.seq, message.op, ControlError(ErrorCode.INTERNAL, str(error)))
        response = encode(Kind.RESPONSE, message.seq, message.op, body)
        if len(response) > self.peer_max_message_size:
            return self._error(message.seq, message.op, ControlError(ErrorCode.TOO_LARGE))
        return response

    def _subscribe(self, subscription: StatusSubscription | None) -> None:
        """Start, replace, or with None cancel the Status subscription."""
        self.subscription = subscription
        if subscription is None:
            self.controller.engine.status_listeners.discard(self.status_changed.set)
        else:
            self.controller.engine.status_listeners.add(self.status_changed.set)

    def _acknowledge(self, message: Message) -> None:
        subscription = self.subscription
        if (subscription is None or subscription.in_flight is None or message.op != Op.STATUS
                or message.seq != subscription.in_flight[0]):
            return
        subscription.acknowledged = subscription.in_flight[1]
        subscription.in_flight = None
        if isinstance(message.body, dict) and message.body.get(1) is True:
            self._subscribe(None)
        else:
            self.status_changed.set()

    async def _notify(self) -> None:
        """Send Status notifications, one at a time, throttled, resent on unreliable transports."""
        loop = asyncio.get_running_loop()
        reliable = self.framing.reliable
        interval = STREAM_NOTIFICATION_INTERVAL if reliable else SERIAL_NOTIFICATION_INTERVAL
        resend_at = 0.0
        while True:
            subscription = self.subscription
            timeout = None
            if subscription is not None and subscription.in_flight is not None and not reliable:
                timeout = max(0.0, resend_at - loop.time())
            try:
                async with asyncio.timeout(timeout):
                    await self.status_changed.wait()
            except TimeoutError:
                pass
            self.status_changed.clear()
            subscription = self.subscription
            if subscription is None or not subscription.started:
                continue
            if subscription.in_flight is not None and (reliable or loop.time() < resend_at):
                continue  # Wait for the acknowledgement.
            values = self.controller.status(subscription.mask)
            # The client may or may not have applied an unacknowledged notification, so changes
            # relative to either state are sent.
            states = [subscription.acknowledged]
            if subscription.in_flight is not None:
                states.append(subscription.in_flight[1])
            delta: dict[int, Any] = {}
            for state in states:
                delta.update((key, value) for key, value in values.items() if state.get(key) != value)
                delta.update((key, None) for key in state if key not in values)
            if not delta:
                subscription.in_flight = None
                continue
            seq = self.notification_counter & 0xff
            self.notification_counter += 1
            subscription.in_flight = (seq, values)
            resend_at = loop.time() + SERIAL_NOTIFICATION_TIMEOUT
            try:
                await self.framing.write(encode(Kind.NOTIFICATION, seq, Op.STATUS, delta))
            except (OSError, FramingError):
                return  # The connection is closing; serve() ends the session.
            await asyncio.sleep(interval)

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

    def __init__(self, controller: RuntimeController, state_directory: str,
                 tls_context: ssl.SSLContext | None = None) -> None:
        self.controller = controller
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
        await serve_session(self.controller, framing)


async def serve_session(controller: RuntimeController, framing: StreamFraming | SerialFraming | MemoryFraming,
                        ) -> None:
    """Serve one connection until it closes."""
    try:
        await Session(framing, controller).serve()
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
