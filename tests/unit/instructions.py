import io
from unittest import TestCase

import wirely
from wirely import Some, Unit
from wirely.program import types
from tests.infra.native_extension.generated import test_library as contracts
from tests.infra.support import library_type, test_extension
from wirely.program import (
    BranchTarget, Constant, ConstOperand, Function, InputSource, Instruction,
    Opcode, Operand, OperandKind, Procedure, ProcedureBody, Program, ProgramSymbols, OutputTarget,
    RunStage, Variable, VariableBank, VariableTable, validate_program,
)
from wirely.runtime.engine import Engine
from wirely.runtime.extensions import NativeTypes
from wirely.runtime.procedures import execute_procedure


checks = TestCase()
duration = library_type('Std.Duration')


def local(index):
    return Operand(OperandKind.LOCAL, index)


def output(index):
    return Operand(OperandKind.OUTPUT, index)


def const(value_type, value):
    return ConstOperand(Constant(value_type, value))


def instruction(opcode, *operands):
    return Instruction(opcode, operands)


def make_program(instructions, *, local_types=(), output_types=(types.boolean,), inputs=()):
    state = tuple(Variable(value_type) for index, (value_type, _) in enumerate(inputs))
    outputs = tuple(Variable(value_type) for index, value_type in enumerate(output_types))
    procedure = Procedure(
        name='test.run',
        inputs=tuple(InputSource(VariableBank.OLD_STATE, variable) for variable in state),
        outputs=tuple(OutputTarget(VariableBank.TEMPORARIES, variable) for variable in outputs),
        body=ProcedureBody(instructions, local_types),
    )
    return Program(
        state_variables=VariableTable(state, 'state'),
        temporaries=VariableTable(outputs, 'temporaries'),
        initial_state={variable: value for variable, (_, value) in zip(state, inputs)},
        procs={RunStage.DEFAULT: (procedure,)},
        symbols=ProgramSymbols(variable_aliases={
            f'output{index}': InputSource(VariableBank.TEMPORARIES, variable) for index, variable in enumerate(outputs)}),
    )


def execute(instructions, *, override_extensions=(), **kwargs):
    program = make_program(instructions, **kwargs)
    from tests.infra.native_extension.generated import extension as fixture
    machine = Engine(program, extensions=[fixture], override_extensions=override_extensions)
    machine.step()
    return tuple(machine.get_value(name) for name in program.symbols.variable_aliases)


