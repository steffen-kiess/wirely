"""Native-independent verification of ordinary bytecode programs."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ._binary import BinaryFile, RawInstruction, TABLES, UINT32_MAX, ValueRef, at, checked, slice_range
from .generated import format as f
from .limits import MAX_NESTING_DEPTH, MAX_STORAGE_SIZE, MAX_TOTAL_MEMBER_COUNT
from .records import record_size, unpack_record


class VerificationError(ValueError):
    """A binary program violates a format validity rule."""


class VerificationLimitError(VerificationError):
    """Verification exceeded its configured work budget."""


class _Budget:
    def __init__(self, steps: int) -> None:
        self.remaining = steps

    def charge(self, count: int = 1) -> None:
        if count > self.remaining:
            raise VerificationLimitError('Verification work budget exhausted')
        self.remaining -= count


@dataclass(frozen=True)
class _Layout:
    alignment: int
    data_size: int
    size: int
    offsets: tuple[int, ...] = ()


def _layout(alignment: int, data_size: int, offsets: tuple[int, ...] = ()) -> _Layout:
    return _Layout(alignment, data_size, checked(data_size + -data_size % alignment), offsets)


def _struct_layout(fields: Sequence[_Layout]) -> _Layout:
    offsets = []
    end = data_size = 0
    alignment = 1
    for field in fields:
        offset = checked(end + -end % field.alignment)
        offsets.append(offset)
        data_size = checked(offset + field.data_size)
        end = checked(offset + field.size)
        alignment = max(alignment, field.alignment)
    return _layout(alignment, data_size, tuple(offsets))


@dataclass(frozen=True)
class _TypeInfo:
    kind: f.TypeKind
    record: Any
    const: _Layout | None
    representation: _Layout | None
    runtime: bool
    depth: int = 0
    members: int = 1
    fields: tuple[int, ...] = ()


class _Verifier:
    def __init__(self, file: BinaryFile) -> None:
        self.file = file
        self.charge = file.charge
        self.types: list[_TypeInfo] = []
        self.type_lists = file.table(f.SectionType.TypeLists, f.TypeEntry)
        self.constants = file.table(f.SectionType.ConstValues, f.Variable)
        self.functions = file.table(f.SectionType.NativeFunctions, f.NativeFunction)
        self.natives = file.table(f.SectionType.NativeProcedures, f.NativeProcedure)
        self.bindings = file.table(f.SectionType.Bindings, f.VariableRef)
        self.bytecode = file.table(f.SectionType.BytecodeProcedures, f.BytecodeProcedure)
        self.variables: dict[f.VariableBank, list[f.Variable]] = {}
        self._contexts: set[tuple[int, f.RunStage, tuple[int, ...], tuple[int, ...]]] = set()

    def zero(self, data: memoryview, offset: int, length: int) -> None:
        raw = slice_range(data, offset, length, 'Padding')
        self.charge(length)
        if any(raw):
            raise ValueError('Padding must be zero')

    def info(self, index: int) -> _TypeInfo:
        return at(self.types, index, 'ValueTypes')

    def type_list(self, interval: f.Range) -> tuple[int, ...]:
        return tuple(entry.value_type.value for entry in self.file.range(self.type_lists, interval, 'TypeLists'))

    def read_types(self) -> None:
        data = self.file.buffer(f.SectionType.ValueTypes)
        if len(data) % 32:
            raise ValueError('Section ValueTypes contains a truncated record')
        self.charge(len(data) + len(data) // 32)
        layouts: dict[f.TypeKind, type[tuple[Any, ...]]] = {
            f.TypeKind.Unit: f.SimpleType, f.TypeKind.Boolean: f.SimpleType, f.TypeKind.Integer: f.IntegerType,
            f.TypeKind.String: f.SimpleType, f.TypeKind.Type: f.SimpleType, f.TypeKind.ClosedEnum: f.ClosedEnumType,
            f.TypeKind.OpenEnum: f.OpenEnumType, f.TypeKind.Struct: f.StructType, f.TypeKind.Resource: f.ResourceType,
            f.TypeKind.Option: f.CompositeType, f.TypeKind.List: f.CompositeType,
        }
        for offset in range(0, len(data), 32):
            try:
                kind = f.TypeKind(data[offset])
                if kind not in layouts:
                    raise ValueError(f'Unknown type kind {kind}')
                record: Any = unpack_record(layouts[kind], data, offset)
                self.zero(data, offset + record_size(layouts[kind]), 32 - record_size(layouts[kind]))
                self.types.append(self.make_type(kind, record))
            except VerificationLimitError:
                raise
            except ValueError as error:
                raise ValueError(f'ValueTypes[{offset // 32}]: {error}') from error
        # Unused TypeLists entries still contain references.
        for entry in self.type_lists:
            self.info(entry.value_type.value)

    def make_type(self, kind: f.TypeKind, record: Any) -> _TypeInfo:
        representation: _Layout | None
        const: _Layout | None
        depth, members = 0, 1
        fields: tuple[int, ...] = ()
        if kind == f.TypeKind.Unit:
            const = representation = _layout(1, 0)
        elif kind == f.TypeKind.Boolean:
            const = representation = _layout(1, 1)
        elif kind == f.TypeKind.Integer:
            if record.bits not in (8, 16, 32, 64, 128):
                raise ValueError('Invalid integer width')
            const = representation = _layout(record.bits // 8, record.bits // 8)
        elif kind in (f.TypeKind.String, f.TypeKind.Type):
            representation = None
            const = _layout(4, 8 if kind == f.TypeKind.String else 4)
        elif kind == f.TypeKind.Resource:
            self.file.string(record.native_name)
            const, representation = None, _layout(1, 0)
        elif kind in (f.TypeKind.ClosedEnum, f.TypeKind.OpenEnum):
            self.charge(1)
            underlying = self.info(record.underlying_type.value)
            if underlying.kind != f.TypeKind.Integer:
                raise ValueError('Enum underlying type must be an integer')
            if kind == f.TypeKind.ClosedEnum:
                if (underlying.record.signed or underlying.record.bits not in (8, 16, 32)
                        or not 1 <= record.member_count <= 1 << underlying.record.bits):
                    raise ValueError('Invalid closed enum member count or underlying type')
            self.file.optional_name(record.native_name)
            const = representation = underlying.representation
        else:
            if kind == f.TypeKind.Struct:
                fields = self.type_list(record.fields)
                name = self.file.optional_name(record.native_name)
                if record.opaque and name is None:
                    raise ValueError('Opaque struct requires a native name')
            else:
                fields = (record.inner_type.value,)
                self.charge(1)
            children = [self.info(index) for index in fields]
            if any(child.kind == f.TypeKind.Resource for child in children):
                raise ValueError('Composite types cannot contain resources')
            depth = min(MAX_NESTING_DEPTH + 1, 1 + max((child.depth for child in children), default=0))
            members = min(MAX_TOTAL_MEMBER_COUNT + 1, 1 + sum(child.members for child in children))
            if kind == f.TypeKind.Struct:
                const = _struct_layout([child.const for child in children if child.const is not None])
                representation = (_struct_layout([child.representation for child in children if child.representation is not None])
                                  if all(child.representation is not None for child in children) else None)
            elif kind == f.TypeKind.List:
                const, representation = _layout(4, 8), None
            else:
                child = children[0]
                assert child.const is not None
                const = _layout(child.const.alignment, checked(child.const.data_size + 1))
                representation = (None if child.representation is None else
                                  _layout(child.representation.alignment, checked(child.representation.data_size + 1)))
        runtime = (kind != f.TypeKind.Resource and representation is not None
                   and representation.size <= MAX_STORAGE_SIZE and depth <= MAX_NESTING_DEPTH
                   and members <= MAX_TOTAL_MEMBER_COUNT)
        return _TypeInfo(kind, record, const, representation, runtime, depth, members, fields)

    def value(self, type_index: int, offset: int, data: memoryview, *, runtime: bool = False) -> None:
        # Iterators keep the work stack proportional to nesting, not array counts.
        stack: list[Iterator[tuple[int, int, bool]]] = [iter(((type_index, offset, True),))]
        while stack:
            try:
                index, start, padded = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            info = self.info(index)
            layout = info.representation if runtime else info.const
            if layout is None or info.kind == f.TypeKind.Resource:
                raise ValueError('Value has a resource or unsupported storage type')
            self.charge(1 + layout.data_size)
            if start % layout.alignment:
                raise ValueError('Value slot is not aligned for its type')
            size = layout.size if padded else layout.data_size
            slot = memoryview(slice_range(data, start, size, 'Value slot'))
            self.zero(slot, layout.data_size, size - layout.data_size)
            kind = info.kind
            if kind == f.TypeKind.Boolean:
                if slot[0] > 1:
                    raise ValueError('Boolean value must be zero or one')
            elif kind in (f.TypeKind.ClosedEnum, f.TypeKind.OpenEnum):
                underlying = self.info(info.record.underlying_type.value)
                number = int.from_bytes(slot[:layout.data_size], 'little', signed=underlying.record.signed)
                if kind == f.TypeKind.ClosedEnum and number >= info.record.member_count:
                    raise ValueError('Closed enum value exceeds member count')
            elif kind == f.TypeKind.String:
                self.file.string(unpack_record(f.StringRef, slot), f.SectionType.ProgramData)
            elif kind == f.TypeKind.Type:
                self.info(unpack_record(f.ValueTypeValue, slot).value_type.value)
            elif kind == f.TypeKind.List:
                record = unpack_record(f.ListValue, slot)
                inner = self.info(info.fields[0]).const
                assert inner is not None
                if not record.element_count and record.offset:
                    raise ValueError('Empty list must have offset zero')
                if record.offset % inner.alignment:
                    raise ValueError('List elements are not aligned')
                slice_range(data, record.offset, checked(record.element_count * inner.size), 'List elements')
                self.charge(record.element_count)
                stack.append(self.array_values(info.fields[0], record.offset, inner.size, record.element_count))
            elif kind == f.TypeKind.Option:
                flag = slot[layout.data_size - 1]
                if flag > 1:
                    raise ValueError('Invalid option presence byte')
                if not flag:
                    self.zero(slot, 0, layout.data_size - 1)
                else:
                    # An option reuses its payload's tail padding for the flag.
                    stack.append(iter(((info.fields[0], start, False),)))
            elif kind == f.TypeKind.Struct:
                end = 0
                for child_index, child_offset in zip(info.fields, layout.offsets):
                    self.zero(slot, end, child_offset - end)
                    child_info = self.info(child_index)
                    child_layout = child_info.representation if runtime else child_info.const
                    assert child_layout is not None
                    end = child_offset + child_layout.size
                stack.append(self.struct_values(info.fields, layout.offsets, start))

    @staticmethod
    def struct_values(fields: tuple[int, ...], offsets: tuple[int, ...], start: int) -> Iterator[tuple[int, int, bool]]:
        return ((index, start + offset, position + 1 < len(fields))
                for position, (index, offset) in enumerate(zip(fields, offsets)))

    @staticmethod
    def array_values(index: int, start: int, stride: int, count: int) -> Iterator[tuple[int, int, bool]]:
        return ((index, start + item * stride, True) for item in range(count))

    def json(self) -> None:
        names = self.file.table(f.SectionType.JsonNames, f.JsonName)
        for name in names:
            self.file.string(name.name, f.SectionType.JsonData)
        previous = -1
        for record in self.file.table(f.SectionType.JsonTypes, f.JsonType):
            index = record.type.value
            if index <= previous:
                raise ValueError('JsonTypes must be strictly sorted by type index')
            previous = index
            info = self.info(index)
            if not record.count_ and record.names.value:
                raise ValueError('Empty JsonNames range must start at zero')
            self.file.range(names, f.Range(record.names, record.count_), 'JsonNames')
            if info.kind not in (f.TypeKind.Struct, f.TypeKind.ClosedEnum, f.TypeKind.OpenEnum):
                raise ValueError('JsonTypes entry must name an enum or struct')
            if info.kind == f.TypeKind.OpenEnum:
                underlying = self.info(info.record.underlying_type.value)
                layout = underlying.const
                assert layout is not None
                if (not record.count_ and record.numbers) or record.numbers % layout.alignment:
                    raise ValueError('Invalid open enum number offset or alignment')
                data = memoryview(slice_range(self.file.buffer(f.SectionType.JsonData), record.numbers,
                                              checked(record.count_ * layout.size), 'Open enum numbers'))
                self.charge(len(data) + record.count_)
                previous_number = None
                for offset in range(0, len(data), layout.size):
                    number = int.from_bytes(data[offset:offset + layout.size], 'little', signed=underlying.record.signed)
                    if previous_number is not None and number <= previous_number:
                        raise ValueError('Open enum numbers must be strictly ascending')
                    previous_number = number
            else:
                expected = len(info.fields) if info.kind == f.TypeKind.Struct else info.record.member_count
                if record.numbers != UINT32_MAX or record.count_ != expected:
                    raise ValueError('Invalid JSON name count or Numbers sentinel')

    def variable_tables(self) -> None:
        self.variables[f.VariableBank.ConstValues] = self.constants
        data = self.file.buffer(f.SectionType.ProgramData)
        for index, record in enumerate(self.constants):
            try:
                self.value(record.value_type.value, record.offset, data)
            except VerificationLimitError:
                raise
            except ValueError as error:
                raise ValueError(f'ConstValues[{index}]: {error}') from error
        for section, banks in ((f.SectionType.FixedValues, (f.VariableBank.FixedValues,)),
                               (f.SectionType.Temporaries, (f.VariableBank.Temporaries,)),
                               (f.SectionType.StateVariables, (f.VariableBank.OldState, f.VariableBank.NewState))):
            records = self.file.table(section, f.Variable)
            end, alignment = 0, 1
            state = self.file.buffer(f.SectionType.InitialState)
            for index, record in enumerate(records):
                info = self.info(record.value_type.value)
                if not info.runtime and not (section == f.SectionType.FixedValues and info.kind == f.TypeKind.Resource):
                    raise ValueError(f'{section.name}[{index}] has an invalid storage type')
                layout = info.representation
                assert layout is not None
                if record.offset < end or record.offset % layout.alignment:
                    raise ValueError(f'{section.name}[{index}] has an invalid variable offset')
                if section == f.SectionType.StateVariables:
                    self.zero(state, end, record.offset - end)
                    self.value(record.value_type.value, record.offset, state, runtime=True)
                end = checked(record.offset + layout.size)
                alignment = max(alignment, layout.alignment)
            size = checked(end + -end % alignment)
            if section == f.SectionType.StateVariables:
                if len(state) != size:
                    raise ValueError('InitialState has an invalid size')
                self.zero(state, end, size - end)
            for bank in banks:
                self.variables[bank] = records

    def runtime_type(self, index: int) -> _TypeInfo:
        info = self.info(index)
        if not info.runtime:
            raise ValueError('Expected a runtime type')
        return info

    def names_and_bindings(self) -> None:
        for entries in (self.functions, self.natives):
            previous = None
            for entry in entries:
                self.charge(entry.name.length_bytes + 1)
                name = self.file.string(entry.name).encode('utf-8')
                if previous is not None and name <= previous:
                    raise ValueError('Native names must be strictly sorted and unique')
                previous = name
        for function in self.functions:
            for index in (*self.type_list(function.parameters), function.return_type.value):
                self.runtime_type(index)
        interfaces = self.file.table(f.SectionType.InterfaceNames, f.NameRef)
        for interface in interfaces:
            self.file.string(interface.name)
        for native in self.natives:
            for interval in (native.input_names, native.output_names):
                previous = None
                for item in self.file.range(interfaces, interval, 'InterfaceNames'):
                    self.charge(item.name.length_bytes + 1)
                    name = self.file.string(item.name).encode('utf-8')
                    if previous is not None and name <= previous:
                        raise ValueError('InterfaceNames range must be strictly sorted and unique')
                    previous = name
        for binding in self.bindings:
            if binding.bank not in tuple(f.VariableBank):
                raise ValueError(f'Unknown variable bank {binding.bank}')
            at(self.variables[binding.bank], binding.variable.value, binding.bank.name or 'Variable')

    def operand_type(self, operand: ValueRef | int, locals_: tuple[int, ...],
                     inputs: tuple[int, ...] | None, outputs: tuple[int, ...] | None) -> int | None:
        if not isinstance(operand, ValueRef):
            raise ValueError('Expected a value operand')
        if operand.kind == f.OperandKind.Immediate:
            return None
        if operand.kind == f.OperandKind.Const:
            index = at(self.constants, operand.index, 'ConstValues').value_type.value
        elif operand.kind == f.OperandKind.Local:
            index = at(locals_, operand.index, 'LOCAL')
        else:
            table = inputs if operand.kind == f.OperandKind.Input else outputs
            if table is None:
                if operand.index == UINT32_MAX:
                    raise ValueError('Operand index is reserved')
                return None
            index = at(table, operand.index, 'INPUT' if operand.kind == f.OperandKind.Input else 'OUTPUT')
        self.runtime_type(index)
        return index

    def immediate(self, operand: ValueRef, index: int | None = None, *, boolean: bool = False,
                  shift_count: bool = False) -> None:
        if boolean:
            if operand.index > 1:
                raise ValueError('Boolean immediate must be zero or one')
            return
        if shift_count:
            return  # Its encoded index already fits uint32.
        if index is None:
            raise ValueError('An immediate requires a typed counterpart')
        info = self.runtime_type(index)
        number = operand.index
        if info.kind == f.TypeKind.Boolean:
            self.immediate(operand, boolean=True)
            return
        if info.kind == f.TypeKind.ClosedEnum:
            if number >= info.record.member_count:
                raise ValueError('Closed enum immediate exceeds member count')
            return
        if info.kind == f.TypeKind.OpenEnum:
            info = self.info(info.record.underlying_type.value)
        if info.kind != f.TypeKind.Integer:
            raise ValueError('Immediate requires an integer, boolean, or enum type')
        if info.record.signed:
            number = (number >> 1) ^ -(number & 1)
            lower, upper = -(1 << (info.record.bits - 1)), (1 << (info.record.bits - 1)) - 1
        else:
            lower, upper = 0, (1 << info.record.bits) - 1
        if not lower <= number <= upper:
            raise ValueError('Immediate is outside its type range')

    def fail_format(self, index: int, count: int) -> None:
        constant = at(self.constants, index, 'ConstValues')
        info = self.info(constant.value_type.value)
        if info.kind != f.TypeKind.List or self.info(info.fields[0]).kind != f.TypeKind.String:
            raise ValueError('FAIL format must have type List[string]')
        data = self.file.buffer(f.SectionType.ProgramData)
        record = unpack_record(f.ListValue, data, constant.offset)
        if record.element_count != checked(2 * count + 1):
            raise ValueError('FAIL format has the wrong number of strings')
        self.charge(count)
        for item in range(count):
            specification = unpack_record(f.StringRef, data, record.offset + (2 * item + 1) * record_size(f.StringRef))
            if specification.length_bytes:
                raise ValueError('Unsupported FAIL format specification')

    def code(self, index: int, inputs: tuple[int, ...] | None = None, outputs: tuple[int, ...] | None = None,
             stage: f.RunStage | None = None) -> None:
        record = at(self.bytecode, index, 'BytecodeProcedures')
        locals_ = self.type_list(record.locals)
        for value_type in locals_:
            self.runtime_type(value_type)
        for instruction_index, instruction in enumerate(self.file.instructions(index)):
            try:
                self.instruction(instruction, locals_, inputs, outputs, stage)
            except VerificationLimitError:
                raise
            except ValueError as error:
                raise ValueError(f'BytecodeProcedures[{index}] instruction {instruction_index} at byte {instruction.offset}: {error}') from error

    def instruction(self, instruction: RawInstruction, locals_: tuple[int, ...], inputs: tuple[int, ...] | None,
                    outputs: tuple[int, ...] | None, stage: f.RunStage | None) -> None:
        opcode, operands = instruction.opcode, instruction.operands
        self.charge(1 + len(operands))
        operand_types = [self.operand_type(item, locals_, inputs, outputs) if isinstance(item, ValueRef) else None
                         for item in operands]
        if opcode == f.Opcode.FAIL:
            assert isinstance(operands[0], int) and isinstance(operands[1], int)
            self.fail_format(operands[0], operands[1])
            if stage is not None and stage != f.RunStage.Load:
                raise ValueError('FAIL is only allowed in the Load stage')
        if opcode in (f.Opcode.EQUAL, f.Opcode.LESS) and all(
                isinstance(item, ValueRef) and item.kind == f.OperandKind.Immediate for item in operands[1:]):
            raise ValueError('An immediate comparison requires a typed counterpart')
        # Instance-independent checks still apply to unused streams. Type rules
        # involving input/output bindings are deferred until an instance exists.
        for position, item in enumerate(operands):
            if not isinstance(item, ValueRef) or item.kind != f.OperandKind.Immediate:
                continue
            if opcode in (f.Opcode.JUMP_IF_TRUE, f.Opcode.JUMP_IF_FALSE):
                self.immediate(item, boolean=True)
                continue
            if opcode in (f.Opcode.SHIFT_LEFT, f.Opcode.SHIFT_RIGHT) and position == 2:
                self.immediate(item, shift_count=True)
                continue
            expected = self.expected_type(opcode, operands, operand_types, position)
            if expected is not None:
                self.immediate(item, expected)
            operand_types[position] = expected
        if inputs is None:
            # CALL and FAIL have table-based arities even without an instance.
            if opcode == f.Opcode.CALL:
                self.call(operands, operand_types, partial=True)
            return
        if opcode in (f.Opcode.JUMP, f.Opcode.RETURN, f.Opcode.FAIL):
            return
        if opcode in (f.Opcode.JUMP_IF_TRUE, f.Opcode.JUMP_IF_FALSE):
            condition = operands[0]
            if not isinstance(condition, ValueRef) or condition.kind != f.OperandKind.Immediate:
                self.require_kind(operand_types[0], f.TypeKind.Boolean)
            return
        destination = operand_types[0]
        if opcode == f.Opcode.SET_DEFAULT:
            return
        if opcode == f.Opcode.COPY:
            self.same(destination, operand_types[1])
            return
        if opcode == f.Opcode.CALL:
            self.call(operands, operand_types)
            return
        if opcode in (f.Opcode.CONVERT_WRAPPING, f.Opcode.CONVERT_SATURATING, f.Opcode.CONVERT_CHECKED):
            target = self.unwrap(destination) if opcode == f.Opcode.CONVERT_CHECKED else destination
            self.require_kind(target, f.TypeKind.Integer, f.TypeKind.Boolean)
            self.require_kind(operand_types[1], f.TypeKind.Integer, f.TypeKind.Boolean, f.TypeKind.OpenEnum, f.TypeKind.ClosedEnum)
            return
        if opcode in (f.Opcode.GET_FIELD, f.Opcode.SET_FIELD):
            struct_index = operand_types[1] if opcode == f.Opcode.GET_FIELD else destination
            info = self.require_kind(struct_index, f.TypeKind.Struct)
            if info.record.opaque:
                raise ValueError('Field access requires a non-opaque struct')
            assert isinstance(operands[2], int)
            field = at(info.fields, operands[2], 'Struct field')
            self.same(field, destination if opcode == f.Opcode.GET_FIELD else operand_types[1])
        elif opcode in (f.Opcode.MAKE_SOME, f.Opcode.GET_OPTION_VALUE, f.Opcode.IS_SOME):
            inner = self.unwrap(destination if opcode == f.Opcode.MAKE_SOME else operand_types[1])
            if opcode == f.Opcode.IS_SOME:
                self.require_kind(destination, f.TypeKind.Boolean)
            else:
                self.same(inner, operand_types[1] if opcode == f.Opcode.MAKE_SOME else destination)
        elif opcode in (f.Opcode.EQUAL, f.Opcode.LESS):
            self.require_kind(destination, f.TypeKind.Boolean)
            self.same(operand_types[1], operand_types[2])
            if opcode == f.Opcode.LESS:
                self.require_kind(operand_types[1], f.TypeKind.Integer)
        elif opcode in (f.Opcode.SHIFT_LEFT, f.Opcode.SHIFT_RIGHT):
            self.require_kind(destination, f.TypeKind.Integer)
            self.same(destination, operand_types[1])
            if operand_types[2] is not None:
                count = self.require_kind(operand_types[2], f.TypeKind.Integer)
                if count.record.signed or count.record.bits != 32:
                    raise ValueError('Shift count must have type uint32')
        elif opcode in (f.Opcode.NOT, f.Opcode.AND, f.Opcode.OR, f.Opcode.XOR):
            self.require_kind(destination, f.TypeKind.Integer, f.TypeKind.Boolean)
            for source in operand_types[1:]:
                self.same(destination, source)
        else:
            self.require_kind(operand_types[1], f.TypeKind.Integer)
            self.same(operand_types[1], operand_types[2])
            self.same(self.unwrap(destination) if opcode.name and opcode.name.endswith('_CHECKED') else destination,
                      operand_types[1])

    def expected_type(self, opcode: f.Opcode, operands: tuple[ValueRef | int, ...],
                      operand_types: list[int | None], position: int) -> int | None:
        if opcode == f.Opcode.CALL:
            assert isinstance(operands[1], int)
            function = at(self.functions, operands[1], 'NativeFunctions')
            return at(self.type_list(function.parameters), position - 3, 'Function parameter')
        if opcode in (f.Opcode.EQUAL, f.Opcode.LESS):
            return operand_types[3 - position]
        destination = operand_types[0]
        if destination is None:
            return None
        if opcode == f.Opcode.SET_FIELD:
            info = self.require_kind(destination, f.TypeKind.Struct)
            assert isinstance(operands[2], int)
            return at(info.fields, operands[2], 'Struct field')
        if opcode == f.Opcode.MAKE_SOME or (opcode.name and opcode.name.endswith('_CHECKED')):
            return self.unwrap(destination)
        return destination

    def require_kind(self, index: int | None, *kinds: f.TypeKind) -> _TypeInfo:
        if index is None:
            raise ValueError('Instruction requires a typed counterpart')
        info = self.info(index)
        if info.kind not in kinds:
            raise ValueError('Instruction operand has the wrong type kind')
        return info

    def unwrap(self, index: int | None) -> int:
        return self.require_kind(index, f.TypeKind.Option).fields[0]

    @staticmethod
    def same(left: int | None, right: int | None) -> None:
        if left is None or right is None or left != right:
            raise ValueError('Instruction operand type indices do not match')

    def call(self, operands: tuple[ValueRef | int, ...], operand_types: list[int | None], *, partial: bool = False) -> None:
        assert isinstance(operands[1], int) and isinstance(operands[2], int)
        function = at(self.functions, operands[1], 'NativeFunctions')
        parameters = self.type_list(function.parameters)
        if operands[2] != len(parameters):
            raise ValueError('CALL argument count does not match function signature')
        if not partial or operand_types[0] is not None:
            self.same(operand_types[0], function.return_type.value)
        for actual, expected in zip(operand_types[3:], parameters):
            if not partial or actual is not None:
                self.same(actual, expected)

    def instances(self) -> None:
        instances = self.file.table(f.SectionType.ProcedureInstances, f.ProcedureInstance)
        stages = self.file.table(f.SectionType.Stages, f.Stage)
        if [record.stage for record in stages] != list(f.RunStage):
            raise ValueError('Stages must contain Load, Input, Default, and Output in order')
        for index in range(len(self.bytecode)):
            self.code(index)
        assigned = {bank: bytearray((len(self.variables[bank]) + 7) // 8)
                    for bank in (f.VariableBank.FixedValues, f.VariableBank.Temporaries, f.VariableBank.NewState)}
        covered = 0
        for stage in stages:
            if stage.schedule.count_ and stage.schedule.first.value != covered:
                raise ValueError('Stage schedules must partition ProcedureInstances in order')
            for instance_index, record in enumerate(self.file.range(instances, stage.schedule, 'ProcedureInstances'), covered):
                try:
                    inputs = self.file.range(self.bindings, record.inputs, 'Bindings')
                    outputs = self.file.range(self.bindings, record.outputs, 'Bindings')
                    for source in inputs:
                        if stage.stage == f.RunStage.Load and source.bank not in (f.VariableBank.ConstValues, f.VariableBank.FixedValues):
                            raise ValueError('Load procedures can only read constants and fixed values')
                        if source.bank in assigned and not self.bit(assigned[source.bank], source.variable.value):
                            raise ValueError('Variable is read before assignment')
                    for target in outputs:
                        allowed = (f.VariableBank.FixedValues,) if stage.stage == f.RunStage.Load else (
                            f.VariableBank.Temporaries, f.VariableBank.NewState)
                        if target.bank not in allowed:
                            raise ValueError('Output bank is not allowed in this stage')
                        if self.bit(assigned[target.bank], target.variable.value):
                            raise ValueError('Variable is assigned more than once')
                        assigned[target.bank][target.variable.value // 8] |= 1 << (target.variable.value % 8)
                    if (record.bytecode.value == UINT32_MAX) == (record.native.value == UINT32_MAX):
                        raise ValueError('Procedure instance must select exactly one implementation')
                    if record.native.value != UINT32_MAX:
                        native = at(self.natives, record.native.value, 'NativeProcedures')
                        if native.initializer and stage.stage != f.RunStage.Load:
                            raise ValueError('Initializers must be load procedures')
                        if len(inputs) != native.input_names.count_ or len(outputs) != native.output_names.count_:
                            raise ValueError('Native binding counts do not match interface names')
                    else:
                        input_types = tuple(self.variables[item.bank][item.variable.value].value_type.value for item in inputs)
                        output_types = tuple(self.variables[item.bank][item.variable.value].value_type.value for item in outputs)
                        for index in (*input_types, *output_types):
                            self.runtime_type(index)
                        context = record.bytecode.value, stage.stage, input_types, output_types
                        if context not in self._contexts:
                            self.code(record.bytecode.value, input_types, output_types, stage.stage)
                            self._contexts.add(context)
                except VerificationLimitError:
                    raise
                except ValueError as error:
                    raise ValueError(f'ProcedureInstances[{instance_index}]: {error}') from error
            covered += stage.schedule.count_
        if covered != len(instances):
            raise ValueError('Unscheduled procedure instances')
        for bank, bitmap in assigned.items():
            for index in range(len(self.variables[bank])):
                self.charge(1)
                if not self.bit(bitmap, index):
                    raise ValueError(f'{bank.name}[{index}] has no output assignment')

    @staticmethod
    def bit(bitmap: bytearray, index: int) -> bool:
        return bool(bitmap[index // 8] & (1 << (index % 8)))

    def verify(self) -> None:
        # Parse every known record table, including unreferenced entries.
        for section, (_, record_class) in TABLES.items():
            self.file.table(section, record_class)
        self.read_types()
        self.json()
        self.variable_tables()
        self.names_and_bindings()
        self.instances()


def _verified_file(data: bytes | bytearray | memoryview, max_steps: int) -> BinaryFile:
    if max_steps < 0:
        raise ValueError('max_steps must be nonnegative')
    try:
        budget = _Budget(max_steps)
        file = BinaryFile(bytes(data), budget.charge)
        _Verifier(file).verify()
        return file
    except VerificationError:
        raise
    except ValueError as error:
        raise VerificationError(str(error)) from error


def verify_program(data: bytes | bytearray | memoryview, *, max_steps: int = 10_000_000) -> None:
    """Verify ordinary sections without decoding a Program or invoking native code.

    The budget counts record and instruction visits and bytes, type edges,
    operands, value visits, and inspected string and padding bytes.
    Opaque-value semantics are checked during linking.
    """
    _verified_file(data, max_steps)
