"""Binary program decoding, execution integration, and malformed input."""

import argparse
import contextlib
import io
import struct
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, mock_open, patch

from wirely import types
from wirely.cli import run as run_command
from wirely.program import (
    BranchTarget, Constant, ConstOperand, Function, InputSource, NativeProcedureEntry, Opcode, Operand, OperandKind,
    OutputTarget, Procedure, ProcedureBody, RunStage, Section, SectionList, Variable, VariableBank,
    pack_record, record_size,
)
from wirely.program.const_data import ConstDataReader, ConstDataWriter
from wirely.program.encoding import (
    decode_integer_parameter, decode_value_operand, encode_integer_parameter, encode_value_operand,
)
from wirely.program.generated import format as fmt
from wirely.program.loader import load_program, read_build_id
from wirely.program.writer import write_program
from wirely.runtime.engine import Engine
from wirely.runtime.procedures import LoadFailure
from tests.infra.bytecode import match_programs
from tests.unit.program_writer import body_program, const, instruction, local, program, records


checks = TestCase()


def change_section(data, section_type, transform):
    container = SectionList.from_bytes(data)
    container.sections = [transform(section) if section.section_type == section_type else section
                          for section in container.sections]
    return container.to_bytes()


def replace_data(data, section_type, content):
    return change_section(data, section_type, lambda section: replace(section, data=content))


def roundtrip(prog):
    data = write_program(prog)
    loaded = load_program(data)
    assert loaded.graph is None and not loaded.symbols.node_aliases
    assert write_program(loaded) == data
    match_programs(prog, loaded)
    assert all(var.name is None for table in (loaded.temporaries, loaded.fixed_values) for var in table.variables)
    return loaded


def state_program(variables, initial):
    outputs = tuple(OutputTarget(VariableBank.NEW_STATE, variable) for variable in variables)
    names = tuple(f'value{index:08}' for index in range(len(outputs)))
    proc = Procedure((), outputs, name='test.state', native=NativeProcedureEntry('Test:state', (), names))
    return program(state=variables, initial=initial, procs=((RunStage.DEFAULT, (proc,)),))


def check_helpers():
    for kind in fmt.OperandKind:
        for index in (0, 28, 29, 255, 256, 65535, 65536, 0xffffffff):
            data = encode_value_operand(kind, index)
            assert decode_value_operand(b'x' + data, 1) == (kind, index, 1 + len(data))
            for end in range(len(data)):
                with checks.assertRaisesRegex(ValueError, 'Truncated'):
                    decode_value_operand(data[:end])
    for index in (0, 253, 254, 65535, 65536, 0xffffffff):
        data = encode_integer_parameter(index)
        assert decode_integer_parameter(data) == (index, len(data))
        for end in range(len(data)):
            with checks.assertRaisesRegex(ValueError, 'Truncated'):
                decode_integer_parameter(data[:end])
    with checks.assertRaisesRegex(ValueError, 'Unknown operand kind'):
        decode_value_operand(b'\xe0')


