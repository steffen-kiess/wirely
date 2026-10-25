"""Stream and serial framing of control protocol messages."""

import asyncio
import contextlib
import zlib

STREAM_PREFACE = b'WRLC'
TLS_HANDSHAKE_BYTE = 0x16

# Message limits and windows used for each framing.
STREAM_MAX_MESSAGE_SIZE = 0xffff
STREAM_WINDOW = 16
SERIAL_MAX_MESSAGE_SIZE = 256
SERIAL_WINDOW = 1
# Shortest time between notifications, and on serial the time before an unacknowledged one is resent.
STREAM_NOTIFICATION_INTERVAL = 0.1
SERIAL_NOTIFICATION_INTERVAL = 0.25
SERIAL_NOTIFICATION_TIMEOUT = 1.0


class FramingError(Exception):
    pass


class StreamFraming:
    """Frames with a little-endian uint16 length prefix, for reliable transports."""

    reliable = True

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 max_message_size: int = STREAM_MAX_MESSAGE_SIZE) -> None:
        self.reader = reader
        self.writer = writer
        self.max_message_size = max_message_size

    async def read(self) -> bytes | None:
        """Return the next message, or None at the end of the stream."""
        try:
            length = int.from_bytes(await self.reader.readexactly(2), 'little')
            if length > self.max_message_size:
                raise FramingError(f'Message of {length} bytes exceeds limit of {self.max_message_size}')
            return await self.reader.readexactly(length)
        except asyncio.IncompleteReadError as error:
            if error.partial:
                raise FramingError('Connection closed within a frame') from None
            return None

    async def write(self, message: bytes) -> None:
        if len(message) > 0xffff:
            raise FramingError(f'Message of {len(message)} bytes is too long for a frame')
        self.writer.write(len(message).to_bytes(2, 'little') + message)
        await self.writer.drain()

    async def close(self) -> None:
        await _close_writer(self.writer)


class SerialFraming:
    """COBS-encoded frames with a CRC-32, for transports that can lose or corrupt data."""

    reliable = False

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 max_message_size: int = SERIAL_MAX_MESSAGE_SIZE) -> None:
        self.reader = reader
        self.writer = writer
        self.max_message_size = max_message_size

    async def read(self) -> bytes | None:
        """Return the next valid message, or None at the end of the stream.

        Frames that are too long or fail decoding are discarded.
        """
        discarding = False
        while True:
            try:
                frame = await self.reader.readuntil(b'\0')
            except asyncio.IncompleteReadError:
                return None
            except asyncio.LimitOverrunError as error:
                # Drop the start of an overlong frame and its remainder up to the next delimiter.
                await self.reader.readexactly(error.consumed)
                discarding = True
                continue
            if discarding:
                discarding = False
                continue
            try:
                data = cobs_decode(frame[:-1])
            except ValueError:
                continue
            if len(data) < 4 or len(data) - 4 > self.max_message_size:
                continue
            message, crc = data[:-4], data[-4:]
            if zlib.crc32(message).to_bytes(4, 'little') == crc:
                return message

    async def write(self, message: bytes) -> None:
        data = message + zlib.crc32(message).to_bytes(4, 'little')
        self.writer.write(cobs_encode(data) + b'\0')
        await self.writer.drain()

    async def close(self) -> None:
        await _close_writer(self.writer)


class MemoryFraming:
    """One end of an in-process connection, created by memory_framing_pair()."""

    reliable = True

    def __init__(self, max_message_size: int = STREAM_MAX_MESSAGE_SIZE) -> None:
        self.max_message_size = max_message_size
        self._incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._peer: MemoryFraming = self

    async def read(self) -> bytes | None:
        """Return the next message, or None once either end has closed."""
        message = await self._incoming.get()
        if message is None:
            self._incoming.put_nowait(None)  # Later reads also see the end.
        return message

    async def write(self, message: bytes) -> None:
        if len(message) > self._peer.max_message_size:
            raise FramingError(f'Message of {len(message)} bytes exceeds limit of {self._peer.max_message_size}')
        self._peer._incoming.put_nowait(message)

    async def close(self) -> None:
        self._incoming.put_nowait(None)
        self._peer._incoming.put_nowait(None)


def memory_framing_pair() -> tuple[MemoryFraming, MemoryFraming]:
    """Return the two connected ends of an in-process connection."""
    first, second = MemoryFraming(), MemoryFraming()
    first._peer, second._peer = second, first
    return first, second


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()


def cobs_encode(data: bytes) -> bytes:
    """Encode data with Consistent Overhead Byte Stuffing, without the delimiter."""
    out = bytearray()
    block = bytearray()
    for byte in data:
        if byte == 0:
            out.append(len(block) + 1)
            out += block
            block.clear()
            continue
        block.append(byte)
        if len(block) == 254:
            out.append(0xff)
            out += block
            block.clear()
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Decode COBS data without the delimiter, raising ValueError if it is invalid."""
    out = bytearray()
    position = 0
    while position < len(data):
        code = data[position]
        end = position + code
        if code == 0 or end > len(data) or 0 in data[position + 1:end]:
            raise ValueError('Invalid COBS data')
        out += data[position + 1:end]
        position = end
        if code != 0xff and position < len(data):
            out.append(0)
    return bytes(out)
