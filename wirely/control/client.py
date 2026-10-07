"""Control protocol client."""

import asyncio
import os

from .framing import STREAM_PREFACE, SerialFraming, StreamFraming
from .messages import (
    MIN_MESSAGE_SIZE, PROTOCOL_VERSION, ControlError, ErrorCode, Kind, Op, decode, encode,
    get_field,
)


class ControlClient:
    """A session with a runtime; requests are sent in order and may overlap up to the window."""

    def __init__(self, framing, *, timeout=1.0, retries=5):
        self.framing = framing
        # On unreliable transports, the retransmission timeout in seconds and the number of retransmissions.
        self.timeout = timeout
        self.retries = retries
        self.counter = 0
        self.peer_max_message_size = MIN_MESSAGE_SIZE
        self.window = 1
        self.supported_ops = None

    @classmethod
    async def connect_unix(cls, path, protocol='stream', *, ssl=None, server_hostname=None, **kwargs):
        """Connect to a Unix socket and start a session.

        `protocol` is 'stream' or 'serial'. `ssl` is only valid with 'stream'.
        """
        if protocol == 'stream':
            reader, writer = await asyncio.open_unix_connection(
                path, ssl=ssl, server_hostname=(server_hostname or '') if ssl is not None else None,
            )
            writer.write(STREAM_PREFACE)
            framing = StreamFraming(reader, writer)
        elif protocol == 'serial':
            if ssl is not None:
                raise ValueError('TLS requires the stream protocol')
            reader, writer = await asyncio.open_unix_connection(path)
            framing = SerialFraming(reader, writer)
        else:
            raise ValueError(f'Unknown protocol {protocol!r}')
        client = cls(framing, **kwargs)
        try:
            await client.hello()
        except BaseException:
            await client.close()
            raise
        return client

    async def close(self):
        writer = self.framing.writer
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def hello(self):
        nonce = os.urandom(8)
        self.counter = 0
        self.peer_max_message_size = MIN_MESSAGE_SIZE
        body = await self._exchange(0, Op.HELLO, {
            1: self.framing.max_message_size, 2: nonce, 3: [PROTOCOL_VERSION],
        })
        self.peer_max_message_size = get_field(body, 1, int, required=True)
        self.window = get_field(body, 5, int, default=1) or 1
        self.supported_ops = get_field(body, 8, list)
        if get_field(body, 3, int, required=True) != PROTOCOL_VERSION:
            raise ControlError(ErrorCode.UNSUPPORTED_VERSION)
        if get_field(body, 4, bool, default=False):
            raise ControlError(ErrorCode.UNAUTHENTICATED, 'Authentication is not supported by this client')
        return body

    async def request(self, op, body=None):
        """Send one request and return its response body; error responses raise ControlError."""
        self.counter += 1
        return await self._exchange(self.counter & 0xff, op, body or {})

    async def requests(self, items):
        """Send (op, body) requests, overlapping up to the window, and return their response bodies."""
        items = list(items)
        if self.window <= 1 or not self.framing.reliable:
            return [await self.request(op, body) for op, body in items]
        results = []
        pending = []  # Sequence numbers and operations awaiting responses, in order.
        for op, body in items:
            if len(pending) >= self.window:
                results.append(await self._receive(*pending.pop(0)))
            self.counter += 1
            seq = self.counter & 0xff
            await self._send(seq, op, body or {})
            pending.append((seq, op))
        for seq, op in pending:
            results.append(await self._receive(seq, op))
        return results

    async def _send(self, seq, op, body):
        message = encode(Kind.REQUEST, seq, op, body)
        if len(message) > self.peer_max_message_size:
            raise ValueError(f'Request of {len(message)} bytes exceeds the runtime limit')
        await self.framing.write(message)
        return message

    async def _receive(self, seq, op, timeout=None):
        """Return the body of the response to a request, ignoring unrelated messages."""
        async with asyncio.timeout(timeout):
            while True:
                data = await self.framing.read()
                if data is None:
                    raise ConnectionError('Connection closed by runtime')
                try:
                    message = decode(data)
                except ControlError:
                    continue
                if message.seq != seq or message.op != op or message.kind == Kind.REQUEST:
                    continue
                if message.kind == Kind.ERROR:
                    raise ControlError(get_field(message.body, 1, int, required=True),
                                       get_field(message.body, 2, str))
                return message.body

    async def _exchange(self, seq, op, body):
        message = await self._send(seq, op, body)
        if self.framing.reliable:
            return await self._receive(seq, op)
        for attempt in range(self.retries + 1):
            if attempt:
                await self.framing.write(message)
            try:
                return await self._receive(seq, op, self.timeout)
            except TimeoutError:
                pass
        raise TimeoutError(f'No response to request {op:#04x} after {self.retries} retransmissions')
