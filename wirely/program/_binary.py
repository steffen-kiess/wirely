"""Bounded, native-independent table and instruction readers."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from .container import SectionList
from .encoding import decode_integer_parameter, decode_value_operand, immediate_positions
from .generated import format as f
from .records import record_size, unpack_record

T = TypeVar('T')
R = TypeVar('R', bound=tuple[Any, ...])
UINT32_MAX = 0xffffffff


def at(items: Sequence[T], index: int, what: str) -> T:
    if not 0 <= index < len(items):
        raise ValueError(f'{what} index {index} is out of range')
    return items[index]


def checked(value: int) -> int:
    if not 0 <= value <= UINT32_MAX:
        raise ValueError('Computed size or offset does not fit uint32')
    return value


@overload
def slice_range(items: memoryview, first: int, count: int, what: str) -> memoryview: ...


@overload
def slice_range(items: Sequence[T], first: int, count: int, what: str) -> Sequence[T]: ...


def slice_range(items: Sequence[T], first: int, count: int, what: str) -> Sequence[T]:
    if first < 0 or count < 0 or checked(first + count) > len(items):
        raise ValueError(f'{what} range {first}+{count} is out of bounds')
    return items[first:first + count]


@dataclass(frozen=True)
class BinarySection:
    record_type: int
    data: memoryview


@dataclass(frozen=True)
class ValueRef:
    kind: f.OperandKind
    index: int


@dataclass(frozen=True)
class RawInstruction:
    opcode: f.Opcode
    operands: tuple[ValueRef | int, ...]
    offset: int


# Buffers have record type zero; structured sections name their layouts.
TABLES: dict[f.SectionType, tuple[f.RecordType, type[tuple[Any, ...]]]] = {
    f.SectionType.TypeLists: (f.RecordType.TypeEntry, f.TypeEntry),
    f.SectionType.ConstValues: (f.RecordType.Variable, f.Variable),
    f.SectionType.FixedValues: (f.RecordType.Variable, f.Variable),
    f.SectionType.Temporaries: (f.RecordType.Variable, f.Variable),
    f.SectionType.StateVariables: (f.RecordType.Variable, f.Variable),
    f.SectionType.NativeFunctions: (f.RecordType.NativeFunction, f.NativeFunction),
    f.SectionType.BytecodeProcedures: (f.RecordType.BytecodeProcedure, f.BytecodeProcedure),
    f.SectionType.InterfaceNames: (f.RecordType.NameRef, f.NameRef),
    f.SectionType.NativeProcedures: (f.RecordType.NativeProcedure, f.NativeProcedure),
    f.SectionType.Bindings: (f.RecordType.VariableRef, f.VariableRef),
    f.SectionType.ProcedureInstances: (f.RecordType.ProcedureInstance, f.ProcedureInstance),
    f.SectionType.Stages: (f.RecordType.Stage, f.Stage),
    f.SectionType.JsonNames: (f.RecordType.JsonName, f.JsonName),
    f.SectionType.JsonTypes: (f.RecordType.JsonType, f.JsonType),
}


class BinaryFile:
    def __init__(self, data: bytes, charge: Callable[[int], None]) -> None:
        self.charge = charge
        self.data = memoryview(data)
        self.sections: dict[int, BinarySection] = {}
        self._tables: dict[f.SectionType, list[Any]] = {}
        self._strings: dict[tuple[f.SectionType, int, int], str] = {}
        self._code: dict[int, tuple[RawInstruction, ...]] = {}
        if len(data) < record_size(f.Header):
            raise ValueError('Truncated 16-byte header')
        header = unpack_record(f.Header, data)
        if header.magic != SectionList.MAGIC or header.version_major != SectionList.SUPPORTED_MAJOR:
            raise ValueError('Unsupported magic or major version')
        if header.file_length != len(data):
            raise ValueError('Header FileLength does not match file length')
        end = checked(record_size(f.Header) + checked(header.section_count * record_size(f.SectionEntry)))
        if end > len(data):
            raise ValueError('Directory extends past the end of the file')
        charge(end)
        previous = 0
        for offset in range(record_size(f.Header), end, record_size(f.SectionEntry)):
            entry = unpack_record(f.SectionEntry, data, offset)
            if entry.section_type <= previous:
                raise ValueError('Section types must be nonzero and strictly ascending')
            previous = entry.section_type
            if entry.offset % 16:
                raise ValueError('Section offset is not aligned to 16 bytes')
            payload = slice_range(self.data, entry.offset, entry.length, 'Section')
            self.sections[entry.section_type] = BinarySection(entry.record_type, memoryview(payload))
        for section in f.SectionType:
            if section not in self.sections:
                raise ValueError(f'Missing section {section.name}')
            expected = TABLES[section][0] if section in TABLES else (
                f.RecordType.TypeRecord if section == f.SectionType.ValueTypes else f.RecordType.Buffer)
            if self.sections[section].record_type != expected:
                raise ValueError(f'Section {section.name} has the wrong record type; expected {expected.name}')
        if len(self.buffer(f.SectionType.BuildIDSHA512)) != 64:
            raise ValueError('BuildIDSHA512 must contain 64 bytes')

    def buffer(self, section: f.SectionType) -> memoryview:
        return self.sections[section].data

    def table(self, section: f.SectionType, record_class: type[R]) -> list[R]:
        if section not in self._tables:
            data = self.buffer(section)
            size = record_size(record_class)
            if len(data) % size:
                raise ValueError(f'Section {section.name} contains a truncated record')
            self.charge(len(data) + len(data) // size)
            try:
                self._tables[section] = [unpack_record(record_class, data, offset) for offset in range(0, len(data), size)]
            except ValueError as error:
                raise ValueError(f'Section {section.name}: {error}') from error
        return self._tables[section]

    def range(self, items: Sequence[T], interval: f.Range, what: str) -> Sequence[T]:
        if interval.count_ == 0 and interval.first.value != 0:
            raise ValueError(f'Empty {what} range must start at zero')
        self.charge(interval.count_)
        return slice_range(items, interval.first.value, interval.count_, what)

    def string(self, ref: f.StringRef | f.OptionalStringRef, section: f.SectionType = f.SectionType.NativeNames) -> str:
        key = section, ref.offset, ref.length_bytes
        if key not in self._strings:
            if not ref.length_bytes and ref.offset:
                raise ValueError('Empty strings must have offset zero')
            raw = slice_range(self.buffer(section), ref.offset, ref.length_bytes, section.name or 'String')
            self.charge(ref.length_bytes + 1)
            try:
                self._strings[key] = bytes(raw).decode('utf-8')
            except UnicodeDecodeError as error:
                raise ValueError(f'Section {section.name} contains invalid UTF-8') from error
        return self._strings[key]

    def optional_name(self, ref: f.OptionalStringRef) -> str | None:
        return None if ref == f.OptionalStringRef(UINT32_MAX, UINT32_MAX) else self.string(ref)

    def instructions(self, index: int) -> tuple[RawInstruction, ...]:
        if index in self._code:
            return self._code[index]
        record = at(self.table(f.SectionType.BytecodeProcedures, f.BytecodeProcedure), index, 'BytecodeProcedures')
        if not record.code.length and record.code.offset:
            raise ValueError('Empty byte range must have offset zero')
        data = memoryview(slice_range(self.buffer(f.SectionType.ProcedureCode), record.code.offset,
                                      record.code.length, 'ProcedureCode'))
        self.charge(len(data))
        rows: list[RawInstruction] = []
        starts: dict[int, int] = {}
        branches = []
        offset = 0
        while offset < len(data):
            start = offset
            starts[start] = len(rows)
            try:
                opcode = f.Opcode(data[offset])
                if opcode not in tuple(f.Opcode):
                    raise ValueError(f'Unknown opcode {opcode}')
                offset += 1
                operands: list[ValueRef | int] = []

                def value() -> None:
                    nonlocal offset
                    self.charge(1)
                    kind, number, offset = decode_value_operand(data, offset)
                    operands.append(ValueRef(kind, number))

                def parameter() -> int:
                    nonlocal offset
                    self.charge(1)
                    number, offset = decode_integer_parameter(data, offset)
                    return number

                if opcode in (f.Opcode.CALL, f.Opcode.FAIL):
                    if opcode == f.Opcode.CALL:
                        value()
                    operands.append(parameter())
                    count = parameter()
                    operands.append(count)
                    if count > len(data) - offset:
                        raise ValueError('Argument count exceeds instruction stream')
                    self.charge(count)
                    for _ in range(count):
                        value()
                else:
                    signatures = {f.Opcode.RETURN: '', f.Opcode.JUMP: 'b', f.Opcode.JUMP_IF_TRUE: 'vb',
                                  f.Opcode.JUMP_IF_FALSE: 'vb', f.Opcode.GET_FIELD: 'vvp', f.Opcode.SET_FIELD: 'vvp',
                                  f.Opcode.SET_DEFAULT: 'v', f.Opcode.COPY: 'vv', f.Opcode.MAKE_SOME: 'vv',
                                  f.Opcode.GET_OPTION_VALUE: 'vv', f.Opcode.IS_SOME: 'vv', f.Opcode.NOT: 'vv',
                                  f.Opcode.CONVERT_WRAPPING: 'vv', f.Opcode.CONVERT_SATURATING: 'vv',
                                  f.Opcode.CONVERT_CHECKED: 'vv'}
                    for kind in signatures.get(opcode, 'vvv'):
                        if kind == 'v':
                            value()
                        else:
                            number = parameter()
                            operands.append(number)
                            if kind == 'b':
                                branches.append((len(rows), len(operands) - 1, checked(offset + number)))
                for position, operand in enumerate(operands):
                    if isinstance(operand, ValueRef) and operand.kind == f.OperandKind.Immediate:
                        if position not in immediate_positions(opcode, len(operands)):
                            raise ValueError('Immediate is not allowed at this position')
                if opcode not in (f.Opcode.JUMP, f.Opcode.JUMP_IF_TRUE, f.Opcode.JUMP_IF_FALSE, f.Opcode.RETURN, f.Opcode.FAIL):
                    destination = operands[0]
                    if not isinstance(destination, ValueRef) or destination.kind not in (f.OperandKind.Local, f.OperandKind.Output):
                        raise ValueError('Destination must be local or output')
                rows.append(RawInstruction(opcode, tuple(operands), record.code.offset + start))
            except ValueError as error:
                raise ValueError(f'ProcedureCode at byte {record.code.offset + start}: {error}') from error
        if not rows or rows[-1].opcode not in (f.Opcode.RETURN, f.Opcode.FAIL):
            raise ValueError('Instruction stream must be nonempty and end in RETURN or FAIL')
        for row, position, target in branches:
            if target not in starts:
                raise ValueError('Branch target is not an instruction start inside the stream')
            operands = list(rows[row].operands)
            operands[position] = starts[target]
            rows[row] = RawInstruction(rows[row].opcode, tuple(operands), rows[row].offset)
        self._code[index] = tuple(rows)
        return self._code[index]
