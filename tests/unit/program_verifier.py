"""Validity rules on file bytes, independently of loading and native code."""

import random
import struct
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from wirely import types
from wirely.program import (
    Constant, InputSource, NativeProcedureEntry, Opcode,
    OutputTarget, Procedure, ProcedureBody, RunStage, Section, SectionList, Variable, VariableBank,
    pack_record, record_size,
)
from wirely.program.generated import format as f
from wirely.program.loader import load_program
from wirely.program.verifier import VerificationError, VerificationLimitError, verify_program
from wirely.program.writer import write_program
from tests.unit.program_writer import body_program, const, instruction, local, program, records
from tests.unit.program_loader import change_section, replace_data, state_program


checks = TestCase()


def reject(data, message=None):
    with checks.assertRaisesRegex(VerificationError, message or '.'):
        verify_program(data)


def table_change(data, section, record_class, index, **fields):
    def update(section):
        entries = records(section.data, record_class)
        entries[index] = entries[index]._replace(**fields)
        return replace(section, data=b''.join(map(pack_record, entries)))
    return change_section(data, section, update)


def code_image(code, *, local_types=(types.boolean,)):
    data = write_program(body_program((instruction(Opcode.RETURN),), local_types))
    data = replace_data(data, f.SectionType.ProcedureCode, code)
    return table_change(data, f.SectionType.BytecodeProcedures, f.BytecodeProcedure, 0,
                        code=f.ByteRange(0, len(code)))


def bound_constants(*constants):
    proc = Procedure('test.read', tuple(InputSource(VariableBank.CONST_VALUES, constant=value) for value in constants), (),
                     native=NativeProcedureEntry('Test:read', tuple(f'value{index:08}' for index in range(len(constants))), ()))
    return write_program(program(procs=((RunStage.LOAD, (proc,)),)))


def check_structure():
    data = write_program(program())
    verify_program(data)
    verify_program(bytearray(data))
    verify_program(memoryview(data))
    for offset, fmt, value in ((0, '<I', 0), (4, '<H', 1), (8, '<I', len(data) + 1),
                                (12, '<I', 0xffffffff), (16, '<I', 1), (24, '<I', 0)):
        damaged = bytearray(data)
        struct.pack_into(fmt, damaged, offset, value)
        reject(damaged)
    for section in f.SectionType:
        reject(change_section(data, section, lambda entry: replace(entry, record_type=0xffffffff)), 'record type')
    container = SectionList.from_bytes(data)
    container.sections.pop()
    reject(container.to_bytes(), 'Missing section')
    reject(replace_data(data, f.SectionType.Stages, b'x'), 'truncated record')
    stages = records(next(section.data for section in SectionList.from_bytes(data).sections
                          if section.section_type == f.SectionType.Stages), f.Stage)
    stages[0] = stages[0]._replace(stage=f.RunStage(0))
    reject(replace_data(data, f.SectionType.Stages, b''.join(map(pack_record, stages))), 'Stages')
    reject(table_change(data, f.SectionType.Stages, f.Stage, 0, schedule=f.Range(f.Ref(1), 0)), 'Empty')
    # Generated record padding is checked even when no procedure uses the record.
    damaged = bytearray(pack_record(f.VariableRef(f.VariableBank.ConstValues, f.Ref(0))))
    damaged[1] = 1
    reject(replace_data(data, f.SectionType.Bindings, bytes(damaged)), 'padding|Padding')


