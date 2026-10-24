"""The binary writer's tables, instruction encodings, and CLI."""

import argparse
import contextlib
import hashlib
import io
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from wirely import types
from wirely.compiler import compile_files, compile_strs
from wirely.compiler import program as program_compiler
from wirely.cli import compile_command
from wirely.program import (
    BranchTarget, Constant, ConstOperand, Function, InputSource, Instruction,
    NativeProcedureEntry, Opcode, Operand, OperandKind, OutputTarget, Procedure,
    ProcedureBody, Program, ProgramSymbols, RunStage, SectionList, Variable,
    VariableBank, VariableTable, record_size, unpack_record,
)
from wirely.program.const_data import read_const
from wirely.program.encoding import encode_integer_parameter, encode_value_operand, zigzag
from wirely.program.generated import format as fmt
from wirely.program.writer import write_program


checks = TestCase()
root = Path(__file__).resolve().parents[2]


def instruction(opcode, *operands):
    return Instruction(opcode, operands)


def local(index):
    return Operand(OperandKind.LOCAL, index)


def const(value_type, value):
    return ConstOperand(Constant(value_type, value))


def program(*, procs=(), state=(), temporaries=(), fixed=(), initial=None, aliases=None):
    return Program(state_variables=VariableTable(state, 'state'),
                   temporaries=VariableTable(temporaries, 'temporaries'),
                   fixed_values=VariableTable(fixed, 'fixed'),
                   initial_state={} if initial is None else initial, procs=dict(procs),
                   symbols=ProgramSymbols(variable_aliases={} if aliases is None else aliases))


def body_program(instructions, local_types=(), stage=RunStage.DEFAULT):
    proc = Procedure('test:run', (), (), body=ProcedureBody(instructions, local_types))
    return program(procs=((stage, (proc,)),))


def sections(prog):
    return {fmt.SectionType(section.section_type): section.data
            for section in SectionList.from_bytes(write_program(prog)).sections}


def records(data, record_class):
    return [unpack_record(record_class, data, offset)
            for offset in range(0, len(data), record_size(record_class))]


def text(buffer, ref):
    return buffer[ref.offset:ref.offset + ref.length_bytes].decode('utf-8')


def check_helpers():
    for index, expected in ((0, b'\x00'), (28, b'\x1c'), (29, b'\x1d\x1d'),
                            (255, b'\x1d\xff'), (256, b'\x1e\x00\x01'),
                            (65535, b'\x1e\xff\xff'), (65536, b'\x1f\x00\x00\x01\x00'),
                            (0xffffffff, b'\x1f\xff\xff\xff\xff')):
        assert encode_value_operand(fmt.OperandKind.Immediate, index) == expected
        assert encode_value_operand(fmt.OperandKind.Output, index) == bytes([expected[0] | 0x80]) + expected[1:]
    for value, expected in ((0, b'\x00'), (253, b'\xfd'), (254, b'\xfe\xfe\x00'),
                            (65535, b'\xfe\xff\xff'), (65536, b'\xff\x00\x00\x01\x00'),
                            (0xffffffff, b'\xff\xff\xff\xff\xff')):
        assert encode_integer_parameter(value) == expected
    for value in (-1, 1 << 32):
        with checks.assertRaises(ValueError):
            encode_integer_parameter(value)
        with checks.assertRaises(ValueError):
            encode_value_operand(fmt.OperandKind.Const, value)
    with checks.assertRaises(ValueError):
        encode_value_operand(fmt.OperandKind(5), 0)
    assert [zigzag(value) for value in (0, -1, 1, -2, 2)] == [0, 1, 2, 3, 4]


def check_container():
    data = write_program(program())
    container = SectionList.from_bytes(data)
    assert container.version == (0, 0)
    assert [section.section_type for section in container.sections] == list(fmt.SectionType)
    expected_records = [0, 0, 1, 2, 0, 3, 3, 3, 3, 0, 4, 0, 5, 6, 7, 8, 9, 10, 0, 11, 12]
    assert [section.record_type for section in container.sections] == expected_records
    offset = unpack_record(fmt.SectionEntry, data, record_size(fmt.Header)).offset
    zeroed = data[:offset] + bytes(64) + data[offset + 64:]
    assert container.sections[0].data == hashlib.sha512(zeroed).digest()
    stage_data = next(section.data for section in container.sections if section.section_type == fmt.SectionType.Stages)
    assert records(stage_data, fmt.Stage) == [fmt.Stage(stage, fmt.Range(fmt.Ref(0), 0)) for stage in fmt.RunStage]
    assert all(section.length == 0 for section in container.sections
               if section.section_type not in (fmt.SectionType.BuildIDSHA512, fmt.SectionType.Stages))


