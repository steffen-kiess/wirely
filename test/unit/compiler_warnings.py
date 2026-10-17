from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.function_implementations import test_library
from wirely.test import test_constant_function


checks = TestCase()


def warn_value(value, *, services):
    services.warn(f'Got {value.value}', argument=0)
    return value


def check_native_warnings():
    # Equal warnings from several instances appear once, traced to the first.
    source = '''namespace TestLibrary {
  native constant function Warn as "TestLibrary.Warn"(value: uint16): uint16;
}
node type Probe {
  parameter Number: uint16;
  constant Checked: uint16 = TestLibrary.Warn(Number);
  #[Allow(NativeFunction)]
  constant Allowed: uint16 = TestLibrary.Warn(Number);
}
node first: Probe { Number: 1 }
node second: Probe { Number: 1 }
node third: Probe { Number: 2 }
'''
    diagnostics = []
    # Which instance is first follows declaration order, so do not shuffle.
    with patch.dict(test_library.native_functions, {'Warn': test_constant_function('TestLibrary.Warn', warn_value)}):
        graph = compile_strs([('<warnings>', source)], on_diagnostic=diagnostics.append, shuffle_seed=None)
        assert graph is not None
        assert program_compiler.compile(graph, on_diagnostic=diagnostics.append) is not None
    assert [(diagnostic.kind, diagnostic.line, diagnostic.message) for diagnostic in diagnostics] == [
        ('warning', 6, 'Got 1 [NativeFunction]'),
        ('hint', 10, "While instantiating 'first'"),
        ('warning', 6, 'Got 2 [NativeFunction]'),
        ('hint', 12, "While instantiating 'third'"),
    ], diagnostics


def check_positional_diagnostics():
    source = '''namespace TestLibrary {
  native constant function Inspect(left: uint16, right: uint16): uint16;
  native constant function Empty(): uint16;
}
constant Value: uint16 = TestLibrary.Inspect(
  7,
  3,
);
constant Empty: uint16 = TestLibrary.Empty();
'''

    def empty(*, services):
        with checks.assertRaisesRegex(ValueError, 'Invalid function argument index'):
            services.warn('invalid', argument=0)
        return wirely.UInt16(0)

    for kind in ('error', 'warning'):
        for index, line in ((None, 5), (0, 6), (1, 7)):
            def inspect(left, right, *, services):
                assert (left, right) == (wirely.UInt16(7), wirely.UInt16(3))
                assert services.function_name == 'TestLibrary.Inspect'
                for invalid in (-1, 2, True, 0.0, 'left'):
                    for report in (services.error, services.warn):
                        with checks.assertRaisesRegex(ValueError, 'Invalid function argument index'):
                            report('invalid', argument=invalid)
                if kind == 'error':
                    raise services.error('Selected argument', argument=index)
                services.warn('Selected argument', argument=index)
                return wirely.UInt16(4)

            diagnostics = []
            functions = {'Inspect': test_constant_function('TestLibrary.Inspect', inspect),
                         'Empty': test_constant_function('TestLibrary.Empty', empty)}
            with patch.dict(test_library.native_functions, functions):
                graph = compile_strs([('<positional>', source)], on_diagnostic=diagnostics.append)
                assert graph is not None
                program = program_compiler.compile(graph, on_diagnostic=diagnostics.append)
            assert (program is None) == (kind == 'error')
            selected = [item for item in diagnostics if item.kind == kind]
            assert len(selected) == 1, diagnostics
            assert selected[0].line == line, diagnostics
            if index is not None:
                assert source[selected[0].start:selected[0].end] == ('7', '3')[index]


check_native_warnings()
check_positional_diagnostics()


async def run_test():
    pass