def check_types():
    data = write_program(program())

    def type_records(*records_):
        encoded = b''.join(pack_record(record) + bytes(32 - record_size(type(record))) for record in records_)
        return replace_data(data, f.SectionType.ValueTypes, encoded)

    reject(type_records(f.SimpleType(f.TypeKind(0))), 'Unknown type kind')
    reject(type_records(f.IntegerType(f.TypeKind.Integer, False, 24)), 'integer width')
    reject(type_records(f.CompositeType(f.TypeKind.Option, f.Ref(0))), 'ValueTypes index')
    reject(type_records(f.CompositeType(f.TypeKind.List, f.Ref(1)), f.SimpleType(f.TypeKind.Unit)), 'ValueTypes index')
    reject(type_records(f.SimpleType(f.TypeKind.Boolean),
                        f.OpenEnumType(f.TypeKind.OpenEnum, f.Ref(0), f.OptionalStringRef(0xffffffff, 0xffffffff))), 'underlying')
    for count in (0, 257):
        reject(type_records(f.IntegerType(f.TypeKind.Integer, False, 8),
                            f.ClosedEnumType(f.TypeKind.ClosedEnum, f.Ref(0), count,
                                             f.OptionalStringRef(0xffffffff, 0xffffffff))), 'closed enum')
    reject(type_records(f.StructType(f.TypeKind.Struct, True, f.Range(f.Ref(0), 0),
                                     f.OptionalStringRef(0xffffffff, 0xffffffff))), 'native name')
    for kind in (f.TypeKind.Option, f.TypeKind.List):
        reject(type_records(f.ResourceType(f.TypeKind.Resource, f.StringRef(0, 0)),
                            f.CompositeType(kind, f.Ref(0))), 'resources')
    resource_data = type_records(f.ResourceType(f.TypeKind.Resource, f.StringRef(0, 0)),
                                 f.StructType(f.TypeKind.Struct, False, f.Range(f.Ref(0), 1),
                                              f.OptionalStringRef(0xffffffff, 0xffffffff)))
    reject(replace_data(resource_data, f.SectionType.TypeLists, pack_record(f.TypeEntry(f.Ref(0)))), 'resources')
    reject(replace_data(data, f.SectionType.TypeLists, pack_record(f.TypeEntry(f.Ref(9)))), 'ValueTypes index')
    padded = bytearray(pack_record(f.SimpleType(f.TypeKind.Unit)) + bytes(31))
    padded[-1] = 1
    reject(replace_data(data, f.SectionType.ValueTypes, bytes(padded)), 'Padding')
    # Const-only composites are valid types, even when they exceed runtime limits.
    wide = types.TypeStruct('Wide', {'value': types.TypeList(types.uint8)})
    verify_program(bound_constants(Constant(wide, ((1, 2, 3),))))
    nested = types.uint8
    for _ in range(17):
        nested = types.TypeOption(nested)
    deep = bound_constants(Constant(nested, None))
    verify_program(deep)
    slot = records(next(section.data for section in SectionList.from_bytes(deep).sections
                        if section.section_type == f.SectionType.ConstValues), f.Variable)[0]
    reject(replace_data(deep, f.SectionType.Temporaries, pack_record(f.Variable(slot.value_type, 0))), 'storage type')
    reject(replace_data(deep, f.SectionType.NativeFunctions,
                        pack_record(f.NativeFunction(f.StringRef(0, 1), f.Range(f.Ref(0), 0), slot.value_type))), 'runtime type')
    # Repeated fields can expand a small type graph past uint32 storage sizes.
    rows = [f.IntegerType(f.TypeKind.Integer, False, 128)]
    edges = []
    for index in range(28):
        edges.extend([f.TypeEntry(f.Ref(index))] * 2)
        rows.append(f.StructType(f.TypeKind.Struct, False, f.Range(f.Ref(2 * index), 2),
                                  f.OptionalStringRef(0xffffffff, 0xffffffff)))
    huge = replace_data(type_records(*rows), f.SectionType.TypeLists, b''.join(map(pack_record, edges)))
    reject(huge, 'uint32')