def check_types_and_constants():
    option = types.TypeOption(types.uint8)
    pair = types.TypeStruct('Pair', {'one': option, 'two': types.TypeOption(types.uint8)})
    twin = types.TypeStruct('Pair', {'one': option, 'two': option})
    closed = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False,
                            underlying_type=types.uint8, native_name='Test.Mode')
    opened = types.TypeEnum('Signed', {'High': 300, 'Low': -3}, is_open=True,
                            underlying_type=types.int16, json_names={300: 'high', -3: 'low'})
    resource = types.TypeResource('Resource', 'Test.Resource')
    typed = types.TypeList(types.ValueType)
    constants = [Constant(pair, (types.Some(1), None)), Constant(twin, (None, None)),
                 Constant(closed, 1), Constant(opened, -3), Constant(typed, (resource, option)),
                 Constant(types.String, 'héllo'), Constant(types.String, 'héllo')]
    inputs = tuple(InputSource(VariableBank.CONST_VALUES, constant=item) for item in constants)
    proc = Procedure('test:run', inputs, (), native=NativeProcedureEntry('Test:run', tuple('abcdefg'), ()))
    data = sections(program(procs=((RunStage.LOAD, (proc,)),)))
    type_data = data[fmt.SectionType.ValueTypes]
    kinds = list(type_data[::32])
    assert kinds == [3, 10, 8, 8, 6, 3, 7, 5, 11, 9, 4]
    option_record = unpack_record(fmt.CompositeType, type_data, 32)
    assert option_record == fmt.CompositeType(fmt.TypeKind.Option, fmt.Ref(0))
    first_pair = unpack_record(fmt.StructType, type_data, 64)
    second_pair = unpack_record(fmt.StructType, type_data, 96)
    assert first_pair.fields == second_pair.fields == fmt.Range(fmt.Ref(0), 2)
    assert records(data[fmt.SectionType.TypeLists], fmt.TypeEntry) == [fmt.TypeEntry(fmt.Ref(1))] * 2
    assert unpack_record(fmt.ClosedEnumType, type_data, 128).underlying_type == fmt.Ref(0)
    assert unpack_record(fmt.OpenEnumType, type_data, 192).underlying_type == fmt.Ref(5)
    names = data[fmt.SectionType.NativeNames]
    assert text(names, unpack_record(fmt.ClosedEnumType, type_data, 128).native_name) == 'Test.Mode'
    assert text(names, unpack_record(fmt.ResourceType, type_data, 288).native_name) == 'Test.Resource'
    type_table = [types.uint8, option, pair, twin, closed, types.int16, opened, types.ValueType,
                  typed, resource, types.String]
    slots = records(data[fmt.SectionType.ConstValues], fmt.Variable)
    assert len(slots) == len(constants)
    assert slots[-1] == slots[-2]  # Separate constant identities, shared payload storage.
    for record, constant in zip(slots, constants):
        assert read_const(data[fmt.SectionType.ProgramData], record.offset,
                          type_table[record.value_type.value], type_table.__getitem__) == constant.value
    json_types = records(data[fmt.SectionType.JsonTypes], fmt.JsonType)
    assert [record.type.value for record in json_types] == [2, 3, 4, 6]
    json_names = records(data[fmt.SectionType.JsonNames], fmt.JsonName)
    json_data = data[fmt.SectionType.JsonData]
    assert [text(json_data, record.name) for record in json_names] == ['one', 'two', 'one', 'two', 'Off', 'On', 'low', 'high']
    assert json_names[0] == json_names[2]
    numbers = json_types[-1].numbers
    assert numbers % 2 == 0 and struct.unpack_from('<hh', json_data, numbers) == (-3, 300)
    assert all(record.numbers == 0xffffffff for record in json_types[:-1])
    # Every record has zero padding through the end of its 32-byte entry.
    layouts = {fmt.TypeKind.Integer: fmt.IntegerType, fmt.TypeKind.Option: fmt.CompositeType,
               fmt.TypeKind.Struct: fmt.StructType, fmt.TypeKind.ClosedEnum: fmt.ClosedEnumType,
               fmt.TypeKind.OpenEnum: fmt.OpenEnumType, fmt.TypeKind.Type: fmt.SimpleType,
               fmt.TypeKind.List: fmt.CompositeType, fmt.TypeKind.Resource: fmt.ResourceType,
               fmt.TypeKind.String: fmt.SimpleType}
    for index, kind in enumerate(kinds):
        assert not any(type_data[index * 32 + record_size(layouts[kind]):(index + 1) * 32])


