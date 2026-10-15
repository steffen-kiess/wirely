from unittest import TestCase
from unittest.mock import patch

from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls


checks = TestCase()


def warn_value(*, context):
    value, = context.arguments
    context.warn(f'Got {value}', argument=0)
    return value


def check_native_warnings():
    # Equal warnings from several instances appear once, traced to the first.
    source = '''namespace Example {
  native constant function Warn as "Example.Warn"(value: uint16): uint16;
}
node type Probe {
  parameter Number: uint16;
  constant Checked: uint16 = Example.Warn(Number);
  #[Allow(NativeFunction)]
  constant Allowed: uint16 = Example.Warn(Number);
}
node first: Probe { Number: 1 }
node second: Probe { Number: 1 }
node third: Probe { Number: 2 }
'''
    diagnostics = []
    with patch.dict(native_function_impls, {'Example.Warn': warn_value}):
        graph = compile_strs([('<warnings>', source)], on_diagnostic=diagnostics.append)
        assert graph is not None
        assert program_compiler.compile(graph, on_diagnostic=diagnostics.append) is not None
    assert [(diagnostic.kind, diagnostic.line, diagnostic.message) for diagnostic in diagnostics] == [
        ('warning', 6, 'Got 1 [NativeFunction]'),
        ('hint', 10, "While instantiating 'first'"),
        ('warning', 6, 'Got 2 [NativeFunction]'),
        ('hint', 12, "While instantiating 'third'"),
    ], diagnostics


def check_positional_diagnostics():
    source = '''namespace Example {
  native constant function Inspect(left: uint16, right: uint16): uint16;
  native constant function Empty(): uint16;
}
constant Value: uint16 = Example.Inspect(
  7,
  3,
);
constant Empty: uint16 = Example.Empty();
'''

    def empty(*, context):
        assert context.arguments == ()
        assert context.arguments_ast == ()
        return 0

    for kind in ('error', 'warning'):
        for index, line in ((None, 5), (0, 6), (1, 7)):
            def inspect(*, context):
                assert type(context.arguments) is tuple
                assert context.arguments == (7, 3)
                assert type(context.arguments_ast) is tuple
                assert len(context.arguments_ast) == 2
                for invalid in (-1, 2, True, 0.0, 'left'):
                    for report in (context.error, context.warn):
                        with checks.assertRaisesRegex(ValueError, 'Invalid function argument index'):
                            report('invalid', argument=invalid)
                if kind == 'error':
                    raise context.error('Selected argument', argument=index)
                context.warn('Selected argument', argument=index)
                return 4

            diagnostics = []
            with patch.dict(native_function_impls, {'Example.Inspect': inspect, 'Example.Empty': empty}):
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