def check_values():
    mode = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    for value_type, value, invalid in ((types.boolean, True, b'\x02'), (mode, 1, b'\x02'),
                                       (types.TypeOption(types.uint8), None, b'\x01\x00'),
                                       (types.TypeOption(types.uint8), None, b'\x00\x02')):
        data = bound_constants(Constant(value_type, value))
        reject(replace_data(data, f.SectionType.ProgramData, invalid))
    data = bound_constants(Constant(types.ValueType, types.uint8))
    reject(replace_data(data, f.SectionType.ProgramData, struct.pack('<I', 999)), 'ValueTypes index')
    data = bound_constants(Constant(types.String, 'a'))
    reject(change_section(data, f.SectionType.ProgramData,
                          lambda section: replace(section, data=b'\xff' + section.data[1:])), 'UTF-8')
    data = bound_constants(Constant(types.TypeList(types.uint16), (1, 2)))
    slot = records(next(section.data for section in SectionList.from_bytes(data).sections
                        if section.section_type == f.SectionType.ConstValues), f.Variable)[0].offset
    def misalign(section):
        raw = bytearray(section.data)
        struct.pack_into('<I', raw, slot, 1)
        return replace(section, data=bytes(raw))
    reject(change_section(data, f.SectionType.ProgramData, misalign), 'aligned')
    scalar = bound_constants(Constant(types.uint16, 3))
    reject(table_change(scalar, f.SectionType.ConstValues, f.Variable, 0, offset=1), 'aligned')
    # Reused tail padding is valid in nested options and final struct fields.
    pair = types.TypeStruct('Pair', {'large': types.uint64, 'small': types.uint8})
    nested = types.TypeOption(types.TypeOption(pair))
    for value in (None, types.Some(None), types.Some(types.Some((7, 1)))):
        data = bound_constants(Constant(nested, value))
        verify_program(data)
        assert write_program(load_program(data)) == data
    variable = Variable('state', types.TypeOption(types.uint64))
    data = write_program(state_program((variable,), {variable: types.Some(9)}))
    reject(change_section(data, f.SectionType.InitialState,
                          lambda section: replace(section, data=section.data[:-1] + b'\x01')), 'Padding')
    reject(replace_data(data, f.SectionType.InitialState, b''), 'bounds|size')
    reject(table_change(data, f.SectionType.StateVariables, f.Variable, 0, offset=1), 'offset')


def check_names_and_json():
    mode = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    data = bound_constants(Constant(mode, 1))
    reject(table_change(data, f.SectionType.JsonTypes, f.JsonType, 0, numbers=0), 'sentinel')
    reject(table_change(data, f.SectionType.JsonTypes, f.JsonType, 0, count_=1), 'count')
    reject(table_change(data, f.SectionType.JsonNames, f.JsonName, 0, name=f.StringRef(999, 1)), 'bounds')
    reject(table_change(data, f.SectionType.NativeProcedures, f.NativeProcedure, 0,
                        input_names=f.Range(f.Ref(0), 0)), 'binding counts')
    reject(table_change(data, f.SectionType.NativeProcedures, f.NativeProcedure, 0,
                        input_names=f.Range(f.Ref(99), 1)), 'InterfaceNames range')
    json_record = next(section.data for section in SectionList.from_bytes(data).sections if section.section_type == f.SectionType.JsonTypes)
    reject(replace_data(data, f.SectionType.JsonTypes, json_record * 2), 'strictly sorted')
    interfaces = bound_constants(Constant(types.uint8, 1), Constant(types.uint8, 2))
    names = records(next(section.data for section in SectionList.from_bytes(interfaces).sections
                         if section.section_type == f.SectionType.InterfaceNames), f.NameRef)
    reject(replace_data(interfaces, f.SectionType.InterfaceNames, b''.join(map(pack_record, reversed(names)))), 'InterfaceNames range')
    opened = types.TypeEnum('Numbers', {'Low': -3, 'High': 7}, is_open=True, underlying_type=types.int16)
    data = bound_constants(Constant(opened, -1))
    json_type = records(next(section.data for section in SectionList.from_bytes(data).sections
                             if section.section_type == f.SectionType.JsonTypes), f.JsonType)[0]
    reject(table_change(data, f.SectionType.JsonTypes, f.JsonType, 0, numbers=json_type.numbers + 1), 'alignment')
    def unordered(section):
        raw = bytearray(section.data)
        struct.pack_into('<hh', raw, json_type.numbers, 7, -3)
        return replace(section, data=bytes(raw))
    reject(change_section(data, f.SectionType.JsonData, unordered), 'ascending')
    # Sort order and uniqueness also apply to unused native records.
    base = write_program(program())
    base = replace_data(base, f.SectionType.NativeNames, b'ZA')
    functions = [f.NativeFunction(f.StringRef(index, 1), f.Range(f.Ref(0), 0), f.Ref(0)) for index in (0, 1)]
    base = replace_data(base, f.SectionType.ValueTypes, pack_record(f.SimpleType(f.TypeKind.Unit)) + bytes(31))
    reject(replace_data(base, f.SectionType.NativeFunctions, b''.join(map(pack_record, functions))), 'sorted')
    functions[1] = functions[0]
    reject(replace_data(base, f.SectionType.NativeFunctions, b''.join(map(pack_record, functions))), 'unique')