def check_variables_and_procedures():
    small = Variable('small', types.uint8)
    wide = Variable('wide', types.uint64)
    empty = Variable('empty', types.unit)
    temporary = Variable('temporary', types.uint8)
    fixed = Variable('fixed', types.uint8)
    resource = Variable('resource', types.TypeResource('Resource', 'Test.Resource'))
    init = Procedure('z:create', (), (OutputTarget(VariableBank.FIXED_VALUES, resource),),
                     native=NativeProcedureEntry('Z:create', (), ('resource',), initializer=True))
    setup = Procedure('a:load', (), (OutputTarget(VariableBank.FIXED_VALUES, fixed),),
                      native=NativeProcedureEntry('A:load', (), ('fixed',)))
    sources = (InputSource(VariableBank.CONST_VALUES, constant=Constant(types.uint8, 7)),
               InputSource(VariableBank.FIXED_VALUES, fixed), InputSource(VariableBank.FIXED_VALUES, resource),
               InputSource(VariableBank.OLD_STATE, wide))
    outputs = (OutputTarget(VariableBank.TEMPORARIES, temporary), OutputTarget(VariableBank.NEW_STATE, small),
               OutputTarget(VariableBank.NEW_STATE, wide), OutputTarget(VariableBank.NEW_STATE, empty))
    native = Procedure('b:run', sources, outputs,
                       native=NativeProcedureEntry('B:run', ('a', 'b', 'c', 'd'), ('a', 'b', 'c', 'd')))
    shared = ProcedureBody((instruction(Opcode.RETURN),))
    first = Procedure('first:run', (), (), body=shared)
    second = Procedure('second:run', (), (), body=shared)
    distinct = Procedure('third:run', (), (), body=ProcedureBody(shared.instructions))
    last = Procedure('last:run', (InputSource(VariableBank.TEMPORARIES, temporary),
                                  InputSource(VariableBank.NEW_STATE, small)), (),
                     native=NativeProcedureEntry('C:run', ('a', 'b'), ()))
    prog = program(procs=((RunStage.LOAD, (init, setup)),
                          (RunStage.DEFAULT, (native, first, second, distinct, last))),
                   state=(small, wide, empty), temporaries=(temporary,), fixed=(fixed, resource),
                   initial={small: 9, wide: 0x12345678, empty: types.Unit()})
    data = sections(prog)
    for section, table in ((fmt.SectionType.StateVariables, prog.state_variables),
                           (fmt.SectionType.FixedValues, prog.fixed_values),
                           (fmt.SectionType.Temporaries, prog.temporaries)):
        assert [record.offset for record in records(data[section], fmt.Variable)] == list(table.offsets.values())
    expected = bytearray(prog.state_variables.size)
    for variable, offset in prog.state_variables.offsets.items():
        value = variable.var_type.serialize(prog.initial_state[variable])
        expected[offset:offset + len(value)] = value
    assert data[fmt.SectionType.InitialState] == expected
    assert [record.schedule for record in records(data[fmt.SectionType.Stages], fmt.Stage)] == [
        fmt.Range(fmt.Ref(0), 2), fmt.Range(fmt.Ref(0), 0), fmt.Range(fmt.Ref(2), 5), fmt.Range(fmt.Ref(0), 0)]
    instances = records(data[fmt.SectionType.ProcedureInstances], fmt.ProcedureInstance)
    assert [record.bytecode.value for record in instances] == [0xffffffff] * 3 + [0, 0, 1, 0xffffffff]
    assert len(records(data[fmt.SectionType.BytecodeProcedures], fmt.BytecodeProcedure)) == 2
    natives = records(data[fmt.SectionType.NativeProcedures], fmt.NativeProcedure)
    assert [text(data[fmt.SectionType.NativeNames], native.name) for native in natives] == ['A:load', 'B:run', 'C:run', 'Z:create']
    assert [native.initializer for native in natives] == [False, False, False, True]
    interfaces = records(data[fmt.SectionType.InterfaceNames], fmt.NameRef)
    for native, original in zip(natives, prog.native_procedures):
        for interval, names in ((native.input_names, original.input_names), (native.output_names, original.output_names)):
            assert [text(data[fmt.SectionType.NativeNames], entry.name)
                    for entry in interfaces[interval.first.value:interval.first.value + interval.count_]] == list(names)
    bindings = records(data[fmt.SectionType.Bindings], fmt.VariableRef)
    assert {record.bank for record in bindings} == set(fmt.VariableBank)
    assert bindings[instances[2].inputs.first.value].variable == fmt.Ref(0)
    with checks.assertRaisesRegex(ValueError, 'no initial value'):
        write_program(program(state=(small,)))


