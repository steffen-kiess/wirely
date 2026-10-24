"""Canonical instruction operand encodings shared by bytecode tools."""

from .generated.format import Opcode, OperandKind


def _uint32(value: int) -> None:
    if not 0 <= value <= 0xffffffff:
        raise ValueError(f'{value} does not fit uint32')


def encode_value_operand(kind: OperandKind, index: int) -> bytes:
    """Encode a value operand using the shortest index form."""
    _uint32(index)
    if kind not in tuple(OperandKind):
        raise ValueError(f'Unknown operand kind {kind}')
    if index <= 28:
        return bytes([(int(kind) << 5) | index])
    size, selector = (1, 29) if index <= 0xff else (2, 30) if index <= 0xffff else (4, 31)
    return bytes([(int(kind) << 5) | selector]) + index.to_bytes(size, 'little')


def encode_integer_parameter(value: int) -> bytes:
    """Encode an unsigned integer parameter using the shortest form."""
    _uint32(value)
    if value <= 253:
        return bytes([value])
    size, prefix = (2, 254) if value <= 0xffff else (4, 255)
    return bytes([prefix]) + value.to_bytes(size, 'little')


def zigzag(value: int) -> int:
    """Map signed integers to unsigned integers, interleaving signs."""
    return value * 2 if value >= 0 else -value * 2 - 1


def _read_integer(data: bytes | memoryview, offset: int, size: int) -> tuple[int, int]:
    if offset < 0 or offset + size > len(data):
        raise ValueError(f'Truncated operand at byte {offset}')
    return int.from_bytes(data[offset:offset + size], 'little'), offset + size


def decode_value_operand(data: bytes | memoryview, offset: int = 0) -> tuple[OperandKind, int, int]:
    """Return the operand kind, index, and next byte offset."""
    descriptor, offset = _read_integer(data, offset, 1)
    kind = OperandKind(descriptor >> 5)
    if kind not in tuple(OperandKind):
        raise ValueError(f'Unknown operand kind {kind}')
    selector = descriptor & 31
    if selector <= 28:
        return kind, selector, offset
    index, offset = _read_integer(data, offset, {29: 1, 30: 2, 31: 4}[selector])
    if (selector == 29 and index <= 28) or (selector == 30 and index <= 255) or (selector == 31 and index <= 65535):
        raise ValueError('Noncanonical value operand encoding')
    return kind, index, offset


def decode_integer_parameter(data: bytes | memoryview, offset: int = 0) -> tuple[int, int]:
    """Return an unsigned integer parameter and the next byte offset."""
    first, offset = _read_integer(data, offset, 1)
    if first <= 253:
        return first, offset
    value, offset = _read_integer(data, offset, 2 if first == 254 else 4)
    if (first == 254 and value <= 253) or (first == 255 and value <= 65535):
        raise ValueError('Noncanonical integer parameter encoding')
    return value, offset


def immediate_positions(opcode: Opcode, operand_count: int) -> tuple[int, ...]:
    """Return eligible positions; comparisons still require one typed source."""
    if opcode in (Opcode.COPY, Opcode.SET_FIELD, Opcode.MAKE_SOME, Opcode.NOT):
        return (1,)
    if opcode in (Opcode.AND, Opcode.OR, Opcode.XOR, Opcode.SHIFT_LEFT, Opcode.SHIFT_RIGHT,
                  Opcode.ADD_WRAPPING, Opcode.SUB_WRAPPING, Opcode.MUL_WRAPPING,
                  Opcode.ADD_SATURATING, Opcode.SUB_SATURATING, Opcode.MUL_SATURATING,
                  Opcode.ADD_CHECKED, Opcode.SUB_CHECKED, Opcode.MUL_CHECKED,
                  Opcode.DIV_CHECKED, Opcode.REM_CHECKED, Opcode.EQUAL, Opcode.LESS):
        return (1, 2)
    if opcode == Opcode.CALL:
        return tuple(range(3, operand_count))
    if opcode in (Opcode.JUMP_IF_TRUE, Opcode.JUMP_IF_FALSE):
        return (0,)
    return ()
