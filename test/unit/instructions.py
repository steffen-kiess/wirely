import datetime
import enum
import io

from wirely import types
from wirely.types.time import Duration as RuntimeDuration
from wirely.program import (
    BranchTarget, Constant, InputSourceBinding, Instruction, NativeInstance,
    Opcode, Operand, OperandKind, Procedure, ProcedureBody, Program, ResultBinding,
    RunStage, Variable, VariableBlock, validate_program,
)
from wirely.runtime.engine import Engine
from wirely.runtime.procedures import execute_procedure


def local(index):
    return Operand(OperandKind.LOCAL, index)


def result(index):
    return Operand(OperandKind.RESULT, index)


def const(index):
    return Operand(OperandKind.CONST, index)


def instruction(opcode, *operands):
    return Instruction(opcode, operands)


def make_program(instructions, *, local_types=(), result_types=(types.boolean,), constants=(), inputs=()):
    state = tuple(Variable(f'input{index}', value_type) for index, (value_type, _) in enumerate(inputs))
    outputs = tuple(Variable(f'result{index}', value_type) for index, value_type in enumerate(result_types))
    instance = NativeInstance('test', ('Test',), False, {}, ())
    procedure = Procedure(
        node_id='test', reference=(('Test',), 'run'), node_context=instance,
        dependencies=tuple((variable.name, (InputSourceBinding(variable),), False) for variable in state),
        resources=(), results={variable.name: ResultBinding(variable) for variable in outputs},
        body=ProcedureBody(instructions, local_types),
    )
    return Program(
        name='instructions', state_variables=VariableBlock(state, 'state'),
        temporaries=VariableBlock(outputs, 'temporaries'),
        initial_state={variable: value for variable, (_, value) in zip(state, inputs)},
        instances={'test': instance}, native_construction_order=('test',),
        procs={RunStage.DEFAULT: (procedure,)}, node_aliases={}, singleton_aliases={},
        variables={variable.name: variable for variable in outputs}, constants=constants,
    )


def execute(instructions, **kwargs):
    program = make_program(instructions, **kwargs)
    machine = Engine(program)
    machine.step()
    return tuple(machine.get_value(name) for name in program.variables)