def check_assignment():
    value = Variable('value', types.boolean)
    reject(write_program(program(temporaries=(value,))), 'no output assignment')
    reject(write_program(program(state=(value,), initial={value: False})), 'no output assignment')
    native = NativeProcedureEntry('Test:write', (), ('value',))
    write = Procedure('write.run', (), (OutputTarget(VariableBank.TEMPORARIES, value),), native=native)
    data = write_program(program(temporaries=(value,), procs=((RunStage.DEFAULT, (write, write)),)))
    reject(data, 'more than once')
    read = Procedure('read.run', (InputSource(VariableBank.TEMPORARIES, value),), (),
                     native=NativeProcedureEntry('Test:read', ('value',), ()))
    reject(write_program(program(temporaries=(value,), procs=((RunStage.DEFAULT, (read, write)),))), 'before assignment')
    verify_program(write_program(program(temporaries=(value,), procs=((RunStage.DEFAULT, (write, read)),))))
    write_state = replace(write, outputs=(OutputTarget(VariableBank.NEW_STATE, value),))
    read_old = replace(read, inputs=(InputSource(VariableBank.OLD_STATE, value),))
    verify_program(write_program(program(state=(value,), initial={value: False},
                                         procs=((RunStage.DEFAULT, (read_old, write_state)),))))
    read_new = replace(read, inputs=(InputSource(VariableBank.NEW_STATE, value),))
    reject(write_program(program(state=(value,), initial={value: False},
                                  procs=((RunStage.DEFAULT, (read_new, write_state)),))), 'before assignment')
    valid = write_program(program(temporaries=(value,), procs=((RunStage.DEFAULT, (write,)),)))
    reject(table_change(valid, f.SectionType.Temporaries, f.Variable, 0, offset=0xffffffff), 'uint32')
    reject(table_change(valid, f.SectionType.Bindings, f.VariableRef, 0, bank=f.VariableBank.OldState), 'index|bank')
    reject(table_change(valid, f.SectionType.NativeProcedures, f.NativeProcedure, 0, initializer=True), 'Initializers')
    reject(table_change(valid, f.SectionType.ProcedureInstances, f.ProcedureInstance, 0,
                        bytecode=f.OptionalRef(0)), 'exactly one')


def check_code():
    for code, message in ((b'\x02\x5d\x00\x22', 'Noncanonical'),
                          (b'\x1f\xfe\x00\x00\x22', 'Noncanonical'),
                          (b'\xff', 'Unknown opcode'), (b'\x02\xe0\x22', 'Unknown operand kind'),
                          (b'\x02\x00\x22', 'Immediate is not allowed'),
                          (b'\x02\x60\x22', 'Destination'),
                          (b'\x02\x41\x22', 'LOCAL index'),
                          (b'\x1f\x01\x22', 'Branch target'),
                          (b'\x02\x40', 'end in RETURN'),
                          (b'\x20\x02\x00\x22', 'Boolean immediate'),
                          (b'\x1d\x40\x00\x00\x22', 'typed counterpart')):
        reject(code_image(code), message)
    for code in (b'\x22', b'\x20\x01\x00\x22', b'\x1f\x00\x22', b'\x22\x02\x40\x22'):
        verify_program(code_image(code))
    reject(code_image(b'\x01\x40\x41\x22', local_types=(types.uint8, types.boolean)), 'type indices')
    # Unreachable instructions and unreferenced bodies still need canonical encodings.
    reject(code_image(b'\x22\x02\x5d\x00\x22'), 'Noncanonical')
    base = write_program(program())
    base = replace_data(base, f.SectionType.ProcedureCode, b'\xff')
    base = replace_data(base, f.SectionType.BytecodeProcedures,
                        pack_record(f.BytecodeProcedure(f.ByteRange(0, 1), f.Range(f.Ref(0), 0))))
    reject(base, 'Unknown opcode')
    # With no instance, input and output bounds have no contextual type information.
    base = replace_data(base, f.SectionType.ProcedureCode, b'\x01\x80\x60\x22')
    base = table_change(base, f.SectionType.BytecodeProcedures, f.BytecodeProcedure, 0, code=f.ByteRange(0, 4))
    verify_program(base)
    # Equal-looking type records retain distinct identities in the file.
    data = bound_constants(Constant(types.uint8, 7))
    original_types = next(section.data for section in SectionList.from_bytes(data).sections if section.section_type == f.SectionType.ValueTypes)
    data = replace_data(data, f.SectionType.ValueTypes, original_types * 2)
    data = replace_data(data, f.SectionType.TypeLists, pack_record(f.TypeEntry(f.Ref(1))))
    data = replace_data(data, f.SectionType.ProcedureCode, b'\x01\x40\x20\x22')
    data = replace_data(data, f.SectionType.BytecodeProcedures,
                        pack_record(f.BytecodeProcedure(f.ByteRange(0, 4), f.Range(f.Ref(0), 1))))
    data = table_change(data, f.SectionType.ProcedureInstances, f.ProcedureInstance, 0,
                        bytecode=f.OptionalRef(0), native=f.OptionalRef(0xffffffff), inputs=f.Range(f.Ref(0), 0))
    reject(data, 'type indices')


