"""Load failure diagnostics, execution timing, and instruction validation."""

from dataclasses import replace
from unittest import TestCase

import wirely.compiler
from wirely.program import types
from wirely.compiler import program as program_compiler
from wirely.program import (
    BranchTarget, Constant, ConstOperand, InputSource, Instruction, Opcode, Operand, OperandKind,
    Procedure, ProcedureBody, Program, RunStage, VariableBank, VariableTable, validate_program,
)
from wirely.program import SectionList
from wirely.program.loader import load_program
from wirely.program.writer import write_program
from wirely.runtime import LoadFailure
from wirely.runtime.engine import Engine
from wirely.runtime.extensions import NativeTypes
from tests.infra.support import test_contracts, test_extension


checks = TestCase()


def compile_program(source, filename='<load-checks>'):
    graph = wirely.compiler.compile_strs([(filename, source)])
    assert graph is not None
    return graph, program_compiler.compile_nocatch(graph)


def check_failure_messages():
    for source, message in [
            ('load assert false;', 'Load-time assertion failed'),
            ('procedure check stage load { load fail; }', 'Load failed'),
            ('load assert false, "Literal {{braces}}";', 'Literal {braces}'),
            ('load assert false, r#"Two values: {}, {}"#, 3, true;', 'Two values: 3, true'),
            ('procedure check stage load { load fail "Empty message follows"; }', 'Empty message follows'),
            ('load assert false, "";', ''),
            ('load assert false, "{}", Some(3);', 'Some(3)'),
            ('''struct type Pair { enabled: boolean, number: uint8, }
                load assert false, "{}", Pair { enabled: true, number: 3 };''',
             'Pair { enabled: true, number: 3 }'),
    ]:
        _, program = compile_program(source)
        with checks.assertRaises(LoadFailure) as caught:
            Engine(program)
        failure = caught.exception
        assert failure.message == message, (failure.message, message)
        assert failure.node == '<global>'
        assert failure.instruction_index >= 0
        assert failure.procedure

    _, program = compile_program('''procedure check stage load {
      let mut number: uint8 = 3;
      load fail "{} / {}", number, { number = 9; number };
    }''')
    with checks.assertRaises(LoadFailure) as caught:
        Engine(program)
    assert caught.exception.message == '3 / 9'
    assert tuple(value.to_text(native_types=NativeTypes()) for value in caught.exception.values) == ('3', '9')

    # UnwrapOr evaluates its fallback even for Some; a failed fallback has no
    # successful path on which the procedure would need to assign its output.
    _, program = compile_program('''binding result: fixed uint8;
    procedure check stage load writes result {
      let option: Option[uint8] = Some(3);
      let selected = option.UnwrapOr({ load fail "Eager fallback"; 0 });
    }
    load assert result == 3;
    ''')
    with checks.assertRaises(LoadFailure) as caught:
        Engine(program)
    assert caught.exception.message == 'Eager fallback'


def check_successful_paths():
    _, program = compile_program('''node type Example {
    output result: fixed uint8;
    procedure compute stage load writes result {
      let mut value: uint8 = 0;
      load assert true, "{}", { value = 9; value };
      load assert true, "{}", { load fail "Must not run"; 1 };
      if value != 0 { load fail "Diagnostic arguments ran"; }
      let selected: uint8;
      if value == 0 { selected = 7; } else { load fail; }
      let other = if value == 0 { selected } else { load fail; };
      let reverse = if value != 0 { load fail; } else { other };
      let option: Option[uint8] = Some(reverse);
      let chosen = if let Some(number) = option { number } else { load fail; };
      result = chosen;
    }
    load assert result == 7, "Expected 7, received {}", result;
    }
    node example: Example {}
    node type Unused { load assert false; }
    ''')
    engine = Engine(program)
    assert engine.get_value('example.result') == 7
    engine.step()
    engine.step()
    assert engine.get_value('example.result') == 7


def check_instances():
    _, program = compile_program('''node type Checked {
      parameter Ready: boolean;
      load assert Ready, "Not ready";
    }
    node first: Checked { Ready: true }
    node second: Checked { Ready: false }
    ''')
    with checks.assertRaises(LoadFailure) as caught:
        Engine(program)
    assert caught.exception.node == 'second'
    assert '<load assert <load-checks>:' in caught.exception.procedure

    for source, owner in [
        ('load assert false;', '<global>'),
        ('''namespace Lib {
          singleton node type Checked {
            load assert false;
          }
        }
        node checked: Lib.Checked {}
        ''', 'Lib.Checked:'),
    ]:
        _, program = compile_program(source, filename='example.source:case.wrl')
        with checks.assertRaises(LoadFailure) as caught:
            Engine(program)
        assert caught.exception.node == owner
        assert 'example.source:case.wrl' in caught.exception.procedure
        container = SectionList.from_bytes(write_program(program))
        container.sections = [section for section in container.sections if section.section_type < 0x10000]
        loaded = load_program(container.to_bytes())
        with checks.assertRaises(LoadFailure) as caught:
            Engine(loaded)
        failure = caught.exception
        assert failure.procedure is None and failure.node is None
        assert failure.procedure_index == 0
        assert failure.procedure_instance is loaded.procs[RunStage.LOAD][0]
        assert 'procedure[0]' in str(failure) and 'None' not in str(failure)


