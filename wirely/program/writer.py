"""Write resolved programs in the binary program format."""

from collections.abc import Iterable
from hashlib import sha512
from typing import Any

from ..types import (
    Type, TypeBoolean, TypeEnum, TypeInt, TypeList, TypeOption, TypeResource,
    TypeString, TypeStruct, TypeUnit, TypeValueType,
)
from .const_data import ConstDataWriter
from .container import Section, SectionList
from .encoding import encode_integer_parameter, encode_value_operand, immediate_positions, zigzag
from .generated import format as fmt
from .procedures import BranchTarget, Constant, ConstOperand, Function, Instruction, Operand, ProcedureBody
from .program import InputSource, OutputTarget, Program, RunStage, VariableBank, VariableTable
from .records import pack_record
from .validation import validate_program


def _range(first: int, count: int) -> fmt.Range:
    return fmt.Range(fmt.Ref(first if count else 0), count)


class _Buffer:
    """Deduplicate byte sequences while respecting their alignment."""

    def __init__(self) -> None:
        self.data = bytearray()
        self._offsets: dict[tuple[int, bytes], int] = {}

    def add(self, data: bytes, alignment: int = 1) -> int:
        if not data:
            return 0
        key = alignment, data
        if key not in self._offsets:
            self.data += bytes(-len(self.data) % alignment)
            self._offsets[key] = len(self.data)
            self.data += data
        return self._offsets[key]

    def string(self, text: str) -> fmt.StringRef:
        data = text.encode('utf-8')
        return fmt.StringRef(self.add(data), len(data))

    def optional_string(self, text: str | None) -> fmt.OptionalStringRef:
        return fmt.OptionalStringRef(*self.string(text)) if text is not None else fmt.OptionalStringRef(0xffffffff, 0xffffffff)


class _Types:
    def __init__(self, names: _Buffer) -> None:
        self.names = names
        self.types: list[Type] = []
        self.records: list[bytes] = []
        self.lists: list[fmt.TypeEntry] = []
        self._indices: dict[object, int] = {}
        self._list_ranges: dict[tuple[int, ...], fmt.Range] = {}

    def add(self, value_type: Type) -> int:
        # Composite keys contain interned dependency indices, so nominal types
        # stay distinct even when nested in structurally interned composites.
        key: object
        record: tuple[Any, ...]
        if isinstance(value_type, (TypeOption, TypeList)):
            inner = self.add(value_type.inner_type)
            kind = fmt.TypeKind.Option if isinstance(value_type, TypeOption) else fmt.TypeKind.List
            key = kind, inner
            record = fmt.CompositeType(kind, fmt.Ref(inner))
        elif isinstance(value_type, TypeEnum):
            underlying = self.add(value_type.underlying_type)
            key = id(value_type)
            native_name = self.names.optional_string(value_type.native_name)
            if value_type.is_open:
                record = fmt.OpenEnumType(fmt.TypeKind.OpenEnum, fmt.Ref(underlying), native_name)
            else:
                assert value_type.member_count is not None
                record = fmt.ClosedEnumType(fmt.TypeKind.ClosedEnum, fmt.Ref(underlying),
                                            value_type.member_count, native_name)
        elif isinstance(value_type, TypeStruct):
            fields = self.type_list(value_type.field_types)
            key = id(value_type)
            record = fmt.StructType(fmt.TypeKind.Struct, value_type.is_opaque, fields,
                                    self.names.optional_string(value_type.native_name))
        elif isinstance(value_type, TypeResource):
            key = id(value_type)
            record = fmt.ResourceType(fmt.TypeKind.Resource, self.names.string(value_type.native_name))
        elif isinstance(value_type, TypeInt):
            key = fmt.TypeKind.Integer, value_type.is_signed, value_type.bits
            record = fmt.IntegerType(fmt.TypeKind.Integer, value_type.is_signed, value_type.bits)
        else:
            kinds: dict[type[Type], fmt.TypeKind] = {
                TypeUnit: fmt.TypeKind.Unit, TypeBoolean: fmt.TypeKind.Boolean,
                TypeString: fmt.TypeKind.String, TypeValueType: fmt.TypeKind.Type,
            }
            if type(value_type) not in kinds:
                raise ValueError(f'Cannot encode type `{value_type}`')
            key = kinds[type(value_type)]
            record = fmt.SimpleType(kinds[type(value_type)])
        if key not in self._indices:
            self._indices[key] = len(self.types)
            self.types.append(value_type)
            data = pack_record(record)
            self.records.append(data + bytes(32 - len(data)))
        return self._indices[key]

    def type_list(self, value_types: Iterable[Type]) -> fmt.Range:
        indices = tuple(self.add(value_type) for value_type in value_types)
        if indices not in self._list_ranges:
            self._list_ranges[indices] = _range(len(self.lists), len(indices))
            self.lists.extend(fmt.TypeEntry(fmt.Ref(index)) for index in indices)
        return self._list_ranges[indices]

    def value(self, value_type: Type, value: Any) -> None:
        """Discover type values nested in const structs, options, and lists."""
        if isinstance(value_type, TypeValueType):
            self.add(value)
        elif isinstance(value_type, TypeStruct):
            for field_type, field in zip(value_type.field_types, value):
                self.value(field_type, field)
        elif isinstance(value_type, TypeList):
            for item in value:
                self.value(value_type.inner_type, item)
        elif isinstance(value_type, TypeOption) and value is not None:
            self.value(value_type.inner_type, value.value)


