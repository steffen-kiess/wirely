from dataclasses import dataclass
from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs, model as blocks
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.compiler.native_configuration import register_configuration
import wirely.native.test_library
from wirely.native import native_block_type


engine.step()
assert set(engine.program.instances) == {
    '<global>', 'enabled', 'disabled', 'nested', 'nested.child', 'Shared',
}
assert not engine.program.variables
assert all(not procedures for procedures in engine.program.procs.values())


def compile_program(source):
    graph = compile_strs([('<functions>', declarations), ('<assertions>', source)])
    assert graph is not None
    return program_compiler.compile_nocatch(graph)


calls = []


def record(*, context):
    calls.append(context.arguments['argument'])
    return True


def fail(*, context):
    raise ValueError('condition failed')


def arithmetic_fail(*, context):
    raise ZeroDivisionError('division failed')


def unexpected_fail(*, context):
    raise RuntimeError('unexpected failure')


def positioned_fail(*, context):
    raise context.error('argument failed', argument='argument')


declarations = """native function Record(argument: int16): boolean;
native function Fail(): boolean;
native function ArithmeticFail(): boolean;
native function UnexpectedFail(): boolean;
native function PositionedFail(argument: boolean): boolean;
"""
functions = {
    ('Record',): record, ('Fail',): fail, ('ArithmeticFail',): arithmetic_fail,
    ('UnexpectedFail',): unexpected_fail, ('PositionedFail',): positioned_fail,
}
with patch.dict(native_function_impls, functions):
    program = compile_program('''assert Record(1);
constant Root: boolean = Record(0);
node type Checked {
  parameter Number: int16;
  assert Cached;
  assert Record(Number);
  assert Record(3);
  constant Cached: boolean = Record(2);
}
node type Unused {
  assert Fail();
  assert false;
}
singleton node type Shared {
  assert Record(6);
}
node first: Checked { Number: 4 }
node second: Checked { Number: 5 }
node shared: Shared {}
node repeated: Shared {}
''')
    assert calls == [0, 1, 2, 4, 3, 2, 5, 3, 6], calls
    runtime = wirely.Engine(program)
    runtime.step()
    runtime.step()
    assert calls == [0, 1, 2, 4, 3, 2, 5, 3, 6]

    compile_program('''assert if true { true } else { Fail() };
assert true || Fail();
assert !(false && Fail());
''')
    # An assertion alone must cause root instantiation.
    compile_program('assert Record(7);')
    assert calls[-1] == 7

    for condition, message in [('false', 'Compile-time assertion failed'),
                               ('Fail()', 'condition failed'),
                               ('ArithmeticFail()', 'division failed')]:
        calls.clear()
        try:
            compile_program(f'assert {condition}; assert Record(8);')
        except blocks.ProgramError as error:
            assert not isinstance(error, blocks.InternalCompilerError)
            assert str(error) == message
            assert error.source_element.source_position.start_line == 1
            assert not error.hints
        else:
            raise AssertionError('Expected assertion failure')
        assert calls == []

    for source, message, column in [
        ('assert false, "decoded\\nmessage";',
         'Compile-time assertion failed: decoded\nmessage', 1),
        ('assert PositionedFail(true);', 'argument failed', 23),
    ]:
        try:
            compile_program(source)
        except blocks.ProgramError as error:
            assert str(error) == message, str(error)
            assert error.source_element.source_position.meta.column == column
        else:
            raise AssertionError('Expected positioned error')

    try:
        compile_program('assert UnexpectedFail();')
    except blocks.InternalCompilerError as error:
        assert isinstance(error.inner_exception, RuntimeError)
    else:
        raise AssertionError('Expected internal error')


# Native preparation follows all constants and assertions for that instance.
@dataclass(frozen=True)
class AssertionConfiguration:
    pass


registry = {}
prepared = []


def prepare(*, context):
    assert calls == [10, 11] * (len(prepared) + 1)
    prepared.append(context.name)
    return AssertionConfiguration()


register_configuration(AssertionConfiguration, prepare)


@native_block_type(registry, has_native_state=True, data_type=AssertionConfiguration)
class NativeAssertionProbe(wirely.NativeState):
    pass


@native_block_type(registry, has_native_state=True)
class NativeAssertionSingleton(wirely.NativeState):
    pass


calls.clear()
with patch.dict(native_function_impls, functions), patch.dict(
        wirely.native.test_library.native_block_types, registry):
    program = compile_program('''namespace TestLibrary {
  native node type NativeAssertionProbe {
    constant Cached: boolean = Record(10);
    assert Cached;
    assert Record(11);
  }
  singleton native node type NativeAssertionSingleton {
    assert Record(12);
  }
}
node first: TestLibrary.NativeAssertionProbe {}
node second: TestLibrary.NativeAssertionProbe {}
node shared: TestLibrary.NativeAssertionSingleton {}
node repeated: TestLibrary.NativeAssertionSingleton {}
''')
    assert prepared == ['first', 'second']
    assert calls == [10, 11, 10, 11, 12]
    runtime = wirely.Engine(program)
    runtime.step()
    assert calls == [10, 11, 10, 11, 12]

    calls.clear()
    try:
        compile_program('''namespace TestLibrary {
  native node type NativeAssertionProbe {
    constant Cached: boolean = Record(10);
    assert Fail();
  }
}
node failed: TestLibrary.NativeAssertionProbe {}
''')
    except blocks.ProgramError as error:
        assert str(error) == 'condition failed'
        assert error.hints[0].source_element.source_position.start_line == 7
        assert str(error.hints[0]) == "While instantiating 'failed'"
    else:
        raise AssertionError('Expected failure before preparation')
    assert calls == [10]
    assert prepared == ['first', 'second']