def check_nested_type_values():
    first = types.TypeEnum('Same', {'Zero': 0}, is_open=False, underlying_type=types.uint8)
    second = types.TypeEnum('Same', {'Zero': 0}, is_open=False, underlying_type=types.uint8)
    wrapper = types.TypeStruct('Types', {'values': types.TypeOption(types.TypeList(types.ValueType))})
    opaque = types.TypeStruct('Native', {'ticks': types.uint64}, native_name='Test.Native', is_opaque=True)
    unnamed = types.TypeStruct(None, (types.boolean,))
    constants = [Constant(wrapper, (types.Some((types.TypeOption(first), types.TypeOption(second))),)),
                 Constant(opaque, (7,)), Constant(unnamed, (True,)), Constant(types.String, '')]
    proc = Procedure('test:load', tuple(InputSource(VariableBank.CONST_VALUES, constant=item) for item in constants), (),
                     native=NativeProcedureEntry('Test:load', ('a', 'b', 'c', 'd'), ()))
    data = sections(program(procs=((RunStage.LOAD, (proc,)),)))
    type_data = data[fmt.SectionType.ValueTypes]
    enum_indices = [index for index, kind in enumerate(type_data[::32]) if kind == fmt.TypeKind.ClosedEnum]
    assert len(enum_indices) == 2
    option_indices = []
    for index, kind in enumerate(type_data[::32]):
        if kind == fmt.TypeKind.Option:
            inner = unpack_record(fmt.CompositeType, type_data, index * 32).inner_type.value
            assert inner < index
            if inner in enum_indices:
                option_indices.append(index)
    assert len(option_indices) == 2
    slots = records(data[fmt.SectionType.ConstValues], fmt.Variable)
    # The nested type values refer to their distinct option records.
    wrapper_slot = slots[0].offset
    list_offset, count = struct.unpack_from('<II', data[fmt.SectionType.ProgramData], wrapper_slot)
    assert count == 2
    assert list(struct.unpack_from('<II', data[fmt.SectionType.ProgramData], list_offset)) == option_indices
    opaque_index = slots[1].value_type.value
    opaque_record = unpack_record(fmt.StructType, type_data, opaque_index * 32)
    assert opaque_record.opaque and text(data[fmt.SectionType.NativeNames], opaque_record.native_name) == 'Test.Native'
    json_indices = [record.type.value for record in records(data[fmt.SectionType.JsonTypes], fmt.JsonType)]
    assert opaque_index in json_indices and slots[2].value_type.value not in json_indices
    assert struct.unpack_from('<II', data[fmt.SectionType.ProgramData], slots[3].offset) == (0, 0)