def check_boolean_operations():
    # Unassigned locals/outputs are defaulted; aliased operands use old values.
    true = const(types.boolean, True)
    outcome = execute([
        instruction(Opcode.COPY, local(0), Operand(OperandKind.INPUT, 0)),
        instruction(Opcode.AND, local(0), true, local(0)),
        instruction(Opcode.NOT, local(1), local(0)),
        instruction(Opcode.OR, output(0), local(1), local(2)),
        instruction(Opcode.EQUAL, local(0), local(0), true),
        instruction(Opcode.COPY, output(1), local(0)),
        instruction(Opcode.SET_DEFAULT, output(1)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,) * 3, output_types=(types.boolean,) * 3, inputs=((types.boolean, False),))
    assert outcome == (True, False, False)
    assert execute([
        instruction(Opcode.NOT, local(0), local(0)),
        instruction(Opcode.EQUAL, local(0), local(0), true),
        instruction(Opcode.COPY, output(0), local(0)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,)) == (True,)


def check_structs_and_options():
    pair = types.TypeStruct('Pair', {'flag': types.boolean, 'number': types.uint8})
    option = types.TypeOption(pair)
    nested = types.TypeOption(option)
    initial = pair.make_value({'flag': False, 'number': 7})
    updated = pair.make_value({'flag': True, 'number': 7})
    outcome = execute([
        instruction(Opcode.COPY, local(0), const(pair, initial)),
        instruction(Opcode.COPY, local(0), local(0)),
        instruction(Opcode.COPY, output(0), local(0)),
        instruction(Opcode.SET_FIELD, local(0), const(types.boolean, True), 0),
        instruction(Opcode.COPY, output(1), local(0)),
        instruction(Opcode.GET_FIELD, output(2), local(0), 1),
        instruction(Opcode.MAKE_SOME, local(1), local(0)),
        instruction(Opcode.COPY, output(3), local(1)),
        instruction(Opcode.GET_OPTION_VALUE, local(2), local(1)),
        instruction(Opcode.EQUAL, output(4), local(0), local(2)),
        instruction(Opcode.SET_FIELD, local(0), const(types.uint8, 9), 1),
        instruction(Opcode.MAKE_SOME, local(3), local(1)),
        instruction(Opcode.GET_OPTION_VALUE, output(5), local(3)),
        instruction(Opcode.IS_SOME, output(6), local(3)),
        instruction(Opcode.SET_DEFAULT, local(1)),
        instruction(Opcode.GET_OPTION_VALUE, output(7), local(1)),
        instruction(Opcode.IS_SOME, output(8), local(1)),
        instruction(Opcode.MAKE_SOME, local(3), local(1)),
        instruction(Opcode.COPY, output(9), local(3)),
        instruction(Opcode.SET_DEFAULT, local(3)),
        instruction(Opcode.EQUAL, output(10), local(1), const(option, None)),
        instruction(Opcode.RETURN),
    ], local_types=(pair, option, pair, nested),
       output_types=(pair, pair, types.uint8, option, types.boolean, option,
                     types.boolean, pair, types.boolean, nested, types.boolean))
    assert outcome == (initial, updated, 7, Some(updated), True, Some(updated),
                      True, pair.default_value(), False, Some(None), True)


def check_conversions():
    level = types.TypeEnum('Level', {'OFF': 0, 'HIGH': 200}, is_open=True, underlying_type=types.uint8)
    boolean_option = types.TypeOption(types.boolean)
    int8_option = types.TypeOption(types.int8)
    cases = [  # opcode, source type, value, destination type, expected
        (Opcode.CONVERT_WRAPPING, types.int16, 300, types.int8, 44),
        (Opcode.CONVERT_SATURATING, types.int16, 300, types.int8, 127),
        (Opcode.CONVERT_CHECKED, types.int16, 300, int8_option, None),
        (Opcode.CONVERT_CHECKED, types.int16, -128, int8_option, Some(-128)),
        (Opcode.CONVERT_SATURATING, types.int16, -1, types.uint8, 0),
        (Opcode.CONVERT_WRAPPING, level, 200, types.int8, -56),
        (Opcode.CONVERT_WRAPPING, types.boolean, True, types.uint8, 1),
        (Opcode.CONVERT_WRAPPING, types.boolean, False, types.int64, 0),
    ]
    # Booleans are 1-bit unsigned integers.
    for value, wrapped, saturated, checked in [
            (0, False, False, Some(False)), (1, True, True, Some(True)),
            (2, False, True, None), (3, True, True, None), (-1, True, False, None)]:
        cases += [(Opcode.CONVERT_WRAPPING, types.int8, value, types.boolean, wrapped),
                  (Opcode.CONVERT_SATURATING, types.int8, value, types.boolean, saturated),
                  (Opcode.CONVERT_CHECKED, types.int8, value, boolean_option, checked)]
    instructions = [instruction(opcode, output(index), const(source, value))
                    for index, (opcode, source, value, _, _) in enumerate(cases)]
    outcome = execute([*instructions, instruction(Opcode.RETURN)],
                     output_types=tuple(target for _, _, _, target, _ in cases))
    assert outcome == tuple(expected for *_, expected in cases)


def check_calls():
    clamp = Function('TestLibrary.Clamp', (types.int16,) * 3, types.int16)
    nothing = Function('TestLibrary.Nothing', (), types.unit)

    def clamp_values(value, low, high):
        assert (value, low, high) == (wirely.Int16(500), wirely.Int16(0), wirely.Int16(100))
        return min(max(value, low), high)

    def no_values():
        return Unit()

    bindings = test_extension(functions={'TestLibrary.Clamp': contracts._Clamp.implementation(clamp_values),
                                            'TestLibrary.Nothing': contracts._Nothing.implementation(no_values)})
    outcome = execute([
        instruction(Opcode.CALL, output(0), clamp, 3,
                    const(types.int16, 500), const(types.int16, 0), const(types.int16, 100)),
        instruction(Opcode.CALL, output(1), nothing, 0),
        instruction(Opcode.RETURN),
    ], output_types=(types.int16, types.unit), override_extensions=[bindings])
    assert outcome == (100, Unit())

    # Linking requires an implementation for every called function, even if unreachable.
    missing = Function('TestLibrary.Missing', (), types.boolean)
    with checks.assertRaisesRegex(ValueError, "Cannot find the signature of native function `TestLibrary.Missing`"):
        execute([instruction(Opcode.RETURN), instruction(Opcode.CALL, output(0), missing, 0),
                 instruction(Opcode.RETURN)])

    # Implementations must not fail; an exception propagates.
    fail = Function('TestLibrary.Fail', (), types.boolean)
    with checks.assertRaisesRegex(RuntimeError, 'Fail always fails'):
        execute([instruction(Opcode.CALL, output(0), fail, 0), instruction(Opcode.RETURN)])

    ret = instruction(Opcode.RETURN)
    options = dict(output_types=(types.int16,), local_types=(types.int16, types.uint8))
    expect_invalid([instruction(Opcode.CALL, output(0), 0, 0), ret], 'Invalid operands', **options)
    expect_invalid([instruction(Opcode.CALL, output(0), clamp, 2, local(0), local(0)), ret], 'expects 3 arguments',
                   **options)
    expect_invalid([instruction(Opcode.CALL, output(0), clamp, 3, local(0), local(0)), ret], 'Invalid operands',
                   **options)
    expect_invalid([instruction(Opcode.CALL, output(0), clamp, 3, local(0), local(1), local(0)), ret],
                   'Expected type `int16`, got `uint8`', **options)
    expect_invalid([instruction(Opcode.CALL, local(1), clamp, 3, local(0), local(0), local(0)), ret],
                   'Expected type `int16`, got `uint8`', **options)
    invalid = Function('Test', (types.String,), types.unit)
    expect_invalid([ret, instruction(Opcode.CALL, local(0), invalid, 1, local(1)), ret], 'Function func.0',
                   local_types=(types.unit, types.String))
    other_clamp = Function('TestLibrary.Clamp', (types.int8,) * 3, types.int8)
    try:
        make_program([instruction(Opcode.CALL, output(0), clamp, 3, local(0), local(0), local(0)),
                      instruction(Opcode.CALL, local(1), other_clamp, 3, local(1), local(1), local(1)), ret],
                     **options)
    except ValueError as error:
        assert 'different signatures' in str(error)
    else:
        raise AssertionError('Conflicting functions were accepted')


def check_defaults_and_equality():
    mode = types.TypeEnum('Mode', {'FIRST': 7, 'SECOND': 9}, is_open=True, underlying_type=types.uint8)
    option = types.TypeOption(mode)
    outcome = execute([
        instruction(Opcode.GET_OPTION_VALUE, output(0), local(0)),
        instruction(Opcode.SET_DEFAULT, output(1)),
        instruction(Opcode.EQUAL, output(2), local(1), local(1)),
        instruction(Opcode.RETURN),
    ], local_types=(option, types.unit), output_types=(mode, mode, types.boolean))
    # Enum values are member numbers inside the runtime.
    assert outcome == (0, 0, True)


def check_ordering():
    minus_one, zero = const(types.int16, -1), const(types.int16, 0)
    outcome = execute([
        instruction(Opcode.LESS, output(0), minus_one, zero),
        instruction(Opcode.LESS, output(1), zero, minus_one),
        instruction(Opcode.LESS, output(2), minus_one, minus_one),
        instruction(Opcode.RETURN),
    ], output_types=(types.boolean,) * 3)
    assert outcome == (True, False, False)


def check_arithmetic():
    int8_option = types.TypeOption(types.int8)
    cases = [  # opcode, left, right, expected
        (Opcode.ADD_WRAPPING, 127, 1, -128),
        (Opcode.ADD_SATURATING, 127, 1, 127),
        (Opcode.ADD_CHECKED, 127, 1, None),
        (Opcode.ADD_CHECKED, 126, 1, Some(127)),
        (Opcode.SUB_WRAPPING, -128, 1, 127),
        (Opcode.SUB_SATURATING, 0, -128, 127),
        (Opcode.SUB_CHECKED, 0, -128, None),
        (Opcode.MUL_WRAPPING, 64, 2, -128),
        (Opcode.MUL_SATURATING, -64, 3, -128),
        (Opcode.MUL_CHECKED, -64, 2, Some(-128)),
        (Opcode.DIV_CHECKED, -7, 2, Some(-3)),
        (Opcode.DIV_CHECKED, -128, -1, None),
        (Opcode.DIV_CHECKED, 1, 0, None),
        (Opcode.REM_CHECKED, -7, 2, Some(-1)),
        (Opcode.REM_CHECKED, 1, 0, None),
    ]
    instructions = []
    output_types = []
    for index, (opcode, left, right, expected) in enumerate(cases):
        instructions.append(instruction(opcode, output(index), const(types.int8, left), const(types.int8, right)))
        output_types.append(int8_option if opcode.name.endswith('_CHECKED') else types.int8)
    instructions.append(instruction(Opcode.RETURN))
    outcome = execute(instructions, output_types=tuple(output_types))
    assert outcome == tuple(expected for _, _, _, expected in cases)
    # Unsigned results wrap modulo 2**bits and saturate at zero.
    zero, one = const(types.uint8, 0), const(types.uint8, 1)
    outcome = execute([
        instruction(Opcode.SUB_WRAPPING, output(0), zero, one),
        instruction(Opcode.SUB_SATURATING, output(1), zero, one),
        instruction(Opcode.RETURN),
    ], output_types=(types.uint8, types.uint8))
    assert outcome == (255, 0)

    program = make_program([
        instruction(Opcode.NOT, local(0), local(0)),
        instruction(Opcode.COPY, output(0), local(0)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,))
    validate_program(program)
    procedure = program.procs[RunStage.DEFAULT][0]
    for _ in range(3):
        assert execute_procedure(procedure, (), native_types=NativeTypes()) == [True]


def check_bitwise_and_shifts():
    source = Operand(OperandKind.INPUT, 0)
    count_input = Operand(OperandKind.INPUT, 1)
    ret = instruction(Opcode.RETURN)
    for bits in (8, 16, 32, 64, 128):
        for signed in (True, False):
            value_type = getattr(types, ('int' if signed else 'uint') + str(bits))
            mask = (1 << bits) - 1

            def normalized(value):
                value &= mask
                return value - (1 << bits) if signed and value >= (1 << (bits - 1)) else value

            five = const(value_type, 5)
            for value in (0, value_type.min_value, value_type.max_value):
                outcome = execute([
                    instruction(Opcode.NOT, output(0), source),
                    instruction(Opcode.AND, output(1), source, five),
                    instruction(Opcode.OR, output(2), source, five),
                    instruction(Opcode.XOR, output(3), source, five),
                    ret,
                ], output_types=(value_type,) * 4, inputs=((value_type, value),))
                assert outcome == (normalized(~value), value & 5, value | 5, value ^ 5)

                for count in (0, 1, bits - 1, bits, bits + 1, 0xffffffff):
                    left = normalized(value << count) if count < bits else 0
                    right = value >> count if count < bits else (-1 if value < 0 else 0)
                    outcome = execute([
                        instruction(Opcode.COPY, output(0), source),
                        instruction(Opcode.SHIFT_LEFT, output(0), output(0), count_input),
                        instruction(Opcode.COPY, output(1), source),
                        instruction(Opcode.SHIFT_RIGHT, output(1), output(1), count_input),
                        ret,
                    ], output_types=(value_type,) * 2,
                       inputs=((value_type, value), (types.uint32, count)))
                    assert outcome == (left, right), (value_type, value, count, outcome)

    for left in (False, True):
        for right in (False, True):
            assert execute([
                instruction(Opcode.XOR, output(0), source, count_input), ret,
            ], inputs=((types.boolean, left), (types.boolean, right))) == (left != right,)

    for opcode in (Opcode.NOT, Opcode.AND, Opcode.OR, Opcode.XOR):
        operands = (output(0), local(0)) if opcode == Opcode.NOT else (output(0), local(0), local(0))
        expect_invalid([instruction(opcode, *operands), ret], 'integer or boolean',
                       output_types=(duration,), local_types=(duration,))
        expect_invalid([instruction(opcode, *operands), ret], 'Expected type',
                       output_types=(types.uint8,), local_types=(types.boolean,))
    for opcode in (Opcode.SHIFT_LEFT, Opcode.SHIFT_RIGHT):
        for count_type in (types.int32, types.uint8, types.uint64, types.boolean):
            expect_invalid([instruction(opcode, output(0), local(0), local(1)), ret], 'Expected type',
                           output_types=(types.uint8,), local_types=(types.uint8, count_type))
        expect_invalid([instruction(opcode, output(0), local(0), local(1)), ret], 'integer type',
                       local_types=(types.boolean, types.uint32))
        expect_invalid([instruction(opcode, output(0), local(0), local(1)), ret], 'Expected type',
                       output_types=(types.uint16,), local_types=(types.uint8, types.uint32))
        expect_invalid([instruction(opcode, output(0), local(0), 1), ret], 'Invalid operands',
                       output_types=(types.uint8,), local_types=(types.uint8,))

def check_branches():
    true, false = const(types.boolean, True), const(types.boolean, False)
    assert execute([
        instruction(Opcode.COPY, output(0), true),
        instruction(Opcode.COPY, output(0), false),
        instruction(Opcode.RETURN),
    ]) == (False,)
    for condition in (False, True):
        outcome = execute([
            instruction(Opcode.JUMP_IF_FALSE, Operand(OperandKind.INPUT, 0), BranchTarget(3)),
            instruction(Opcode.COPY, output(0), true),
            instruction(Opcode.JUMP, BranchTarget(4)),
            instruction(Opcode.COPY, output(0), false),
            instruction(Opcode.JUMP_IF_TRUE, output(0), BranchTarget(6)),
            instruction(Opcode.RETURN),
            instruction(Opcode.COPY, output(1), true),
            instruction(Opcode.RETURN),
        ], output_types=(types.boolean, types.boolean), inputs=((types.boolean, condition),))
        assert outcome == (condition, condition)
    assert execute([instruction(Opcode.RETURN), instruction(Opcode.NOT, output(0), output(0)),
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
    expect_invalid([instruction(Opcode.NOT, output(0), output(0))], 'end in RETURN')
    expect_invalid([instruction('COPY', output(0), output(0)), ret], 'known opcode')
    expect_invalid([instruction(Opcode.COPY, output(0)), ret], 'Invalid operands')
    expect_invalid([instruction(Opcode.RETURN, output(0))], 'Invalid operands')
    expect_invalid([instruction(Opcode.COPY, output(0), 0), ret], 'Invalid operands')
    expect_invalid([instruction(Opcode.COPY, output(0), Operand('local', 0)), ret], 'Unknown operand kind')
    for index in (-1, 1, True):
        expect_invalid([instruction(Opcode.NOT, output(index), output(0)), ret], 'out of range')
    expect_invalid([instruction(Opcode.COPY, const(types.boolean, True), output(0)), ret], 'Destination')
    expect_invalid([instruction(Opcode.COPY, Operand(OperandKind.INPUT, 0), output(0)), ret], 'Destination',
                   inputs=((types.boolean, False),))
    expect_invalid([instruction(Opcode.COPY, output(0), const(types.uint8, 0)), ret], 'Expected type')
    expect_invalid([instruction(Opcode.EQUAL, output(0), output(0), const(types.uint8, 0)), ret], 'Expected type')
    for value_type in (types.boolean, duration, library_type('Schedule.TimeOfDay'),
                       library_type('Timer.MonotonicTime'), types.TypeOption(types.uint8)):
        expect_invalid([instruction(Opcode.LESS, output(0), local(0), local(0)), ret], 'integer type',
                       local_types=(value_type,))
    expect_invalid([instruction(Opcode.LESS, output(0), local(0), const(types.int8, 0)), ret], 'Expected type',
                   local_types=(types.uint8,))
    expect_invalid([instruction(Opcode.ADD_WRAPPING, output(0), local(0), local(0)), ret], 'integer type',
                   local_types=(types.boolean,))
    expect_invalid([instruction(Opcode.ADD_WRAPPING, output(0), local(0), local(1)), ret], 'Expected type',
                   output_types=(types.uint8,), local_types=(types.uint8, types.int8))
    expect_invalid([instruction(Opcode.ADD_CHECKED, output(0), local(0), local(0)), ret], 'Expected type',
                   output_types=(types.uint8,), local_types=(types.uint8,))
    expect_invalid([instruction(Opcode.ADD_WRAPPING, output(0), local(0), local(0)), ret], 'Expected type',
                   output_types=(types.TypeOption(types.uint8),), local_types=(types.uint8,))
    for value_type in (duration, types.TypeOption(types.uint8)):
        expect_invalid([instruction(Opcode.CONVERT_WRAPPING, output(0), local(0)), ret], 'Conversion source',
                       output_types=(types.uint8,), local_types=(value_type,))
    expect_invalid([instruction(Opcode.CONVERT_CHECKED, output(0), local(0)), ret], 'Option destination',
                   output_types=(types.uint8,), local_types=(types.int8,))
    expect_invalid([instruction(Opcode.CONVERT_WRAPPING, output(0), local(0)), ret], 'Conversion destination',
                   output_types=(types.TypeOption(types.uint8),), local_types=(types.int8,))
    expect_invalid([instruction(Opcode.CONVERT_WRAPPING, output(0), local(0)), ret], 'Conversion destination',
                   output_types=(duration,), local_types=(types.int8,))
    expect_invalid([instruction(Opcode.JUMP, 1), ret], 'Invalid operands')
    for index in (-1, 0, 2, True):
        expect_invalid([instruction(Opcode.JUMP, BranchTarget(index)), ret], 'later instruction')
    expect_invalid([instruction(Opcode.JUMP, BranchTarget(1)),
                    instruction(Opcode.JUMP, BranchTarget(0)), ret], 'later instruction')
    expect_invalid([instruction(Opcode.JUMP_IF_TRUE, const(types.uint8, 0), BranchTarget(1)), ret], 'Expected type')
    pair = types.TypeStruct('Pair', {'value': types.uint8})
    for field in (-1, 1):
        expect_invalid([instruction(Opcode.GET_FIELD, output(0), local(0), field), ret], 'Field index',
                       output_types=(types.uint8,), local_types=(pair,))
    expect_invalid([instruction(Opcode.GET_FIELD, output(0), output(0), 0), ret], 'requires a non-opaque struct')
    # Opaque struct fields are only accessible to native code.
    expect_invalid([instruction(Opcode.GET_FIELD, output(0), local(0), 0), ret], 'requires a non-opaque struct',
                   output_types=(types.uint128,), local_types=(duration,))
    expect_invalid([instruction(Opcode.SET_FIELD, local(0), output(0), 0), ret], 'requires a non-opaque struct',
                   output_types=(types.uint128,), local_types=(duration,))
    expect_invalid([instruction(Opcode.GET_FIELD, output(0), local(0), BranchTarget(0)), ret], 'Invalid operands',
                   local_types=(pair,))
    expect_invalid([instruction(Opcode.GET_FIELD, output(0), local(0), 0), ret], 'Expected type',
                   local_types=(pair,))
    expect_invalid([instruction(Opcode.SET_FIELD, local(0), output(0), 0), ret], 'Expected type',
                   local_types=(pair,))
    expect_invalid([instruction(Opcode.MAKE_SOME, output(0), output(0)), ret], 'Option type')
    expect_invalid([instruction(Opcode.GET_OPTION_VALUE, output(0), local(0)), ret], 'Expected type',
                   local_types=(types.TypeOption(types.uint8),))
    expect_invalid([instruction(Opcode.IS_SOME, output(0), local(0)), ret], 'Expected type',
                   output_types=(types.uint8,), local_types=(types.TypeOption(types.uint8),))
    expect_invalid([instruction(Opcode.MAKE_SOME, local(0), output(0)), ret], 'Expected type',
                   local_types=(types.TypeOption(types.uint8),))
    expect_invalid([ret], 'runtime type', local_types=(types.String,))
    # Unreachable instructions are checked too.
    expect_invalid([ret, instruction(Opcode.COPY, local(0), const(types.String, '')), ret], 'runtime type',
                   local_types=(types.String,))
    with checks.assertRaisesRegex(ValueError, 'Invalid `uint8` value 256'):
        const(types.uint8, 256)
    with checks.assertRaises(AttributeError):
        Opcode.COPY.label = 'copy'


def check_dump():
    pair = types.TypeStruct('Pair', {'value': types.boolean})
    program = make_program([
        instruction(Opcode.GET_FIELD, local(0), const(pair, pair.default_value()), 0),
        instruction(Opcode.JUMP_IF_TRUE, local(0), BranchTarget(3)),
        instruction(Opcode.COPY, output(0), local(0)),
        instruction(Opcode.RETURN),
    ], local_types=(types.boolean,))
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
check_conversions()
check_calls()
check_branches()
check_bitwise_and_shifts()
check_validation()
check_dump()


async def run_test():
    pass