def check_fail():
    format_constant = Constant(types.TypeList(types.String), ('value: ', '', ''))
    data = write_program(body_program((instruction(Opcode.FAIL, format_constant, 1, const(types.uint8, 3)),), stage=RunStage.LOAD))
    verify_program(data)
    # Moving the instance to a runtime stage changes validity even though the body is unchanged.
    stages = [f.Stage(stage, f.Range(f.Ref(0), int(stage == f.RunStage.Default))) for stage in f.RunStage]
    reject(replace_data(data, f.SectionType.Stages, b''.join(map(pack_record, stages))), 'FAIL is only')
    original_code = next(section.data for section in SectionList.from_bytes(data).sections
                         if section.section_type == f.SectionType.ProcedureCode)
    unreachable = replace_data(data, f.SectionType.ProcedureCode, b'\x22' + original_code)
    unreachable = table_change(unreachable, f.SectionType.BytecodeProcedures, f.BytecodeProcedure, 0,
                               code=f.ByteRange(0, len(original_code) + 1))
    reject(replace_data(unreachable, f.SectionType.Stages, b''.join(map(pack_record, stages))), 'FAIL is only')
    reject(code_image(b'\x23\x00\x00'), 'ConstValues index')
    bad = Constant(types.TypeList(types.String), ('value: ', 'x', ''))
    # Keep the valid stream and substitute the malformed format payload.
    payload = next(section.data for section in SectionList.from_bytes(bound_constants(bad)).sections
                   if section.section_type == f.SectionType.ProgramData)
    bad_slot = records(next(section.data for section in SectionList.from_bytes(bound_constants(bad)).sections
                            if section.section_type == f.SectionType.ConstValues), f.Variable)[0]
    malformed = replace_data(data, f.SectionType.ProgramData, payload)
    const_records = records(next(section.data for section in SectionList.from_bytes(data).sections
                                 if section.section_type == f.SectionType.ConstValues), f.Variable)
    const_records[0] = const_records[0]._replace(offset=bad_slot.offset)
    # The FAIL value is still a const operand, so give it a valid separate slot.
    payload += b'\x03'
    const_records[1] = const_records[1]._replace(offset=len(payload) - 1)
    malformed = replace_data(malformed, f.SectionType.ProgramData, payload)
    malformed = replace_data(malformed, f.SectionType.ConstValues, b''.join(map(pack_record, const_records)))
    reject(malformed, 'specification')