def check_code():
    enum = types.TypeEnum('Signed', {}, is_open=True, underlying_type=types.int16)
    closed = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    instructions = (
        instruction(Opcode.COPY, local(0), const(types.int32, -3)),
        instruction(Opcode.EQUAL, local(1), const(types.int32, 1), const(types.int32, 2)),
        instruction(Opcode.LESS, local(1), const(types.int32, 3), const(types.int32, 4)),
        instruction(Opcode.SHIFT_LEFT, local(0), const(types.int32, -1), const(types.uint32, 2)),
        instruction(Opcode.CONVERT_WRAPPING, local(0), const(types.int32, 9)),
        instruction(Opcode.COPY, local(2), const(types.uint64, 1 << 32)),
        instruction(Opcode.COPY, local(3), const(enum, -2)),
        instruction(Opcode.COPY, local(4), const(closed, 1)),
        instruction(Opcode.JUMP_IF_TRUE, const(types.boolean, True), BranchTarget(10)),
        instruction(Opcode.JUMP_IF_FALSE, const(types.boolean, False), BranchTarget(10)),
        instruction(Opcode.RETURN),
    )
    prog = body_program(instructions, (types.int32, types.boolean, types.uint64, enum, closed))
    data = sections(prog)
    assert data[fmt.SectionType.ProcedureCode] == bytes([
        1, 0x40, 5, 29, 0x41, 2, 0x20, 30, 0x41, 6, 0x21,
        12, 0x40, 1, 2, 25, 0x40, 0x22, 1, 0x42, 0x23,
        1, 0x43, 3, 1, 0x44, 1, 32, 1, 3, 33, 0, 0, 34,
    ])
    assert len(records(data[fmt.SectionType.ConstValues], fmt.Variable)) == 4
    # Operand index forms in a real stream, including an extended uint32.
    indices = (28, 29, 255, 256, 65535, 65536)
    prog = body_program(tuple(instruction(Opcode.SET_DEFAULT, local(index)) for index in indices)
                        + (instruction(Opcode.RETURN),), (types.unit,) * 65537)
    assert sections(prog)[fmt.SectionType.ProcedureCode] == bytes([
        2, 0x5c, 2, 0x5d, 29, 2, 0x5d, 255, 2, 0x5e, 0, 1,
        2, 0x5e, 255, 255, 2, 0x5f, 0, 0, 1, 0, 34])
    for count, prefix in ((126, b'\x1f\xfc'), (127, b'\x1f\xfe\xfe\x00')):
        prog = body_program((instruction(Opcode.JUMP, BranchTarget(count + 1)),)
                            + (instruction(Opcode.SET_DEFAULT, local(0)),) * count
                            + (instruction(Opcode.RETURN),), (types.unit,))
        assert sections(prog)[fmt.SectionType.ProcedureCode] == prefix + b'\x02\x40' * count + b'\x22'
    # Function indices are sorted independently of first use; argument immediates follow the signature.
    zed = Function('Z', (types.uint8,), types.uint8)
    alpha = Function('A', (types.uint8,), types.uint8)
    prog = body_program((instruction(Opcode.CALL, local(0), zed, 1, const(types.uint8, 9)),
                         instruction(Opcode.CALL, local(0), alpha, 1, const(types.uint8, 8)),
                         instruction(Opcode.RETURN)), (types.uint8,))
    data = sections(prog)
    assert data[fmt.SectionType.ProcedureCode] == bytes([28, 0x40, 1, 1, 9, 28, 0x40, 0, 1, 8, 34])
    functions = records(data[fmt.SectionType.NativeFunctions], fmt.NativeFunction)
    assert [text(data[fmt.SectionType.NativeNames], function.name) for function in functions] == ['A', 'Z']
    assert functions[0].parameters == functions[1].parameters
    # FAIL's format is an integer parameter; its values retain their own types.
    format_constant = Constant(types.TypeList(types.String), ('value: ', '', ''))
    prog = body_program((instruction(Opcode.FAIL, format_constant, 1, const(types.uint8, 3)),), stage=RunStage.LOAD)
    data = sections(prog)
    assert data[fmt.SectionType.ProcedureCode] == bytes([35, 0, 1, 0x21])
    assert len(records(data[fmt.SectionType.ConstValues], fmt.Variable)) == 2


def check_scalar_immediates():
    instructions = (
        instruction(Opcode.NOT, local(0), const(types.boolean, True)),
        instruction(Opcode.AND, local(0), const(types.boolean, True), const(types.boolean, False)),
        instruction(Opcode.OR, local(0), const(types.boolean, False), const(types.boolean, True)),
        instruction(Opcode.XOR, local(0), const(types.boolean, True), const(types.boolean, True)),
        instruction(Opcode.ADD_WRAPPING, local(1), const(types.uint8, 3), const(types.uint8, 4)),
        instruction(Opcode.MAKE_SOME, local(2), const(types.uint8, 5)),
        instruction(Opcode.SET_FIELD, local(3), const(types.uint8, 6), 0),
        instruction(Opcode.COPY, local(4), const(types.int64, -(1 << 31))),
        instruction(Opcode.COPY, local(4), const(types.int64, -(1 << 31) - 1)),
        instruction(Opcode.RETURN),
    )
    prog = body_program(instructions, (types.boolean, types.uint8, types.TypeOption(types.uint8),
                                       types.TypeStruct('S', {'value': types.uint8}), types.int64))
    data = sections(prog)
    assert data[fmt.SectionType.ProcedureCode] == bytes([
        8, 0x40, 1, 9, 0x40, 1, 0, 10, 0x40, 0, 1, 11, 0x40, 1, 1,
        14, 0x41, 3, 4, 7, 0x42, 5, 4, 0x43, 6, 0,
        1, 0x44, 0x1f, 255, 255, 255, 255, 1, 0x44, 0x20, 34])
    slots = records(data[fmt.SectionType.ConstValues], fmt.Variable)
    assert len(slots) == 1
    assert read_const(data[fmt.SectionType.ProgramData], slots[0].offset, types.int64,
                      lambda index: types.int64) == -(1 << 31) - 1