def check_constants_and_bindings():
    mode = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    other_mode = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    option = types.TypeOption(mode)
    wrapper = types.TypeStruct('Types', {'values': types.TypeOption(types.TypeList(types.ValueType))})
    value = Constant(wrapper, (types.Some((option, types.TypeOption(other_mode), option)),))
    opaque = types.TypeStruct('Native', {'ticks': types.uint64}, native_name='Test.Native', is_opaque=True)
    constants = (value, Constant(opaque, (7,)), Constant(types.String, 'héllo'), Constant(types.String, ''))
    fixed = Variable(types.uint8)
    resource = Variable(types.TypeResource('Resource', 'Test.Resource'))
    state = Variable(types.uint8)
    temporary = Variable(types.uint8)
    init = Procedure((), (OutputTarget(VariableBank.FIXED_VALUES, resource),), name='init',
                     native=NativeProcedureEntry('Test:init', (), ('resource',), initializer=True))
    setup = Procedure((), (OutputTarget(VariableBank.FIXED_VALUES, fixed),), name='setup',
                      native=NativeProcedureEntry('Test:setup', (), ('fixed',)))
    inputs = (InputSource(VariableBank.FIXED_VALUES, resource), InputSource(VariableBank.FIXED_VALUES, fixed),
              InputSource(VariableBank.OLD_STATE, state))
    run = Procedure(inputs, (OutputTarget(VariableBank.NEW_STATE, state),
                                    OutputTarget(VariableBank.TEMPORARIES, temporary)), name='run',
                    native=NativeProcedureEntry('Test:run', ('resource', 'fixed', 'old'), ('state', 'temporary')))
    consume = Procedure((InputSource(VariableBank.NEW_STATE, state),
                                    InputSource(VariableBank.TEMPORARIES, temporary)) + tuple(
                                        InputSource(VariableBank.CONST_VALUES, constant=item) for item in constants), (), name='consume',
                        native=NativeProcedureEntry('Test:consume', ('new', 'temporary', 'types', 'opaque', 'text', 'empty'), ()))
    loaded = roundtrip(program(state=(state,), initial={state: 9}, fixed=(fixed, resource), temporaries=(temporary,),
                               procs=((RunStage.LOAD, (init, setup)), (RunStage.INPUT, (run,)),
                                      (RunStage.OUTPUT, (consume,)))))
    loaded_init, loaded_setup = loaded.procs[RunStage.LOAD]
    assert loaded_init.native.initializer and not loaded_setup.native.initializer
    loaded_run, = loaded.procs[RunStage.INPUT]
    assert loaded_run.native == run.native
    assert loaded_run.inputs[0].variable.var_type.native_name == 'Test.Resource'
    assert loaded_run.inputs[1].variable is loaded_setup.outputs[0].variable
    assert loaded_run.inputs[2].variable is loaded_run.outputs[0].variable
    assert loaded.initial_state[loaded_run.outputs[0].variable] == 9
    loaded_consume, = loaded.procs[RunStage.OUTPUT]
    assert loaded_consume.inputs[0].variable is loaded_run.outputs[0].variable
    assert loaded_consume.inputs[1].variable is loaded_run.outputs[1].variable
    nested, opaque_value, text, empty = (source.constant for source in loaded_consume.inputs[2:])
    first, second, repeated = nested.value[0].value
    assert first is repeated and first is not second and first.inner_type is not second.inner_type
    assert first.inner_type.member_count == second.inner_type.member_count == 2
    assert opaque_value.value_type.is_opaque and opaque_value.value_type.native_name == 'Test.Native'
    assert opaque_value.value == (7,) and text.value == 'héllo' and empty.value == ''


