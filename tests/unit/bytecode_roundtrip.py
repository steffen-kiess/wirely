"""A small execution probe for the test runner's bytecode mode."""

from wirely.program import types
from wirely.program import (
    Constant, ConstOperand, InputSource, Instruction, Opcode, Operand, OperandKind, OutputTarget,
    Procedure, ProcedureBody, Program, ProgramSymbols, RunStage, Variable, VariableBank, VariableTable,
)
from tests.infra.bytecode import bytecode_roundtrip_mode
from tests.infra.support import test_engine


def make_program():
    pair = types.TypeStruct('Pair', {'flag': types.boolean, 'number': types.uint8})
    variable = Variable(pair)
    value = Constant(pair, (True, 7))
    procedure = Procedure((), (OutputTarget(VariableBank.TEMPORARIES, variable),), name='source.run', body=ProcedureBody((
        Instruction(Opcode.COPY, (Operand(OperandKind.OUTPUT, 0), ConstOperand(value))),
        Instruction(Opcode.RETURN, ()),
    )))
    return Program(state_variables=VariableTable((), 'state'), temporaries=VariableTable((variable,), 'temporaries'),
                   initial_state={}, procs={RunStage.DEFAULT: (procedure,)}, graph=object(),
                   symbols=ProgramSymbols(name='probe', node_aliases={'alias': 'source'},
                                          singleton_aliases={'Source': 'source'}, variable_aliases={
                                              'alias.Value': InputSource(VariableBank.TEMPORARIES, variable),
                                              'constant': InputSource(VariableBank.CONST_VALUES, constant=value)}))


def check_engine(original, engine):
    engine.step()
    variable, = engine.program.temporaries.offsets
    assert engine.cache[variable] == (True, 7)
    assert variable.name is None
    assert engine.program.graph is original.graph
    assert engine.program.symbols.name == 'probe'
    assert engine.program.symbols.node_aliases == {'alias': 'source'}
    assert engine.program.symbols.singleton_aliases == {'Source': 'source'}
    assert engine.program.symbols.variable_aliases['alias.Value'].variable is variable
    constant = engine.program.symbols.variable_aliases['constant'].constant
    assert constant.value == (True, 7) and constant.value_type is variable.var_type
    assert list(variable.var_type.fields) == ['flag', 'number']
    assert variable.var_type.type_name == 'Pair'
    assert engine.program.procs[RunStage.DEFAULT][0].name == 'source.run'


async def run_test():
    original = make_program()
    engine = test_engine(original)
    decoded = engine.program is not original
    assert decoded == bytecode_roundtrip_mode.get()
    check_engine(original, engine)
    # The parent runner smoke tests observe actual decoding, rather than just
    # reading the flag that they are supposed to propagate to workers.
    print(f'Bytecode round-trip decoded: {decoded}')
    token = bytecode_roundtrip_mode.set(True)
    try:
        restored = test_engine(original)
        assert restored.program is not original
        assert next(iter(restored.program.temporaries.offsets)) is not next(iter(original.temporaries.offsets))
        check_engine(original, restored)
    finally:
        bytecode_roundtrip_mode.reset(token)
