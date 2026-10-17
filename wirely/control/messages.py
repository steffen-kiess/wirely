"""Message headers, operation codes, and errors of the control protocol."""

import enum
import io
from dataclasses import dataclass
from typing import Any

import cbor2

PROTOCOL_VERSION = 0
MIN_MESSAGE_SIZE = 64
HEADER_SIZE = 3


class Kind(enum.IntEnum):
    REQUEST = 0
    RESPONSE = 1
    ERROR = 2


class Op(enum.IntEnum):
    HELLO = 0x00
    STATUS = 0x01
    PAUSE = 0x02
    RESUME = 0x03
    STEP = 0x04
    SNAPSHOT = 0x10
    READ_SNAPSHOT = 0x11
    RELEASE_SNAPSHOT = 0x12
    UPLOAD_BEGIN = 0x20
    UPLOAD_CHUNK = 0x21
    UPLOAD_FINISH = 0x22
    ACTIVATE = 0x23
    READ_PROGRAM = 0x24


class ErrorCode(enum.IntEnum):
    MALFORMED = 1
    UNSUPPORTED_VERSION = 2
    UNKNOWN_OP = 3
    NO_SESSION = 4
    BAD_SEQUENCE = 5
    UNAUTHENTICATED = 6
    INVALID_ARGUMENT = 7
    WRONG_STATE = 8
    NOT_FOUND = 9
    TOO_LARGE = 10
    INTEGRITY_FAILURE = 11
    PROGRAM_REJECTED = 12
    TRANSITION_FAILED = 13
    INTERNAL = 14


class RunState(enum.IntEnum):
    RUNNING = 0
    PAUSED = 1
    NO_PROGRAM = 2
    FAILED = 3


class Bank(enum.IntEnum):
    STATE = 0
    TEMPORARIES = 1


class ControlError(Exception):
    """An error response, raised by handlers and returned to clients."""

    def __init__(self, code: int, text: str | None = None) -> None:
        self.code = code
        self.text = text
        try:
            name = ErrorCode(code).name
        except ValueError:
            name = f'Error {code}'
        super().__init__(f'{name}: {text}' if text else name)

    def body(self) -> dict[int, Any]:
        body: dict[int, Any] = {1: int(self.code)}
        if self.text is not None:
            body[2] = self.text
        return body


@dataclass(frozen=True)
class Message:
    kind: int
    seq: int
    op: int
    body: Any  # Decoded CBOR item; an empty body decodes to an empty map.


def encode(kind: int, seq: int, op: int, body: Any) -> bytes:
    """Encode a message; an empty map is sent as an empty body."""
    payload = b'' if body == {} else cbor2.dumps(body)
    return bytes([kind, seq, op]) + payload


def decode(data: bytes) -> Message:
    """Decode a message, raising ControlError(MALFORMED) on invalid data."""
    if len(data) < HEADER_SIZE:
        raise ControlError(ErrorCode.MALFORMED, 'Message shorter than header')
    kind, seq, op = data[:HEADER_SIZE]
    payload = io.BytesIO(data[HEADER_SIZE:])
    if not payload.getbuffer().nbytes:
        return Message(kind, seq, op, {})
    try:
        body = cbor2.CBORDecoder(payload).decode()
    except cbor2.CBORDecodeError as error:
        raise ControlError(ErrorCode.MALFORMED, f'Invalid CBOR body: {error}') from None
    if payload.tell() != payload.getbuffer().nbytes:
        raise ControlError(ErrorCode.MALFORMED, 'Data after CBOR body')
    _check_item(body)
    return Message(kind, seq, op, body)


def _check_item(item: object) -> None:
    """Reject CBOR items outside the subset used by message bodies."""
    if isinstance(item, (bool, bytes, str)) or isinstance(item, int) and item >= 0:
        return
    if isinstance(item, list):
        for element in item:
            _check_item(element)
        return
    if isinstance(item, dict):
        for key, value in item.items():
            _check_item(key)
            _check_item(value)
        return
    raise ControlError(ErrorCode.MALFORMED, f'Unsupported CBOR item {type(item).__name__}')


def get_field(body: Any, key: int, kind: type, *, default: Any = None, required: bool = False) -> Any:
    """Return a body field after checking its Python type."""
    if not isinstance(body, dict):
        raise ControlError(ErrorCode.MALFORMED, 'Body is not a map')
    if key not in body:
        if required:
            raise ControlError(ErrorCode.INVALID_ARGUMENT, f'Missing field {key}')
        return default
    value = body[key]
    # bool is a subclass of int, but CBOR keeps them apart.
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ControlError(ErrorCode.INVALID_ARGUMENT, f'Field {key} has the wrong type')
    if kind is int and isinstance(value, int) and value < 0:
        raise ControlError(ErrorCode.INVALID_ARGUMENT, f'Field {key} is negative')
    return value