def check_code():
    signed = types.TypeEnum('Signed', {}, is_open=True, underlying_type=types.int16)
    closed = types.TypeEnum('Closed', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    function = Function('Test:function', (types.uint8,), types.uint8)
    instructions = (
        instruction(Opcode.COPY, local(0), const(types.int32, -3)),
        instruction(Opcode.EQUAL, local(1), const(types.int32, 1), const(types.int32, 2)),
        instruction(Opcode.LESS, local(1), const(types.int32, 3), const(types.int32, 4)),
        instruction(Opcode.SHIFT_LEFT, local(0), const(types.int32, -1), const(types.uint32, 2)),
        instruction(Opcode.CONVERT_WRAPPING, local(0), const(types.uint64, 9)),
        instruction(Opcode.COPY, local(2), const(types.uint64, 1 << 40)),
        instruction(Opcode.COPY, local(3), const(signed, -2)),
        instruction(Opcode.COPY, local(4), const(closed, 1)),
        instruction(Opcode.CALL, local(5), function, 1, const(types.uint8, 7)),
        instruction(Opcode.NOT, local(1), const(types.boolean, True)),
        instruction(Opcode.MAKE_SOME, local(6), const(types.uint8, 5)),
        instruction(Opcode.SET_FIELD, local(7), const(types.uint8, 6), 0),
        instruction(Opcode.JUMP_IF_TRUE, const(types.boolean, True), BranchTarget(14)),
        instruction(Opcode.JUMP_IF_FALSE, const(types.boolean, False), BranchTarget(14)),
        instruction(Opcode.RETURN),
    )
    local_types = (types.int32, types.boolean, types.uint64, signed, closed, types.uint8,
                   types.TypeOption(types.uint8), types.TypeStruct('S', {'value': types.uint8}))
    loaded = roundtrip(body_program(instructions, local_types))
    body = loaded.procs[RunStage.DEFAULT][0].body
    assert [item.opcode for item in body.instructions] == [item.opcode for item in instructions]
    assert body.instructions[0].operands[1].constant.value == -3
    assert body.instructions[4].operands[1].constant.value_type is types.uint64
    assert body.instructions[5].operands[1].constant.value == 1 << 40
    assert body.instructions[6].operands[1].constant.value_type is body.local_types[3]
    assert body.instructions[7].operands[1].constant.value_type is body.local_types[4]
    assert body.instructions[8].operands[1] == function
    assert body.instructions[9].operands[1].constant.value is True
    assert body.instructions[12].operands[1] == BranchTarget(14)
    shared = Constant(types.uint64, 1)
    distinct = Constant(types.uint64, 1)
    code = (
        instruction(Opcode.CONVERT_WRAPPING, local(0), const(types.int64, -(1 << 31))),
        instruction(Opcode.CONVERT_WRAPPING, local(0), const(types.int64, -(1 << 31) - 1)),
        instruction(Opcode.CONVERT_WRAPPING, local(0), ConstOperand(shared)),
        instruction(Opcode.CONVERT_WRAPPING, local(0), ConstOperand(distinct)),
        instruction(Opcode.CONVERT_WRAPPING, local(0), ConstOperand(shared)),
        instruction(Opcode.AND, local(1), const(types.boolean, True), const(types.boolean, False)),
        instruction(Opcode.OR, local(1), const(types.boolean, False), const(types.boolean, True)),
        instruction(Opcode.XOR, local(1), const(types.boolean, True), const(types.boolean, True)),
        instruction(Opcode.ADD_WRAPPING, local(0), const(types.int64, 3), const(types.int64, 4)),
        instruction(Opcode.RETURN),
    )
    loaded = roundtrip(body_program(code, (types.int64, types.boolean)))
    operands = [item.operands[1].constant for item in loaded.procs[RunStage.DEFAULT][0].body.instructions[:5]]
    assert [item.value for item in operands] == [-(1 << 31), -(1 << 31) - 1, 1, 1, 1]
    assert operands[2] is operands[4] and operands[2] is not operands[3]
    # Extended local operands need only 257 local types.
    code = (instruction(Opcode.JUMP, BranchTarget(128)),) + tuple(
        instruction(Opcode.SET_DEFAULT, local(index)) for index in (28, 29, 255, 256)) + (
        instruction(Opcode.SET_DEFAULT, local(0)),) * 123 + (instruction(Opcode.RETURN),)
    loaded = roundtrip(body_program(code, (types.unit,) * 257))
    assert loaded.procs[RunStage.DEFAULT][0].body.instructions == code
    # The exact displacement boundary does not need a large local table.
    code = (instruction(Opcode.JUMP, BranchTarget(128)),) + (
        instruction(Opcode.SET_DEFAULT, local(0)),) * 127 + (instruction(Opcode.RETURN),)
    loaded = roundtrip(body_program(code, (types.unit,)))
    assert loaded.procs[RunStage.DEFAULT][0].body.instructions == code
    failure = Constant(types.TypeList(types.String), ('value: ', '', ''))
    loaded = roundtrip(body_program((instruction(Opcode.FAIL, failure, 1, const(types.uint8, 3)),), stage=RunStage.LOAD))
    fail = loaded.procs[RunStage.LOAD][0].body.instructions[0]
    assert fail.operands[0].value == failure.value
    assert fail.operands[2].constant.value_type is types.uint8 and fail.operands[2].constant.value == 3


def check_stripped_types_and_state():
    mode = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    signed = types.TypeEnum('Signed', {'Negative': -3}, is_open=True, underlying_type=types.int16)
    pair = types.TypeStruct('Pair', {'flag': types.boolean, 'mode': mode})
    other = types.TypeStruct('Pair', {'flag': types.boolean, 'mode': mode})
    variables = (Variable(pair), Variable(other), Variable(signed),
                 Variable(types.TypeStruct('Empty', {})))
    original = state_program(variables, dict(zip(variables, ((True, 1), (False, 0), -3, ()))))
    loaded = roundtrip(original)
    state = list(loaded.state_variables.offsets)
    assert list(loaded.initial_state.values()) == [original.initial_state[var] for var in original.state_variables.offsets]
    by_original = dict(zip(original.state_variables.offsets, state))
    by_name = {name: by_original[var] for name, var in zip(('pair', 'other', 'signed', 'empty'), variables)}
    loaded_pair = by_name['pair'].var_type
    assert loaded_pair is not by_name['other'].var_type
    assert loaded_pair.type_name == 'Pair' and loaded_pair.field_names == ('flag', 'mode')
    assert loaded_pair.json_names == ('flag', 'mode')
    loaded_mode = loaded_pair.field_types[1]
    assert loaded_pair.field_types[0] is types.boolean
    assert dict(loaded_mode.members) == {'Off': 0, 'On': 1} and loaded_mode.type_name == 'Mode'
    assert dict(loaded_mode.json_names) == {0: 'Off', 1: 'On'}
    assert loaded_mode.underlying_type is types.uint8
    assert by_name['empty'].var_type.json_names == ()


def symbol_program():
    mode = types.TypeEnum('Example.Mode', {'Off': 0, 'On': 1}, is_open=False,
                          underlying_type=types.uint8, json_names={0: 'off', 1: 'on'})
    packet = types.TypeStruct('Example.Packet', {'mode': mode, 'value': types.uint8}, json_names=('m', 'v'))
    wide = types.TypeEnum('Example.Wide', {'Negative': -2, 'Large': 1 << 90}, is_open=True,
                          underlying_type=types.int128)
    sparse = types.TypeStruct(None, (types.uint8, types.uint8, types.uint8),
                              field_names=(None, 'middle', None))
    state = Variable(packet, name='root.packet')
    temporary = Variable(types.uint8)
    resource = Variable(types.TypeResource('Service', 'Test.Service'))
    init = Procedure((), (OutputTarget(VariableBank.FIXED_VALUES, resource),), name='bus.create',
                     native=NativeProcedureEntry('Test:create', (), ('service',), initializer=True))
    run = Procedure((), (OutputTarget(VariableBank.NEW_STATE, state),
                         OutputTarget(VariableBank.TEMPORARIES, temporary)), name='root.run',
                    native=NativeProcedureEntry('Test:run', (), ('state', 'temporary')))
    aliases = {
        'old': InputSource(VariableBank.OLD_STATE, state),
        'new': InputSource(VariableBank.NEW_STATE, state),
        'temporary': InputSource(VariableBank.TEMPORARIES, temporary),
        'service': InputSource(VariableBank.FIXED_VALUES, resource),
        'wide': InputSource(VariableBank.CONST_VALUES, constant=Constant(wide, 1 << 90)),
        'open': InputSource(VariableBank.CONST_VALUES, constant=Constant(
            types.TypeEnum('Example.Open', {'Zero': 0, 'One': 1}, is_open=True, underlying_type=types.uint8), 1)),
        'é': InputSource(VariableBank.CONST_VALUES, constant=Constant(sparse, (1, 2, 3))),
    }
    return program(state=(state,), initial={state: (1, 7)}, temporaries=(temporary,), fixed=(resource,),
                   procs=((RunStage.LOAD, (init,)), (RunStage.DEFAULT, (run,))), aliases=aliases)


def check_symbols():
    original = symbol_program()
    loaded = roundtrip(original)
    aliases = loaded.symbols.variable_aliases
    state, = loaded.state_variables.variables
    assert state.name == 'root.packet' and state.var_type.type_name == 'Example.Packet'
    assert state.var_type.field_names == ('mode', 'value') and state.var_type.json_names == ('m', 'v')
    assert aliases['old'].variable is aliases['new'].variable is state
    assert aliases['old'].bank == VariableBank.OLD_STATE and aliases['new'].bank == VariableBank.NEW_STATE
    assert aliases['temporary'].variable is loaded.temporaries.variables[0]
    assert aliases['service'].variable is loaded.fixed_values.variables[0]
    assert aliases['wide'].constant.value == 1 << 90
    assert dict(aliases['wide'].value_type.members) == {'Negative': -2, 'Large': 1 << 90}
    sparse = aliases['é'].value_type
    assert sparse.type_name is None and sparse.field_names == (None, 'middle', None)
    assert sparse.index('middle') == 1 and dict(sparse.fields) == {'middle': types.uint8}
    assert sparse.to_text((1, 2, 3)) == 'struct { 0: 1, middle: 2, 2: 3 }'
    assert sparse.json_names is None
    with checks.assertRaisesRegex(ValueError, 'complete field names'):
        sparse.make_value({'middle': 2})

    data = write_program(original)
    container = SectionList.from_bytes(data)
    entries = {section.section_type: section for section in container.sections}
    type_symbols = records(entries[fmt.SectionType.TypeSymbols].data, fmt.TypeSymbol)
    assert any(record.numbers == 0xffffffff for record in type_symbols)
    assert any(record.numbers != 0xffffffff and record.count_ for record in type_symbols)
    assert read_build_id(data) == entries[fmt.SectionType.BuildIDSHA512].data

    # A retained type name need not retain any field/member names.
    name_only = [entry._replace(count_=0, names=fmt.Ref(0), numbers=0) for entry in type_symbols]
    name_only_data = replace_data(data, fmt.SectionType.TypeSymbols, b''.join(map(pack_record, name_only)))
    name_only_container = SectionList.from_bytes(name_only_data)
    name_only_container.sections = [section for section in name_only_container.sections
                                    if section.section_type != fmt.SectionType.SymbolNames]
    name_only_type = load_program(name_only_container.to_bytes()).state_variables.variables[0].var_type
    assert name_only_type.type_name == 'Example.Packet' and name_only_type.field_names is None
    assert name_only_type.json_names == ('m', 'v')

    # Closed enums retain their complete numeric range with only some names.
    value_types = entries[fmt.SectionType.ValueTypes].data
    mode_index = next(index for index in range(len(value_types) // 32)
                      if value_types[index * 32] == fmt.TypeKind.ClosedEnum)
    mode_symbol = next(entry for entry in type_symbols if entry.type.value == mode_index)
    pool = entries[fmt.SectionType.SymbolData].data
    partial_mode = mode_symbol._replace(count_=1, names=fmt.Ref(mode_symbol.names.value + 1), numbers=len(pool))
    sparse_types = [partial_mode if entry.type.value == mode_index else entry for entry in type_symbols]
    partial_data = replace_data(data, fmt.SectionType.TypeSymbols, b''.join(map(pack_record, sparse_types)))
    partial_data = replace_data(partial_data, fmt.SectionType.SymbolData, pool + b'\x01')
    partial = load_program(partial_data).state_variables.variables[0].var_type.field_types[0]
    assert partial.member_count == 2 and dict(partial.members) == {'On': 1}
    assert partial.to_text(0) == '0' and partial.parse('0') == 0
    assert partial.json_encode(0, None) == 'off'

    # Sections and individual entries can be omitted independently.
    for omitted in (fmt.SectionType.StateVariableSymbols, fmt.SectionType.ProcedureSymbols,
                    fmt.SectionType.TypeSymbols, fmt.SectionType.VariableSymbolAliases):
        partial = SectionList([section for section in container.sections if section.section_type != omitted], (0, 0))
        decoded = load_program(partial.to_bytes())
        assert read_build_id(partial.to_bytes()) == read_build_id(data)
        if omitted == fmt.SectionType.StateVariableSymbols:
            assert decoded.state_variables.variables[0].name is None
        elif omitted == fmt.SectionType.ProcedureSymbols:
            assert all(proc.name is None for schedule in decoded.procs.values() for proc in schedule)
        elif omitted == fmt.SectionType.TypeSymbols:
            assert decoded.state_variables.variables[0].var_type.type_name is None
            assert decoded.state_variables.variables[0].var_type.json_names == ('m', 'v')
        else:
            assert not decoded.symbols.variable_aliases
    initializers = replace_data(data, fmt.SectionType.ProcedureSymbols,
                               entries[fmt.SectionType.ProcedureSymbols].data[:record_size(fmt.IndexedSymbol)])
    decoded = load_program(initializers)
    assert decoded.procs[RunStage.LOAD][0].name == 'bus.create'
    assert decoded.procs[RunStage.DEFAULT][0].name is None

    stripped = SectionList([section for section in container.sections if section.section_type < 0x10000], (0, 0))
    decoded = load_program(stripped.to_bytes())
    match_programs(original, decoded, symbols=False)
    assert decoded.state_variables.variables[0].name is None
    assert not decoded.symbols.variable_aliases
    assert decoded.state_variables.variables[0].var_type.field_names is None

    # Pool extensions retain all existing references, including opaque future sections.
    extended = SectionList(container.sections + [Section(0x10006, 0, b'future symbols')], (0, 0))
    extended.sections = [replace(section, data=section.data + b'added') if section.section_type == fmt.SectionType.SymbolData
                         else replace(section, data=section.data + pack_record(fmt.SymbolName(fmt.StringRef(0, 0))))
                         if section.section_type == fmt.SectionType.SymbolNames else section for section in extended.sections]
    match_programs(original, load_program(extended.to_bytes()))
    assert read_build_id(extended.to_bytes()) == read_build_id(data)


def check_json_presence():
    opened = types.TypeEnum('Numbers', {}, is_open=True, underlying_type=types.uint8)
    variable = Variable(opened)
    image = write_program(state_program((variable,), {variable: 7}))
    present = load_program(image)
    assert next(iter(present.state_variables.offsets)).var_type.is_json_serializable
    # A zero-name entry still enables numeric JSON conversion for an open enum.
    stripped = replace_data(image, fmt.SectionType.JsonTypes, b'')
    absent = load_program(stripped)
    assert not next(iter(absent.state_variables.offsets)).var_type.is_json_serializable
    normalized = write_program(absent)
    assert write_program(load_program(normalized)) == normalized
    assert not next(section.data for section in SectionList.from_bytes(normalized).sections
                    if section.section_type == fmt.SectionType.JsonTypes)


def check_shared_bodies():
    flag, number = Variable(types.boolean), Variable(types.int8)
    first = Procedure((), (OutputTarget(VariableBank.TEMPORARIES, flag),), name='first.run', body=ProcedureBody((
        instruction(Opcode.COPY, Operand(OperandKind.OUTPUT, 0), const(types.boolean, True)), instruction(Opcode.RETURN))))
    second = Procedure((), (OutputTarget(VariableBank.TEMPORARIES, number),), name='second.run', body=ProcedureBody((
        instruction(Opcode.COPY, Operand(OperandKind.OUTPUT, 0), const(types.int8, -1)), instruction(Opcode.RETURN))))
    data = write_program(program(temporaries=(flag, number), procs=((RunStage.DEFAULT, (first, second)),)))

    def share(section):
        instances = records(section.data, fmt.ProcedureInstance)
        instances[1] = instances[1]._replace(bytecode=fmt.OptionalRef(0))
        return replace(section, data=b''.join(map(pack_record, instances)))

    loaded = load_program(change_section(data, fmt.SectionType.ProcedureInstances, share))
    left, right = loaded.procs[RunStage.DEFAULT]
    assert left.body is not right.body
    assert left.body.instructions[0].operands[1].constant.value is True
    assert right.body.instructions[0].operands[1].constant.value == -1
    machine = Engine(loaded)
    machine.step()
    assert machine.cache[left.outputs[0].variable] is True
    assert machine.cache[right.outputs[0].variable] == -1
    copy = ProcedureBody((instruction(Opcode.COPY, Operand(OperandKind.OUTPUT, 0), Operand(OperandKind.INPUT, 0)),
                          instruction(Opcode.RETURN)))
    first = replace(first, inputs=(InputSource(VariableBank.CONST_VALUES, constant=Constant(types.boolean, True)),), body=copy)
    second = replace(second, inputs=(InputSource(VariableBank.CONST_VALUES, constant=Constant(types.int8, -1)),), body=copy)
    loaded = roundtrip(program(temporaries=(flag, number), procs=((RunStage.DEFAULT, (first, second)),)))
    left, right = loaded.procs[RunStage.DEFAULT]
    assert left.body is right.body  # No immediates need specializing.


def check_budgets():
    value_type = types.TypeList(types.unit)
    writer = ConstDataWriter(lambda value_type: 0)
    offset = writer.add(value_type, (types.Unit(),) * 3)
    reader = ConstDataReader(writer.data, lambda index: types.unit, max_values=8)
    assert len(reader.read(offset, value_type)) == 3
    assert len(reader.read(offset, value_type)) == 3
    with checks.assertRaisesRegex(ValueError, 'budget'):
        reader.read(offset, value_type)
    constant = Constant(value_type, (types.Unit(),) * 3)
    proc = Procedure((InputSource(VariableBank.CONST_VALUES, constant=constant),), (), name='test.run',
                     native=NativeProcedureEntry('Test:run', ('value',), ()))
    data = write_program(program(procs=((RunStage.LOAD, (proc,)),)))
    assert load_program(data, max_decoded_values=4)
    with checks.assertRaisesRegex(ValueError, 'budget'):
        load_program(data, max_decoded_values=3)
    program_data = next(section.data for section in SectionList.from_bytes(data).sections
                        if section.section_type == fmt.SectionType.ProgramData)
    huge = bytearray(program_data)
    struct.pack_into('<I', huge, 4, 0xffffffff)
    with checks.assertRaisesRegex(ValueError, 'budget'):
        load_program(replace_data(data, fmt.SectionType.ProgramData, bytes(huge)))


def check_malformed():
    data = write_program(program())
    container = SectionList.from_bytes(data)
    container.sections = [section for section in container.sections if section.section_type != fmt.SectionType.JsonTypes]
    with checks.assertRaisesRegex(ValueError, 'Missing section JsonTypes'):
        load_program(container.to_bytes())
    with checks.assertRaisesRegex(ValueError, 'wrong record type'):
        load_program(change_section(data, fmt.SectionType.Stages, lambda section: replace(section, record_type=0)))
    with checks.assertRaisesRegex(ValueError, 'truncated record'):
        load_program(replace_data(data, fmt.SectionType.Stages, b'x'))
    unknown = SectionList.from_bytes(data)
    unknown.sections.append(Section(0xf0000, 0xffffffff, b'ignored'))
    assert write_program(load_program(unknown.to_bytes())) == data
    for code, message in ((b'\xff', 'Unknown opcode'), (b'\x01\x40', 'Truncated'),
                          (b'\x1f\x01\x22', 'Branch target'), (b'\x02\x00\x22', 'Immediate is not allowed'),
                          (b'\x20\x02\x00\x22', 'Boolean immediate'),
                          (b'\x1d\x40\x00\x00\x22', 'typed counterpart'),
                          (b'\x01\x40\x60\x22', 'INPUT index')):
        image = write_program(body_program((instruction(Opcode.RETURN),), (types.boolean,)))
        image = replace_data(image, fmt.SectionType.ProcedureCode, code)
        image = replace_data(image, fmt.SectionType.BytecodeProcedures,
                             pack_record(fmt.BytecodeProcedure(fmt.ByteRange(0, len(code)), fmt.Range(fmt.Ref(0), 1))))
        with checks.assertRaisesRegex(ValueError, message):
            load_program(image)
    image = write_program(body_program((instruction(Opcode.COPY, local(0), const(types.uint8, 1)),
                                       instruction(Opcode.RETURN)), (types.uint8,)))
    with checks.assertRaisesRegex(ValueError, 'ValueTypes index'):
        load_program(replace_data(image, fmt.SectionType.ValueTypes,
                                  pack_record(fmt.CompositeType(fmt.TypeKind.Option, fmt.Ref(100))) + bytes(24)))
    oversized = b'\x01\x40\x1e\x00\x01\x22'  # Immediate 256 does not fit uint8.
    oversized_image = replace_data(image, fmt.SectionType.ProcedureCode, oversized)
    oversized_image = replace_data(oversized_image, fmt.SectionType.BytecodeProcedures,
                                   pack_record(fmt.BytecodeProcedure(fmt.ByteRange(0, len(oversized)), fmt.Range(fmt.Ref(0), 1))))
    with checks.assertRaisesRegex(ValueError, 'instruction 0'):
        load_program(oversized_image)
    # The verifier checks a type-valued constant's reference.
    proc = Procedure((InputSource(VariableBank.CONST_VALUES, constant=Constant(types.ValueType, types.uint8)),), (), name='test.run',
                     native=NativeProcedureEntry('Test:run', ('type',), ()))
    image = write_program(program(procs=((RunStage.LOAD, (proc,)),)))
    with checks.assertRaisesRegex(ValueError, 'ValueTypes index'):
        load_program(replace_data(image, fmt.SectionType.ProgramData, struct.pack('<I', 0xffffffff)))
    with checks.assertRaisesRegex(ValueError, 'invalid UTF-8'):
        load_program(change_section(image, fmt.SectionType.NativeNames,
                                    lambda section: replace(section, data=b'\xff' * len(section.data))))
    with checks.assertRaisesRegex(ValueError, 'InterfaceNames range'):
        load_program(replace_data(image, fmt.SectionType.NativeProcedures,
                                  pack_record(fmt.NativeProcedure(fmt.StringRef(0, 8), fmt.Range(fmt.Ref(1000), 1),
                                                                   fmt.Range(fmt.Ref(0), 0), False))))


def check_native_registry():
    # Decoding needs no native implementations; linking rejects unknown opaque structs.
    opaque = types.TypeStruct('Native', {'value': types.uint8}, native_name='Custom.Native', is_opaque=True)
    variable = Variable(opaque)
    loaded = load_program(write_program(state_program((variable,), {variable: (7,)})))
    with checks.assertRaisesRegex(ValueError, 'Custom.Native'):
        Engine(loaded)


async def check_service():
    magic = SectionList.MAGIC.to_bytes(4, 'little')
    with patch('wirely.cli.run.open', mock_open(read_data=magic + b'ignored')) as opened:
        assert run_command._is_binary_program('program')
        opened().read.assert_called_once_with(4)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'program.bin'
        path.write_bytes(write_program(program()))
        source = Path(directory) / 'source.wbc'
        source.write_text('', encoding='utf-8')
        args = argparse.Namespace(input_filename=[str(path)], verbose=False, debug=False,
                                  state_directory=None, control_tls_cert=None, control_tls_key=None,
                                  commands=None)
        with patch.object(run_command.wirely.compiler, 'compile_files', side_effect=AssertionError('must not compile')):
            with patch.object(run_command, 'run_engine', new_callable=AsyncMock, return_value=None) as run:
                assert await run_command.async_run(args) is None
                run.assert_awaited_once()
            for filenames, status in (([str(path), str(source)], 2), ([str(source), str(path)], 2),
                                       ([str(path), str(path)], 2)):
                with contextlib.redirect_stderr(io.StringIO()):
                    assert await run_command.async_run(argparse.Namespace(**{**vars(args), 'input_filename': filenames})) == status
            for error in (ValueError('link failure'), LoadFailure(Procedure((), (), name='test.load'), 0, 'load failure', ())):
                with patch.object(run_command, 'Engine', side_effect=error), patch.object(run_command, 'run_engine', new_callable=AsyncMock, return_value=None) as run:
                    output = io.StringIO()
                    with contextlib.redirect_stderr(output):
                        assert await run_command.async_run(args) == 1
                    assert str(error) in output.getvalue()
                    run.assert_not_awaited()
            path.write_bytes(magic)
            with patch.object(run_command, 'Engine') as engine, contextlib.redirect_stderr(io.StringIO()):
                assert await run_command.async_run(args) == 1
                engine.assert_not_called()
            path.unlink()
            with patch.object(run_command, 'Engine') as engine, contextlib.redirect_stderr(io.StringIO()):
                assert await run_command.async_run(args) == 1
                engine.assert_not_called()
            failure = Constant(types.TypeList(types.String), ('intentional failure',))
            path.write_bytes(write_program(body_program((instruction(Opcode.FAIL, failure, 0),), stage=RunStage.LOAD)))
            with patch.object(run_command, 'run_engine', new_callable=AsyncMock, return_value=None) as run:
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    assert await run_command.async_run(args) == 1
                assert 'intentional failure' in output.getvalue()
                run.assert_not_awaited()
        args.input_filename = [str(source)]
        with patch.object(run_command, 'load_program', side_effect=AssertionError('must compile source')):
            with patch.object(run_command, 'run_engine', new_callable=AsyncMock, return_value=None) as run:
                assert await run_command.async_run(args) is None
                run.assert_awaited_once()
        for prefix in (b'', magic[:1], magic[:2], magic[:3]):
            source.write_bytes(prefix)
            with patch.object(run_command.wirely.compiler, 'compile_files', return_value=None) as compile_files:
                with patch.object(run_command, 'load_program') as load:
                    assert await run_command.async_run(args) == 1
                    compile_files.assert_called_once_with([str(source)])
                    load.assert_not_called()


async def run_test():
    check_helpers()
    check_constants_and_bindings()
    check_code()
    check_stripped_types_and_state()
    check_json_presence()
    check_symbols()
    check_shared_bodies()
    check_budgets()
    check_malformed()
    check_native_registry()
    await check_service()