def check_loading_stops():
    graph, program = compile_program('''namespace TestLibrary {
      node type Probe {
        output Ready: fixed boolean;
        binding checked: fixed boolean;
        native procedure load stage load writes Ready as "ready";
        procedure check stage load reads Ready writes checked {
          load assert Ready, "Rejected";
          checked = true;
        }
        native procedure later stage load reads checked as "checked";
        native procedure run;
      }
    }
    node probe: TestLibrary.Probe {}
    ''')
    calls = []
    with test_contracts(graph) as contracts:
        class ProbeLoad(contracts.ProbeLoad):
            def load(self, *, services):
                calls.append('load')
                return contracts.ProbeLoadResult(ready=False)

        class ProbeLater(contracts.ProbeLater):
            def load(self, *, services, checked):
                calls.append('later')
                return contracts.ProbeLaterResult()

        class ProbeRun(contracts.ProbeRun):
            def prepare(self, *, services):
                calls.append('prepare')

            def run(self, *, services):
                return contracts.ProbeRunResult()

        extension = test_extension(contracts=contracts, procedures={
            'TestLibrary.Probe:load': ProbeLoad,
            'TestLibrary.Probe:later': ProbeLater,
            'TestLibrary.Probe:run': ProbeRun,
        })
        with checks.assertRaises(LoadFailure):
            Engine(program, override_extensions=[extension])
    assert calls == ['load']


def make_program(instructions, *, local_types=()):
    procedure = Procedure(name='test.check', inputs=(), outputs=(), body=ProcedureBody(instructions, local_types))
    return Program(
        state_variables=VariableTable((), 'state'),
        temporaries=VariableTable((), 'temporaries'), initial_state={},
        procs={RunStage.LOAD: (procedure,)},
    )


def check_validation():
    format_constant = Constant(types.TypeList(types.String), ('Value ', '', ''))
    number = Constant(types.uint8, 7)
    value = ConstOperand(number)
    fail = Instruction(Opcode.FAIL, (format_constant, 1, value))
    program = make_program((fail,))
    validate_program(program)
    with checks.assertRaises(LoadFailure) as caught:
        Engine(program)
    assert caught.exception.message == 'Value 7'

    def fail_with(parts):
        return Instruction(Opcode.FAIL, (Constant(types.TypeList(types.String), parts), 1, value))

    invalid = [
        (make_program((Instruction(Opcode.FAIL),)), 'Invalid operands'),
        (make_program((Instruction(Opcode.FAIL, (format_constant, -1)),)), 'Invalid operands'),
        (make_program((Instruction(Opcode.FAIL, (format_constant, 0xffffffff)),)), 'Invalid operands'),
        (make_program((Instruction(Opcode.FAIL, (format_constant, True)),)), 'Invalid operands'),
        (make_program((Instruction(Opcode.FAIL, (False, 0)),)), 'Invalid operands'),
        (make_program((Instruction(Opcode.FAIL, (0, 0)),)), 'Invalid operands'),
        (make_program((Instruction(Opcode.FAIL, (value, 1, value)),)), 'Invalid operands'),
        (make_program((Instruction(Opcode.FAIL, (number, 1, value)),)), 'List[string]'),
        (make_program((fail_with(('only one',)),)), 'must contain 3 strings'),
        (make_program((fail_with(('', 'x', '')),)), 'Unsupported diagnostic format'),
        (make_program((Instruction(Opcode.COPY, (Operand(OperandKind.LOCAL, 0), ConstOperand(format_constant))),
                       Instruction(Opcode.RETURN)), local_types=(types.uint8,)),
         'resolved runtime type'),
        (make_program((Instruction(Opcode.FAIL, (format_constant, 1, ConstOperand(format_constant))),)),
         'resolved runtime type'),
    ]
    for malformed, message in invalid:
        with checks.assertRaisesRegex(ValueError, message.replace('[', r'\[').replace(']', r'\]')):
            validate_program(malformed)
    # Constants verify their values when created, storing lists as tuples.
    with checks.assertRaisesRegex(ValueError, r'Invalid `List\[string\]` value'):
        fail_with(('', 7, ''))
    assert Constant(types.TypeList(types.uint8), [1, 2]).value == (1, 2)

    procedure = program.procs[RunStage.LOAD][0]
    for stage in (RunStage.INPUT, RunStage.DEFAULT, RunStage.OUTPUT):
        with checks.assertRaisesRegex(ValueError, 'only allowed in load procedures'):
            validate_program(replace(program, procs={stage: (procedure,)}))
    # A shared body must be valid at every use site, including an unreachable FAIL.
    with checks.assertRaisesRegex(ValueError, 'only allowed in load procedures'):
        validate_program(replace(program, procs={RunStage.LOAD: (procedure,), RunStage.DEFAULT: (procedure,)}))
    with checks.assertRaisesRegex(ValueError, 'Unsupported diagnostic format'):
        validate_program(make_program((Instruction(Opcode.RETURN), fail_with(('', 'x', '')))))
    validate_program(make_program((Instruction(Opcode.JUMP, (BranchTarget(1),)), fail)))
    with checks.assertRaisesRegex(ValueError, 'end in RETURN or FAIL'):
        validate_program(make_program(()))
    with checks.assertRaisesRegex(ValueError, 'resolved runtime type'):
        validate_program(replace(program, procs={RunStage.LOAD: (replace(
            procedure, inputs=(InputSource(VariableBank.CONST_VALUES, constant=format_constant),)),)}))


async def run_test():
    check_failure_messages()
    check_successful_paths()
    check_instances()
    check_loading_stops()
    check_validation()