def check_boolean_operations():
    # Unassigned locals/results are defaulted; aliased operands use old values.
    output = execute([
        instruction(Opcode.COPY, local(0), Operand(OperandKind.INPUT, 0)),
        instruction(Opcode.AND, local(0), const(0), local(0)),
        instruction(Opcode.NOT, local(1), local(0)),
        instruction(Opcode.OR, result(0), local(1), local(2)),
        instruction(Opcode.EQUAL, local(0), local(0), const(0)),
        instruction(Opcode.COPY, result(1), local(0)),
        instruction(Opcode.SET_DEFAULT, result(1)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,) * 3, result_types=(types.boolean,) * 3,
       constants=(Constant(types.boolean, True),), inputs=((types.boolean, False),))
    assert output == (True, False, False)
    assert execute([
        instruction(Opcode.NOT, local(0), local(0)),
        instruction(Opcode.EQUAL, local(0), local(0), const(0)),
        instruction(Opcode.COPY, result(0), local(0)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,), constants=(Constant(types.boolean, True),)) == (True,)


def check_structs_and_options():
    pair = types.TypeStruct('Pair', {'flag': types.boolean, 'number': types.uint8})
    option = types.TypeOption(pair)
    nested = types.TypeOption(option)
    initial = pair.make_value({'flag': False, 'number': 7})
    updated = pair.make_value({'flag': True, 'number': 7})
    output = execute([
        instruction(Opcode.COPY, local(0), const(0)),
        instruction(Opcode.COPY, local(0), local(0)),
        instruction(Opcode.COPY, result(0), local(0)),
        instruction(Opcode.SET_FIELD, local(0), const(1), 0),
        instruction(Opcode.COPY, result(1), local(0)),
        instruction(Opcode.GET_FIELD, result(2), local(0), 1),
        instruction(Opcode.MAKE_SOME, local(1), local(0)),
        instruction(Opcode.COPY, result(3), local(1)),
        instruction(Opcode.GET_OPTION_VALUE, local(2), local(1)),
        instruction(Opcode.EQUAL, result(4), local(0), local(2)),
        instruction(Opcode.SET_FIELD, local(0), const(2), 1),
        instruction(Opcode.MAKE_SOME, local(3), local(1)),
        instruction(Opcode.GET_OPTION_VALUE, result(5), local(3)),
        instruction(Opcode.IS_SOME, result(6), local(3)),
        instruction(Opcode.SET_DEFAULT, local(1)),
        instruction(Opcode.GET_OPTION_VALUE, result(7), local(1)),
        instruction(Opcode.IS_SOME, result(8), local(1)),
        instruction(Opcode.MAKE_SOME, local(3), local(1)),
        instruction(Opcode.COPY, result(9), local(3)),
        instruction(Opcode.SET_DEFAULT, local(3)),
        instruction(Opcode.EQUAL, result(10), local(1), const(3)),
        instruction(Opcode.RETURN),
    ], local_types=(pair, option, pair, nested),
       result_types=(pair, pair, types.uint8, option, types.boolean, option,
                     types.boolean, pair, types.boolean, nested, types.boolean),
       constants=(Constant(pair, initial), Constant(types.boolean, True),
                  Constant(types.uint8, 9), Constant(option, None)))
    assert output == (initial, updated, 7, types.Some(updated), True, types.Some(updated),
                      True, pair.default_value(), False, types.Some(None), True)


def check_defaults_and_equality():
    class Mode(enum.Enum):
        FIRST = 7
        SECOND = 9

    mode = types.TypeEnum('Mode', Mode, underlying_type=types.uint8)
    option = types.TypeOption(mode)
    output = execute([
        instruction(Opcode.GET_OPTION_VALUE, result(0), local(0)),
        instruction(Opcode.SET_DEFAULT, result(1)),
        instruction(Opcode.EQUAL, result(2), local(1), local(1)),
        instruction(Opcode.RETURN),
    ], local_types=(option, types.unit), result_types=(mode, mode, types.boolean))
    assert output == (Mode.FIRST, Mode.FIRST, True)


def check_ordering():
    output = execute([
        instruction(Opcode.LESS, result(0), const(0), const(1)),
        instruction(Opcode.LESS, result(1), const(1), const(0)),
        instruction(Opcode.LESS, result(2), const(0), const(0)),
        instruction(Opcode.LESS, result(3), const(2), const(3)),
        instruction(Opcode.LESS, result(4), const(4), const(5)),
        instruction(Opcode.RETURN),
    ], result_types=(types.boolean,) * 5,
       constants=(Constant(types.int16, -1), Constant(types.int16, 0),
                  Constant(types.Duration, RuntimeDuration(duration_ns=1)),
                  Constant(types.Duration, RuntimeDuration(duration_ns=2)),
                  Constant(types.TimeOfDay, datetime.time(23, 59)),
                  Constant(types.TimeOfDay, datetime.time(0, 0))))
    assert output == (True, False, False, True, False)


def check_arithmetic():
    int8_option = types.TypeOption(types.int8)
    cases = [  # opcode, left, right, expected
        (Opcode.ADD_WRAPPING, 127, 1, -128),
        (Opcode.ADD_SATURATING, 127, 1, 127),
        (Opcode.ADD_CHECKED, 127, 1, None),
        (Opcode.ADD_CHECKED, 126, 1, types.Some(127)),
        (Opcode.SUB_WRAPPING, -128, 1, 127),
        (Opcode.SUB_SATURATING, 0, -128, 127),
        (Opcode.SUB_CHECKED, 0, -128, None),
        (Opcode.MUL_WRAPPING, 64, 2, -128),
        (Opcode.MUL_SATURATING, -64, 3, -128),
        (Opcode.MUL_CHECKED, -64, 2, types.Some(-128)),
        (Opcode.DIV_CHECKED, -7, 2, types.Some(-3)),
        (Opcode.DIV_CHECKED, -128, -1, None),
        (Opcode.DIV_CHECKED, 1, 0, None),
        (Opcode.REM_CHECKED, -7, 2, types.Some(-1)),
        (Opcode.REM_CHECKED, 1, 0, None),
    ]
    constants = []
    instructions = []
    result_types = []
    for index, (opcode, left, right, expected) in enumerate(cases):
        constants += [Constant(types.int8, left), Constant(types.int8, right)]
        instructions.append(instruction(opcode, result(index), const(2 * index), const(2 * index + 1)))
        result_types.append(int8_option if opcode.name.endswith('_CHECKED') else types.int8)
    instructions.append(instruction(Opcode.RETURN))
    output = execute(instructions, result_types=tuple(result_types), constants=tuple(constants))
    assert output == tuple(expected for _, _, _, expected in cases)
    # Unsigned results wrap modulo 2**bits and saturate at zero.
    output = execute([
        instruction(Opcode.SUB_WRAPPING, result(0), const(0), const(1)),
        instruction(Opcode.SUB_SATURATING, result(1), const(0), const(1)),
        instruction(Opcode.RETURN),
    ], result_types=(types.uint8, types.uint8),
       constants=(Constant(types.uint8, 0), Constant(types.uint8, 1)))
    assert output == (255, 0)

    program = make_program([
        instruction(Opcode.NOT, local(0), local(0)),
        instruction(Opcode.COPY, result(0), local(0)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,))
    validate_program(program)
    procedure = program.procs[RunStage.DEFAULT][0]
    for _ in range(3):
        assert execute_procedure(program, procedure, {}) == {'result0': True}


def check_branches():
    for condition in (False, True):
        output = execute([
            instruction(Opcode.JUMP_IF_FALSE, Operand(OperandKind.INPUT, 0), BranchTarget(3)),
            instruction(Opcode.COPY, result(0), const(0)),
            instruction(Opcode.JUMP, BranchTarget(4)),
            instruction(Opcode.COPY, result(0), const(1)),
            instruction(Opcode.JUMP_IF_TRUE, result(0), BranchTarget(6)),
            instruction(Opcode.RETURN),
            instruction(Opcode.COPY, result(1), const(0)),
            instruction(Opcode.RETURN),
        ], result_types=(types.boolean, types.boolean),
           constants=(Constant(types.boolean, True), Constant(types.boolean, False)),
           inputs=((types.boolean, condition),))
        assert output == (condition, condition)
    assert execute([instruction(Opcode.RETURN), instruction(Opcode.NOT, result(0), result(0)),
                    instruction(Opcode.RETURN)]) == (False,)
    assert execute([instruction(Opcode.JUMP, BranchTarget(1)), instruction(Opcode.RETURN)]) == (False,)


def expect_invalid(instructions, message, **kwargs):
    program = make_program(instructions, **kwargs)
    for check in (validate_program, Engine):
        try:
            check(program)
        except ValueError as error:
            assert message in str(error), (message, error)
        else:
            raise AssertionError('Invalid instructions were accepted')


def check_validation():
    ret = instruction(Opcode.RETURN)
    expect_invalid([], 'end in RETURN')
    expect_invalid([instruction(Opcode.NOT, result(0), result(0))], 'end in RETURN')
    expect_invalid([instruction('COPY', result(0), result(0)), ret], 'known opcode')
    expect_invalid([instruction(Opcode.COPY, result(0)), ret], 'Invalid operands')
    expect_invalid([instruction(Opcode.RETURN, result(0))], 'Invalid operands')
    expect_invalid([instruction(Opcode.COPY, result(0), 0), ret], 'Invalid operands')
    expect_invalid([instruction(Opcode.COPY, result(0), Operand('local', 0)), ret], 'Unknown operand kind')
    for index in (-1, 1, True):
        expect_invalid([instruction(Opcode.NOT, result(index), result(0)), ret], 'out of range')
    expect_invalid([instruction(Opcode.COPY, const(0), result(0)), ret], 'Destination',
                   constants=(Constant(types.boolean, True),))
    expect_invalid([instruction(Opcode.COPY, Operand(OperandKind.INPUT, 0), result(0)), ret], 'Destination',
                   inputs=((types.boolean, False),))
    expect_invalid([instruction(Opcode.COPY, result(0), const(0)), ret], 'Expected type',
                   constants=(Constant(types.uint8, 0),))
    expect_invalid([instruction(Opcode.NOT, result(0), result(0)), ret], 'Expected type',
                   result_types=(types.uint8,))
    expect_invalid([instruction(Opcode.EQUAL, result(0), result(0), const(0)), ret], 'Expected type',
                   constants=(Constant(types.uint8, 0),))
    for value_type in (types.boolean, types.MonotonicTime, types.TypeOption(types.uint8)):
        expect_invalid([instruction(Opcode.LESS, result(0), local(0), local(0)), ret], 'not ordered',
                       local_types=(value_type,))
    expect_invalid([instruction(Opcode.LESS, result(0), local(0), const(0)), ret], 'Expected type',
                   local_types=(types.uint8,), constants=(Constant(types.int8, 0),))
    expect_invalid([instruction(Opcode.ADD_WRAPPING, result(0), local(0), local(0)), ret], 'integer type',
                   local_types=(types.boolean,))
    expect_invalid([instruction(Opcode.ADD_WRAPPING, result(0), local(0), local(1)), ret], 'Expected type',
                   result_types=(types.uint8,), local_types=(types.uint8, types.int8))
    expect_invalid([instruction(Opcode.ADD_CHECKED, result(0), local(0), local(0)), ret], 'Expected type',
                   result_types=(types.uint8,), local_types=(types.uint8,))
    expect_invalid([instruction(Opcode.ADD_WRAPPING, result(0), local(0), local(0)), ret], 'Expected type',
                   result_types=(types.TypeOption(types.uint8),), local_types=(types.uint8,))
    expect_invalid([instruction(Opcode.JUMP, 1), ret], 'Invalid operands')
    for index in (-1, 0, 2, True):
        expect_invalid([instruction(Opcode.JUMP, BranchTarget(index)), ret], 'later instruction')
    expect_invalid([instruction(Opcode.JUMP, BranchTarget(1)),
                    instruction(Opcode.JUMP, BranchTarget(0)), ret], 'later instruction')
    expect_invalid([instruction(Opcode.JUMP_IF_TRUE, const(0), BranchTarget(1)), ret], 'Expected type',
                   constants=(Constant(types.uint8, 0),))
    pair = types.TypeStruct('Pair', {'value': types.uint8})
    for field in (-1, 1):
        expect_invalid([instruction(Opcode.GET_FIELD, result(0), local(0), field), ret], 'Field index',
                       result_types=(types.uint8,), local_types=(pair,))
    expect_invalid([instruction(Opcode.GET_FIELD, result(0), result(0), 0), ret], 'requires a struct')
    expect_invalid([instruction(Opcode.GET_FIELD, result(0), local(0), BranchTarget(0)), ret], 'Invalid operands',
                   local_types=(pair,))
    expect_invalid([instruction(Opcode.GET_FIELD, result(0), local(0), 0), ret], 'Expected type',
                   local_types=(pair,))
    expect_invalid([instruction(Opcode.SET_FIELD, local(0), result(0), 0), ret], 'Expected type',
                   local_types=(pair,))
    expect_invalid([instruction(Opcode.MAKE_SOME, result(0), result(0)), ret], 'Option type')
    expect_invalid([instruction(Opcode.GET_OPTION_VALUE, result(0), local(0)), ret], 'Expected type',
                   local_types=(types.TypeOption(types.uint8),))
    expect_invalid([instruction(Opcode.IS_SOME, result(0), local(0)), ret], 'Expected type',
                   result_types=(types.uint8,), local_types=(types.TypeOption(types.uint8),))
    expect_invalid([instruction(Opcode.MAKE_SOME, local(0), result(0)), ret], 'Expected type',
                   local_types=(types.TypeOption(types.uint8),))
    expect_invalid([ret], 'runtime type', local_types=(types.String,))
    expect_invalid([ret], 'runtime type', constants=(Constant(types.String, ''),))
    expect_invalid([ret], 'Constant const.0', constants=(Constant(types.uint8, 256),))
    expect_invalid([ret, instruction(Opcode.COPY, result(0), const(0)), ret], 'out of range')


def check_dump():
    pair = types.TypeStruct('Pair', {'value': types.boolean})
    program = make_program([
        instruction(Opcode.GET_FIELD, local(0), const(0), 0),
        instruction(Opcode.JUMP_IF_TRUE, local(0), BranchTarget(3)),
        instruction(Opcode.COPY, result(0), local(0)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,), constants=(Constant(pair, pair.default_value()),))
    stream = io.StringIO()
    program.dump(stream)
    text = stream.getvalue()
    assert 'const.0: Pair' in text
    assert 'local.0: boolean' in text
    assert 'idx0: GET_FIELD local.0, const.0, 0' in text
    assert 'idx1: JUMP_IF_TRUE local.0, idx3' in text
    assert 'idx3: RETURN' in text


check_boolean_operations()
check_structs_and_options()
check_defaults_and_equality()
check_ordering()
check_arithmetic()
check_branches()
check_validation()
check_dump()