def check_limits_and_independence():
    data = write_program(program())
    with checks.assertRaises(ValueError):
        verify_program(data, max_steps=-1)
    with checks.assertRaises(VerificationLimitError):
        verify_program(data, max_steps=1)
    units = bound_constants(Constant(types.TypeList(types.unit), ()))
    units = replace_data(units, f.SectionType.ProgramData, struct.pack('<II', 0, 0xffffffff))
    with checks.assertRaises(VerificationLimitError):
        verify_program(units)
    # Equal payloads share storage but each reference still requires bounded work.
    shared = bound_constants(*(Constant(types.TypeList(types.unit), (types.Unit(),) * 256) for _ in range(20)))
    verify_program(shared)
    with checks.assertRaises(VerificationLimitError):
        verify_program(shared, max_steps=5000)
    # A small shared stream can have many distinct instance type contexts.
    body = ProcedureBody((instruction(Opcode.SET_DEFAULT, local(0)),) * 1000
                         + (instruction(Opcode.RETURN),), (types.boolean,))
    procedures = []
    for index in range(50):
        enum = types.TypeEnum(f'E{index}', {'Zero': 0}, is_open=False, underlying_type=types.uint8)
        procedures.append(Procedure(f'p{index}:run', (InputSource(VariableBank.CONST_VALUES, constant=Constant(enum, 0)),), (), body=body))
    contexts = write_program(program(procs=((RunStage.DEFAULT, tuple(procedures)),)))
    verify_program(contexts)
    with checks.assertRaises(VerificationLimitError):
        verify_program(contexts, max_steps=50_000)
    with patch('wirely.program.loader.resolve_extensions', side_effect=AssertionError('must verify first')):
        with patch('wirely.program.loader._Loader', side_effect=AssertionError('must not decode')):
            verify_program(data)
            reject(b'bad')
            with checks.assertRaises(VerificationError):
                load_program(b'bad')
            with checks.assertRaises(VerificationLimitError):
                load_program(data, max_verification_steps=0)
    opaque = types.TypeStruct('Opaque', {'value': types.uint128}, native_name='Unknown.Opaque', is_opaque=True)
    verify_program(bound_constants(Constant(opaque, ((1 << 128) - 1,))))


def check_ignored_bytes():
    data = write_program(program())
    container = SectionList.from_bytes(data)
    container.sections.append(Section(0xf0000, 0xffffffff, b'\xff'))
    verify_program(container.to_bytes())
    for section in (f.SectionType.ProgramData, f.SectionType.ProcedureCode, f.SectionType.JsonData, f.SectionType.NativeNames):
        verify_program(replace_data(data, section, b'\xff\xff'))
    verify_program(replace_data(data, f.SectionType.BuildIDSHA512, b'\xff' * 64))
    # An unknown section may overlap the header: non-overlap is producer-only.
    overlap = bytearray(container.to_bytes())
    last_entry = record_size(f.Header) + (len(container.sections) - 1) * record_size(f.SectionEntry)
    struct.pack_into('<II', overlap, last_entry, 0, 16)
    verify_program(overlap)
    # JSON-name distinctness is also a producer responsibility.
    enum = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    image = bound_constants(Constant(enum, 1))
    names = records(next(section.data for section in SectionList.from_bytes(image).sections
                         if section.section_type == f.SectionType.JsonNames), f.JsonName)
    verify_program(replace_data(image, f.SectionType.JsonNames, pack_record(names[0]) * 2))
    # Variable offsets need not follow the producer's decreasing-alignment order.
    flag, number = Variable('flag', types.boolean), Variable('number', types.uint64)
    image = write_program(state_program((flag, number), {flag: True, number: 7}))
    state = records(next(section.data for section in SectionList.from_bytes(image).sections
                         if section.section_type == f.SectionType.StateVariables), f.Variable)
    image = replace_data(image, f.SectionType.StateVariables,
                         pack_record(state[1]._replace(offset=0)) + pack_record(state[0]._replace(offset=8)))
    image = replace_data(image, f.SectionType.InitialState, struct.pack('<?7xQ', True, 7))
    def remap(section):
        return replace(section, data=b''.join(pack_record(row._replace(variable=f.Ref(1 - row.variable.value)))
                                              for row in records(section.data, f.VariableRef)))
    verify_program(change_section(image, f.SectionType.Bindings, remap))


def check_random_values():
    rng = random.Random(42)
    cases = [(types.uint64, 7), (types.boolean, True)]
    for index in range(30):
        inner, value = rng.choice(cases)
        if index % 3 == 0:
            cases.append((types.TypeOption(inner), types.Some(value)))
        elif index % 3 == 1:
            cases.append((types.TypeList(inner), (value, value)))
        else:
            cases.append((types.TypeStruct(f'S{index}', {'value': inner, 'flag': types.boolean}), (value, False)))
    verify_program(bound_constants(*(Constant(value_type, value) for value_type, value in cases)))


async def run_test():
    check_structure()
    check_types()
    check_values()
    check_names_and_json()
    check_assignment()
    check_code()
    check_fail()
    check_limits_and_independence()
    check_ignored_bytes()
    check_random_values()