def check_aliases():
    unused_type = types.TypeEnum('Unused', {'Zero': 0}, is_open=False, underlying_type=types.uint16)
    alias = Constant(unused_type, 0)
    immediate = Constant(types.int32, 7)
    proc = Procedure('test:run', (), (), body=ProcedureBody((
        instruction(Opcode.COPY, local(0), ConstOperand(immediate)), instruction(Opcode.RETURN)), (types.int32,)))
    data = sections(program(procs=((RunStage.DEFAULT, (proc,)),), aliases={
        'alias': InputSource(VariableBank.CONST_VALUES, constant=alias),
        'immediate': InputSource(VariableBank.CONST_VALUES, constant=immediate)}))
    assert not data[fmt.SectionType.ConstValues]
    assert len(data[fmt.SectionType.ValueTypes]) == 32
    bound = Procedure('bound:run', (InputSource(VariableBank.CONST_VALUES, constant=immediate),), (),
                      native=NativeProcedureEntry('Bound:run', ('input',), ()))
    data = sections(program(procs=((RunStage.DEFAULT, (proc, bound)),)))
    assert len(records(data[fmt.SectionType.ConstValues], fmt.Variable)) == 1
    assert data[fmt.SectionType.ProcedureCode] == bytes([1, 0x40, 14, 34])


def check_constant_order():
    def make(separate_immediate):
        small = Constant(types.uint64, 1)
        large = Constant(types.uint64, 1 << 40)
        distinct = Constant(types.uint64, 1)
        immediate = Constant(types.uint64, 1) if separate_immediate else small
        return body_program((
            instruction(Opcode.COPY, local(0), ConstOperand(immediate)),
            instruction(Opcode.COPY, local(0), ConstOperand(large)),
            instruction(Opcode.CONVERT_WRAPPING, local(0), ConstOperand(small)),
            instruction(Opcode.CONVERT_WRAPPING, local(0), ConstOperand(distinct)),
            instruction(Opcode.CONVERT_WRAPPING, local(0), ConstOperand(small)),
            instruction(Opcode.RETURN),
        ), (types.uint64,))

    original, reconstructed = make(False), make(True)
    original_constants = original.constants
    assert [constant.value for constant in original_constants] == [1, 1 << 40, 1]
    assert write_program(original) == write_program(reconstructed)
    assert original.constants is original_constants
    data = sections(original)
    slots = records(data[fmt.SectionType.ConstValues], fmt.Variable)
    assert [read_const(data[fmt.SectionType.ProgramData], slot.offset, types.uint64,
                       lambda index: types.uint64) for slot in slots] == [1 << 40, 1, 1]
    assert slots[1] == slots[2]  # Distinct identities keep entries while sharing their payload.
    assert data[fmt.SectionType.ProcedureCode] == bytes([
        1, 0x40, 1, 1, 0x40, 0x20, 25, 0x40, 0x21, 25, 0x40, 0x22, 25, 0x40, 0x21, 34])