def _immediate(constant: Constant) -> int | None:
    value_type = constant.value_type
    if isinstance(value_type, TypeEnum):
        value_type = value_type.underlying_type
    if not isinstance(value_type, (TypeInt, TypeBoolean)):
        return None
    value = int(value_type.verify_value(constant.value))
    if isinstance(value_type, TypeInt) and value_type.is_signed:
        value = zigzag(value)
    return value if 0 <= value <= 0xffffffff else None


def _instruction_immediates(instruction: Instruction) -> dict[int, int]:
    opcode = fmt.Opcode[instruction.opcode.name]
    result = {}
    for position in immediate_positions(opcode, len(instruction.operands)):
        operand = instruction.operands[position]
        if isinstance(operand, ConstOperand):
            value = _immediate(operand.constant)
            if value is not None:
                result[position] = value
                if opcode in (fmt.Opcode.EQUAL, fmt.Opcode.LESS):
                    break
    return result


class _Writer:
    def __init__(self, program: Program) -> None:
        self.program = program
        self.names = _Buffer()
        self.types = _Types(self.names)
        self.procedures = [proc for stage in RunStage for proc in program.procs.get(stage, ())]
        self.bodies: dict[int, ProcedureBody] = {}
        used: dict[int, Constant] = {}
        retained: dict[int, Constant] = {}
        self.immediates = {}
        # Bindings precede instructions within each scheduled procedure. Only
        # uses that remain table references establish the constant-table order;
        # decoded immediates need not share their original Constant identity.
        for procedure in self.procedures:
            for source in procedure.inputs:
                if source.constant is not None:
                    used.setdefault(id(source.constant), source.constant)
                    retained.setdefault(id(source.constant), source.constant)
            body = procedure.body
            if body is None or id(body) in self.bodies:
                continue
            self.bodies[id(body)] = body
            for instruction in body.instructions:
                immediates = _instruction_immediates(instruction)
                self.immediates[id(instruction)] = immediates
                for position, operand in enumerate(instruction.operands):
                    constant = operand.constant if isinstance(operand, ConstOperand) else operand
                    if isinstance(constant, Constant):
                        used.setdefault(id(constant), constant)
                        if position not in immediates:
                            retained.setdefault(id(constant), constant)
        self.constants = list(retained.values())
        self.constant_indices = {id(constant): index for index, constant in enumerate(self.constants)}
        self.function_indices = {function.name: index for index, function in enumerate(program.functions)}
        self.native_indices = {native.name: index for index, native in enumerate(program.native_procedures)}
        self.body_indices = {identity: index for index, identity in enumerate(self.bodies)}
        self.variable_indices = {}
        for table, banks in ((program.fixed_values, (VariableBank.FIXED_VALUES,)),
                             (program.temporaries, (VariableBank.TEMPORARIES,)),
                             (program.state_variables, (VariableBank.OLD_STATE, VariableBank.NEW_STATE))):
            indices = {var: index for index, var in enumerate(table.offsets)}
            for bank in banks:
                self.variable_indices[bank] = indices
            for var in table.offsets:
                self.types.add(var.var_type)
        for constant in used.values():
            self.types.add(constant.value_type)
            self.types.value(constant.value_type, constant.value)
        for function in program.functions:
            self.types.type_list(function.parameter_types)
            self.types.add(function.return_type)
        for body in self.bodies.values():
            self.types.type_list(body.local_types)

    def code(self, body: ProcedureBody) -> bytes:
        chunks = []
        # Distances from the stream's end avoid a fixed-point calculation for
        # branch sizes: every target has already been encoded.
        distances: dict[int, int] = {}
        length = 0
        for index in reversed(range(len(body.instructions))):
            instruction = body.instructions[index]
            data = bytearray([fmt.Opcode[instruction.opcode.name]])
            immediates = self.immediates[id(instruction)]
            for position, operand in enumerate(instruction.operands):
                if position in immediates:
                    data += encode_value_operand(fmt.OperandKind.Immediate, immediates[position])
                elif isinstance(operand, ConstOperand):
                    data += encode_value_operand(fmt.OperandKind.Const, self.constant_indices[id(operand.constant)])
                elif isinstance(operand, Operand):
                    data += encode_value_operand(fmt.OperandKind[operand.kind.name.title()], operand.index)
                elif isinstance(operand, BranchTarget):
                    data += encode_integer_parameter(length - distances[operand.index])
                elif isinstance(operand, Function):
                    data += encode_integer_parameter(self.function_indices[operand.name])
                elif isinstance(operand, Constant):
                    data += encode_integer_parameter(self.constant_indices[id(operand)])
                else:
                    data += encode_integer_parameter(operand)
            chunks.append(bytes(data))
            length += len(data)
            distances[index] = length
        return b''.join(reversed(chunks))

    def binding(self, binding: InputSource | OutputTarget) -> fmt.VariableRef:
        if isinstance(binding, InputSource) and binding.constant is not None:
            index = self.constant_indices[id(binding.constant)]
        else:
            assert binding.variable is not None
            index = self.variable_indices[binding.bank][binding.variable]
        bank = fmt.VariableBank[''.join(part.title() for part in binding.bank.name.split('_'))]
        return fmt.VariableRef(bank, fmt.Ref(index))

    def variables(self, table: VariableTable) -> list[fmt.Variable]:
        return [fmt.Variable(fmt.Ref(self.types.add(var.var_type)), offset) for var, offset in table.offsets.items()]

    def sections(self) -> list[Section]:
        sections: list[Section] = []

        def buffer(section: fmt.SectionType, data: bytes | bytearray) -> None:
            sections.append(Section(section, fmt.RecordType.Buffer, bytes(data)))

        def table(section: fmt.SectionType, record_type: fmt.RecordType, records: Iterable[tuple[Any, ...]]) -> None:
            sections.append(Section(section, record_type, b''.join(pack_record(record) for record in records)))

        buffer(fmt.SectionType.BuildIDSHA512, bytes(64))
        const_data = ConstDataWriter(self.types.add)
        table(fmt.SectionType.ConstValues, fmt.RecordType.Variable,
              (fmt.Variable(fmt.Ref(self.types.add(const.value_type)), const_data.add(const.value_type, const.value))
               for const in self.constants))
        buffer(fmt.SectionType.ProgramData, const_data.data)
        for section, variables in ((fmt.SectionType.FixedValues, self.program.fixed_values),
                                   (fmt.SectionType.Temporaries, self.program.temporaries),
                                   (fmt.SectionType.StateVariables, self.program.state_variables)):
            table(section, fmt.RecordType.Variable, self.variables(variables))
        initial = bytearray(self.program.state_variables.size)
        for var, offset in self.program.state_variables.offsets.items():
            if var not in self.program.initial_state:
                raise ValueError(f'State variable `{var}` has no initial value')
            data = var.var_type.serialize(self.program.initial_state[var])
            initial[offset:offset + len(data)] = data
        buffer(fmt.SectionType.InitialState, initial)

        interface_names: list[fmt.NameRef] = []

        def interfaces(names: tuple[str, ...]) -> fmt.Range:
            result = _range(len(interface_names), len(names))
            interface_names.extend(fmt.NameRef(self.names.string(name)) for name in names)
            return result

        table(fmt.SectionType.NativeProcedures, fmt.RecordType.NativeProcedure,
              (fmt.NativeProcedure(self.names.string(native.name), interfaces(native.input_names),
                                   interfaces(native.output_names), native.initializer)
               for native in self.program.native_procedures))
        table(fmt.SectionType.InterfaceNames, fmt.RecordType.NameRef, interface_names)
        table(fmt.SectionType.NativeFunctions, fmt.RecordType.NativeFunction,
              (fmt.NativeFunction(self.names.string(function.name), self.types.type_list(function.parameter_types),
                                  fmt.Ref(self.types.add(function.return_type))) for function in self.program.functions))
        code = bytearray()
        bytecode = []
        for body in self.bodies.values():
            data = self.code(body)
            bytecode.append(fmt.BytecodeProcedure(fmt.ByteRange(len(code), len(data)), self.types.type_list(body.local_types)))
            code += data
        table(fmt.SectionType.BytecodeProcedures, fmt.RecordType.BytecodeProcedure, bytecode)
        buffer(fmt.SectionType.ProcedureCode, code)
        bindings: list[fmt.VariableRef] = []

        def bind(items: tuple[InputSource, ...] | tuple[OutputTarget, ...]) -> fmt.Range:
            result = _range(len(bindings), len(items))
            bindings.extend(self.binding(item) for item in items)
            return result

        instances: list[fmt.ProcedureInstance] = []
        stages = []
        for stage in RunStage:
            schedule = self.program.procs.get(stage, ())
            stages.append(fmt.Stage(fmt.RunStage[stage.name.title()], _range(len(instances), len(schedule))))
            for proc in schedule:
                instances.append(fmt.ProcedureInstance(
                    fmt.OptionalRef(self.body_indices[id(proc.body)] if proc.body is not None else 0xffffffff),
                    fmt.OptionalRef(self.native_indices[proc.native.name] if proc.native is not None else 0xffffffff),
                    bind(proc.inputs), bind(proc.outputs)))
        table(fmt.SectionType.Stages, fmt.RecordType.Stage, stages)
        table(fmt.SectionType.ProcedureInstances, fmt.RecordType.ProcedureInstance, instances)
        table(fmt.SectionType.Bindings, fmt.RecordType.VariableRef, bindings)

        json_data = _Buffer()
        json_names: list[fmt.JsonName] = []
        json_types = []
        for index, value_type in enumerate(self.types.types):
            numbers_offset = 0xffffffff
            if isinstance(value_type, TypeEnum):
                numbers = sorted(value_type.json_names)
                if not value_type.is_open and len(numbers) != value_type.member_count:
                    continue  # Unnamed closed enums have no JSON representation.
                names = tuple(value_type.json_names[number] for number in numbers)
                if value_type.is_open:
                    underlying = value_type.underlying_type
                    numbers_offset = json_data.add(b''.join(underlying.serialize(number) for number in numbers),
                                                   underlying.alignment)
            elif isinstance(value_type, TypeStruct) and value_type.json_names is not None:
                names = value_type.json_names
            else:
                continue
            first = len(json_names) if names else 0
            json_names.extend(fmt.JsonName(json_data.string(name)) for name in names)
            json_types.append(fmt.JsonType(fmt.Ref(index), len(names), fmt.Ref(first), numbers_offset))
        table(fmt.SectionType.JsonTypes, fmt.RecordType.JsonType, json_types)
        table(fmt.SectionType.JsonNames, fmt.RecordType.JsonName, json_names)
        buffer(fmt.SectionType.JsonData, json_data.data)
        sections.append(Section(fmt.SectionType.ValueTypes, fmt.RecordType.TypeRecord, b''.join(self.types.records)))
        table(fmt.SectionType.TypeLists, fmt.RecordType.TypeEntry, self.types.lists)
        buffer(fmt.SectionType.NativeNames, self.names.data)
        return sections


def write_program(program: Program) -> bytes:
    """Encode a validated program, without symbols, including its SHA-512 build ID."""
    validate_program(program)
    sections = _Writer(program).sections()
    container = SectionList(sections, (0, 0))
    digest = sha512(container.to_bytes()).digest()
    sections[0] = Section(fmt.SectionType.BuildIDSHA512, fmt.RecordType.Buffer, digest)
    return container.to_bytes()
