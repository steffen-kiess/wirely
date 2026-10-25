"""Decode ordinary binary program sections into a resolved Program."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, overload

from .. import types
from ..runtime.extensions import NativeTypes, resolve_extensions
from .const_data import ConstDataReader
from ._binary import BinaryFile, ValueRef
from .container import SectionList
from .generated import format as fmt
from .procedures import (
    ARITHMETIC_OPCODES, BranchTarget, Constant, ConstOperand, Function,
    Instruction, Opcode, Operand, OperandKind, ProcedureBody,
)
from .program import (
    InputSource, NativeProcedureEntry, OutputTarget, Procedure, Program,
    RunStage, Variable, VariableBank, VariableTable,
)
from .records import unpack_record
from .validation import validate_program
from .verifier import verified_file

T = TypeVar('T')
R = TypeVar('R', bound=tuple[Any, ...])


@dataclass(frozen=True)
class _Immediate:
    number: int


@dataclass
class _RawInstruction:
    opcode: Opcode
    operands: list[Any]


class _Loader:
    """Decode a file that passed verification, so references and layouts are valid."""

    def __init__(self, file: BinaryFile, native_types: NativeTypes, max_decoded_values: int) -> None:
        self.file = file
        self.native_types = native_types
        self.type_lists = self.table(fmt.SectionType.TypeLists, fmt.TypeEntry)
        self.json_names = self.table(fmt.SectionType.JsonNames, fmt.JsonName)
        self.json_types = {record.type.value: record for record in self.table(fmt.SectionType.JsonTypes, fmt.JsonType)}
        self.types: list[types.Type] = []
        self.read_types()
        reader = ConstDataReader(self.buffer(fmt.SectionType.ProgramData), self.type_at,
                                 max_values=max_decoded_values)
        self.constants = []
        for index, record in enumerate(self.table(fmt.SectionType.ConstValues, fmt.Variable)):
            value_type = self.type_at(record.value_type.value)
            try:
                self.constants.append(Constant(value_type, reader.read(record.offset, value_type)))
            except ValueError as error:
                raise ValueError(f'ConstValues[{index}]: {error}') from error
        self.variable_tables = {}
        self.variables: dict[VariableBank, list[Variable]] = {}
        self.variable_records = {}
        for section, banks, name in (
                (fmt.SectionType.FixedValues, (VariableBank.FIXED_VALUES,), 'fixed'),
                (fmt.SectionType.Temporaries, (VariableBank.TEMPORARIES,), 'temporary'),
                (fmt.SectionType.StateVariables, (VariableBank.OLD_STATE, VariableBank.NEW_STATE), 'state')):
            records = self.table(section, fmt.Variable)
            variables = [Variable(f'{name}.{index}', self.type_at(record.value_type.value))
                         for index, record in enumerate(records)]
            self.variable_tables[section] = VariableTable(variables, name)
            self.variable_records[section] = records
            for bank in banks:
                self.variables[bank] = variables
        self.functions = [Function(self.string(record.name), tuple(self.type_list(record.parameters)),
                                   self.type_at(record.return_type.value))
                          for record in self.table(fmt.SectionType.NativeFunctions, fmt.NativeFunction)]
        interface_names = self.table(fmt.SectionType.InterfaceNames, fmt.NameRef)

        def interfaces(interval: fmt.Range) -> tuple[str, ...]:
            return tuple(self.string(record.name) for record in self.range(interface_names, interval))

        self.natives = [NativeProcedureEntry(self.string(record.name), interfaces(record.input_names),
                                             interfaces(record.output_names), record.initializer)
                        for record in self.table(fmt.SectionType.NativeProcedures, fmt.NativeProcedure)]
        self.bindings = self.table(fmt.SectionType.Bindings, fmt.VariableRef)
        self.bytecode = self.table(fmt.SectionType.BytecodeProcedures, fmt.BytecodeProcedure)
        self.raw_bodies = [self.raw_body(index) for index in range(len(self.bytecode))]
        self.resolved_bodies: dict[tuple[int, tuple[int, ...]], ProcedureBody] = {}

    def buffer(self, section: fmt.SectionType) -> memoryview:
        return self.file.buffer(section)

    def table(self, section: fmt.SectionType, record_class: type[R]) -> list[R]:
        return self.file.table(section, record_class)

    @staticmethod
    def range(items: Sequence[T], interval: fmt.Range) -> Sequence[T]:
        return items[interval.first.value:interval.first.value + interval.count_]

    def string(self, ref: fmt.StringRef | fmt.OptionalStringRef,
               section: fmt.SectionType = fmt.SectionType.NativeNames) -> str:
        return self.file.string(ref, section)

    def optional_name(self, ref: fmt.OptionalStringRef) -> str | None:
        return self.file.optional_name(ref)

    def type_at(self, index: int) -> types.Type:
        return self.types[index]

    def type_list(self, interval: fmt.Range) -> list[types.Type]:
        return [self.type_at(entry.value_type.value) for entry in self.range(self.type_lists, interval)]

    def read_types(self) -> None:
        data = self.buffer(fmt.SectionType.ValueTypes)
        layouts = {
            fmt.TypeKind.Unit: fmt.SimpleType, fmt.TypeKind.Boolean: fmt.SimpleType,
            fmt.TypeKind.String: fmt.SimpleType, fmt.TypeKind.Type: fmt.SimpleType,
            fmt.TypeKind.Integer: fmt.IntegerType, fmt.TypeKind.ClosedEnum: fmt.ClosedEnumType,
            fmt.TypeKind.OpenEnum: fmt.OpenEnumType, fmt.TypeKind.Struct: fmt.StructType,
            fmt.TypeKind.Resource: fmt.ResourceType, fmt.TypeKind.Option: fmt.CompositeType,
            fmt.TypeKind.List: fmt.CompositeType,
        }
        for offset in range(0, len(data), 32):
            kind = fmt.TypeKind(data[offset])
            record: Any = unpack_record(layouts[kind], data, offset)
            self.types.append(self.make_type(offset // 32, kind, record))

    def make_type(self, index: int, kind: fmt.TypeKind, record: Any) -> types.Type:
        primitives = {fmt.TypeKind.Unit: types.unit, fmt.TypeKind.Boolean: types.boolean,
                      fmt.TypeKind.String: types.String, fmt.TypeKind.Type: types.ValueType}
        if kind in primitives:
            return primitives[kind]
        if kind == fmt.TypeKind.Integer:
            return getattr(types, f'{"int" if record.signed else "uint"}{record.bits}')
        json_record = self.json_types.get(index)
        names = None if json_record is None else tuple(
            self.string(item.name, fmt.SectionType.JsonData)
            for item in self.range(self.json_names, fmt.Range(json_record.names, json_record.count_)))
        if kind in (fmt.TypeKind.ClosedEnum, fmt.TypeKind.OpenEnum):
            underlying = self.type_at(record.underlying_type.value)
            assert isinstance(underlying, types.TypeInt)
            json_names = {}
            if names is not None:
                assert json_record is not None
                if kind == fmt.TypeKind.OpenEnum:
                    start = json_record.numbers
                    numbers = bytes(self.buffer(fmt.SectionType.JsonData)[start:start + len(names) * underlying.size])
                    values = [underlying.deserialize(numbers[offset:offset + underlying.size])
                              for offset in range(0, len(numbers), underlying.size)]
                else:
                    values = list(range(record.member_count))
                json_names = dict(zip(values, names))
            enum_type = types.TypeEnum(None, {}, is_open=kind == fmt.TypeKind.OpenEnum, underlying_type=underlying,
                                       member_count=record.member_count if kind == fmt.TypeKind.ClosedEnum else None,
                                       native_name=self.optional_name(record.native_name), json_names=json_names)
            enum_type.is_json_serializable = json_record is not None
            return enum_type
        if kind == fmt.TypeKind.Struct:
            return types.TypeStruct(None, self.type_list(record.fields), native_name=self.optional_name(record.native_name),
                                    is_opaque=record.opaque, json_names=names)
        if kind == fmt.TypeKind.Resource:
            name = self.string(record.native_name)
            return types.TypeResource(name, name)
        inner = self.type_at(record.inner_type.value)
        return types.TypeOption(inner) if kind == fmt.TypeKind.Option else types.TypeList(inner)

    def raw_body(self, index: int) -> list[_RawInstruction]:
        instructions = []
        for raw in self.file.instructions(index):
            opcode = Opcode[raw.opcode.name or '']
            operands: list[Any] = []
            for position, operand in enumerate(raw.operands):
                if isinstance(operand, ValueRef):
                    if operand.kind == fmt.OperandKind.Immediate:
                        operands.append(_Immediate(operand.index))
                    elif operand.kind == fmt.OperandKind.Const:
                        operands.append(ConstOperand(self.constants[operand.index]))
                    else:
                        operands.append(Operand(OperandKind[(operand.kind.name or '').upper()], operand.index))
                elif opcode == Opcode.CALL and position == 1:
                    operands.append(self.functions[operand])
                elif opcode == Opcode.FAIL and position == 0:
                    operands.append(self.constants[operand])
                elif opcode == Opcode.JUMP or (opcode in (Opcode.JUMP_IF_TRUE, Opcode.JUMP_IF_FALSE) and position == 1):
                    operands.append(BranchTarget(operand))
                else:
                    operands.append(operand)
            instructions.append(_RawInstruction(opcode, operands))
        return instructions

    def body(self, index: int, inputs: tuple[InputSource, ...], outputs: tuple[OutputTarget, ...]) -> ProcedureBody:
        locals_ = tuple(self.type_list(self.bytecode[index].locals))
        tables = {OperandKind.LOCAL: locals_, OperandKind.INPUT: tuple(source.value_type for source in inputs),
                  OperandKind.OUTPUT: tuple(target.variable.var_type for target in outputs)}

        def operand_type(operand: Any) -> types.Type:
            if isinstance(operand, Operand):
                return tables[operand.kind][operand.index]
            assert isinstance(operand, ConstOperand)
            return operand.constant.value_type

        instructions = []
        immediate_types = []
        for raw in self.raw_bodies[index]:
            operands = list(raw.operands)
            opcode = raw.opcode
            for position, operand in enumerate(raw.operands):
                if not isinstance(operand, _Immediate):
                    continue
                value_type: types.Type
                if opcode in (Opcode.JUMP_IF_TRUE, Opcode.JUMP_IF_FALSE):
                    value_type = types.boolean
                elif opcode in (Opcode.SHIFT_LEFT, Opcode.SHIFT_RIGHT) and position == 2:
                    value_type = types.uint32
                elif opcode == Opcode.CALL:
                    value_type = operands[1].parameter_types[position - 3]
                elif opcode in (Opcode.EQUAL, Opcode.LESS):
                    value_type = operand_type(raw.operands[3 - position])
                else:
                    value_type = operand_type(raw.operands[0])
                    if opcode == Opcode.SET_FIELD:
                        assert isinstance(value_type, types.TypeStruct)
                        value_type = value_type.field_types[operands[2]]
                    elif opcode == Opcode.MAKE_SOME or (opcode in ARITHMETIC_OPCODES and opcode.name.endswith('_CHECKED')):
                        assert isinstance(value_type, types.TypeOption)
                        value_type = value_type.inner_type
                representation = value_type.underlying_type if isinstance(value_type, types.TypeEnum) else value_type
                number = operand.number
                value: Any
                if isinstance(representation, types.TypeBoolean):
                    value = bool(number)
                else:
                    assert isinstance(representation, types.TypeInt)
                    value = (number >> 1) ^ -(number & 1) if representation.is_signed else number
                operands[position] = ConstOperand(Constant(value_type, value))
                immediate_types.append(id(value_type))
            instructions.append(Instruction(opcode, tuple(operands)))
        key = index, tuple(immediate_types)
        if key not in self.resolved_bodies:
            self.resolved_bodies[key] = ProcedureBody(tuple(instructions), locals_)
        return self.resolved_bodies[key]

    @overload
    def binding(self, record: fmt.VariableRef, *, output: Literal[False] = False) -> InputSource: ...

    @overload
    def binding(self, record: fmt.VariableRef, *, output: Literal[True]) -> OutputTarget: ...

    def binding(self, record: fmt.VariableRef, *, output: bool = False) -> InputSource | OutputTarget:
        bank = VariableBank['_'.join(part.upper() for part in self._bank_parts(record.bank))]
        if bank == VariableBank.CONST_VALUES:
            assert not output
            return InputSource(bank, constant=self.constants[record.variable.value])
        variable = self.variables[bank][record.variable.value]
        return OutputTarget(bank, variable) if output else InputSource(bank, variable)

    @staticmethod
    def _bank_parts(bank: fmt.VariableBank) -> tuple[str, ...]:
        return {fmt.VariableBank.ConstValues: ('const', 'values'), fmt.VariableBank.FixedValues: ('fixed', 'values'),
                fmt.VariableBank.Temporaries: ('temporaries',), fmt.VariableBank.OldState: ('old', 'state'),
                fmt.VariableBank.NewState: ('new', 'state')}[bank]

    def program(self) -> Program:
        state_vars = self.variables[VariableBank.OLD_STATE]
        state_records = self.variable_records[fmt.SectionType.StateVariables]
        state_data = self.buffer(fmt.SectionType.InitialState)
        initial = {}
        for variable, record in zip(state_vars, state_records):
            data = bytes(state_data[record.offset:record.offset + variable.var_type.data_size])
            initial[variable] = variable.var_type.deserialize(data)
        instances = self.table(fmt.SectionType.ProcedureInstances, fmt.ProcedureInstance)
        stages = self.table(fmt.SectionType.Stages, fmt.Stage)
        schedules = {}
        for stage, run_stage in zip(stages, RunStage):
            schedule = []
            for index, instance in enumerate(self.range(instances, stage.schedule), stage.schedule.first.value):
                inputs = tuple(self.binding(binding) for binding in self.range(self.bindings, instance.inputs))
                outputs = tuple(self.binding(binding, output=True) for binding in self.range(self.bindings, instance.outputs))
                native = None if instance.native.value == 0xffffffff else self.natives[instance.native.value]
                body = None if native is not None else self.body(instance.bytecode.value, inputs, outputs)
                schedule.append(Procedure(f'procedure{index}.run', inputs, outputs, native=native, body=body))
            schedules[run_stage] = tuple(schedule)
        # Decoding is independent of native implementations. Linking and opaque
        # value checks happen when Engine is constructed with the same registry.
        for value_type in self.types:
            if isinstance(value_type, types.TypeStruct):
                value_type.native_types = self.native_types
        program = Program(state_variables=self.variable_tables[fmt.SectionType.StateVariables],
                          temporaries=self.variable_tables[fmt.SectionType.Temporaries],
                          fixed_values=self.variable_tables[fmt.SectionType.FixedValues],
                          initial_state=initial, procs=schedules)
        validate_program(program)
        return program


def read_build_id(data: bytes | bytearray | memoryview) -> bytes:
    """Return the 64-byte build ID stored in a binary program file."""
    for section in SectionList.from_bytes(data).sections:
        if section.section_type == fmt.SectionType.BuildIDSHA512:
            return section.data
    raise ValueError('Missing section BuildIDSHA512')


def load_program(data: bytes | bytearray | memoryview, *, native_types: NativeTypes | None = None,
                 max_decoded_values: int = 1_000_000, max_verification_steps: int = 10_000_000) -> Program:
    """Decode a binary program without linking or executing its load stage.

    native_types defaults to the built-in registry. max_decoded_values bounds
    the total number of const values, including nested fields and list elements.
    max_verification_steps bounds the preceding native-independent verification.
    """
    if max_decoded_values < 0:
        raise ValueError('max_decoded_values must be nonnegative')
    file = verified_file(data, max_verification_steps)
    if native_types is None:
        native_types = resolve_extensions().native_types
    try:
        return _Loader(file, native_types, max_decoded_values).program()
    except RecursionError as error:
        raise ValueError('Program types or values are nested too deeply') from error