def check_stage_order():
    format_constant = Constant(types.TypeList(types.String), ('value: ', '', ''))
    fail_value = Constant(types.uint16, 3)
    load = Procedure('load:run', (), (), body=ProcedureBody((
        instruction(Opcode.FAIL, format_constant, 1, ConstOperand(fail_value)),)))
    bound = Constant(types.uint64, 4)
    converted = Constant(types.uint64, 5)
    body = ProcedureBody((instruction(Opcode.CONVERT_WRAPPING, local(0), ConstOperand(converted)),
                          instruction(Opcode.RETURN)), (types.uint64,))
    first = Procedure('first:run', (InputSource(VariableBank.CONST_VALUES, constant=bound),), (), body=body)
    following = Constant(types.uint64, 6)
    second = Procedure('second:run', (InputSource(VariableBank.CONST_VALUES, constant=following),), (),
                       native=NativeProcedureEntry('Test:run', ('input',), ()))
    nested = types.TypeOption(types.int8)
    type_constant = Constant(types.ValueType, nested)
    output = Procedure('output:run', (InputSource(VariableBank.CONST_VALUES, constant=type_constant),), (),
                       native=second.native)
    stages = ((RunStage.LOAD, (load,)), (RunStage.INPUT, (first,)),
              (RunStage.DEFAULT, (second,)), (RunStage.OUTPUT, (output,)))
    forward, reverse = program(procs=stages), program(procs=reversed(stages))
    assert forward.constants[0] is format_constant and reverse.constants[0] is type_constant
    assert write_program(forward) == write_program(reverse)
    data = sections(reverse)
    slots = records(data[fmt.SectionType.ConstValues], fmt.Variable)
    expected = (format_constant, fail_value, bound, converted, following, type_constant)
    assert len(slots) == len(expected)
    for slot, constant in zip(slots, expected):
        assert read_const(data[fmt.SectionType.ProgramData], slot.offset, constant.value_type,
                          lambda index: nested) == constant.value
    # Type-valued constants also discover their dependencies in stage order.
    reference = unpack_record(fmt.ValueTypeValue, data[fmt.SectionType.ProgramData], slots[-1].offset)
    option = unpack_record(fmt.CompositeType, data[fmt.SectionType.ValueTypes], reference.value_type.value * 32)
    assert option.kind == fmt.TypeKind.Option and option.inner_type.value < reference.value_type.value
    inner = unpack_record(fmt.IntegerType, data[fmt.SectionType.ValueTypes], option.inner_type.value * 32)
    assert inner.signed and inner.bits == 8


def check_compiled_programs():
    source = ('node x: Std.DebugValueSource { Type: boolean }\n'
              'node y: Std.TwoStageValueForward { Type: boolean }\nconnect x -> y;')

    def compile_source():
        graph = compile_strs([('<writer>', source)], add_std=True, shuffle_seed=None)
        assert graph is not None
        prog = program_compiler.compile(graph)
        assert prog is not None
        return prog

    assert write_program(compile_source()) == write_program(compile_source())
    for path in sorted((root / 'examples').glob('*.wrl')):
        graph = compile_files([path])
        assert graph is not None, path
        prog = program_compiler.compile(graph)
        assert prog is not None, path
        assert len(SectionList.from_bytes(write_program(prog)).sections) == 21, path


def check_cli():
    def run(*args):
        return subprocess.run([sys.executable, '-m', 'wirely', 'compile', *map(str, args)],
                              cwd=root, capture_output=True, text=True)

    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        source = directory / 'source.wrl'
        source.write_text('', encoding='utf-8')
        output = directory / 'program.wbc'
        result = run('--output', output, source)
        assert result.returncode == 0, result.stderr
        assert len(SectionList.load(output).sections) == 21
        original = output.read_bytes()
        result = run('--no-create-program', '--output', output, source)
        assert result.returncode == 2 and '--no-create-program' in result.stderr
        assert output.read_bytes() == original
        source.write_text('node broken:', encoding='utf-8')
        result = run('--output', output, source)
        assert result.returncode == 1 and output.read_bytes() == original
        source.write_text('', encoding='utf-8')
        result = run('--output', directory, source)
        assert result.returncode == 1 and 'Error:' in result.stderr
        args = argparse.Namespace(input_filename=[str(source)], output=str(output),
                                  create_program=True, create_engine=False, dump_program=False, timings=False)
        error = io.StringIO()
        with patch.object(compile_command, 'write_program', side_effect=ValueError('Cannot encode program')):
            with contextlib.redirect_stderr(error):
                assert compile_command.run(args) == 1
        assert 'Cannot encode program' in error.getvalue()
        assert output.read_bytes() == original


async def run_test():
    check_helpers()
    check_container()
    check_types_and_constants()
    check_variables_and_procedures()
    check_nested_type_values()
    check_code()
    check_scalar_immediates()
    check_aliases()
    check_constant_order()
    check_stage_order()
    check_compiled_programs()
    check_cli()
