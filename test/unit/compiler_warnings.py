from unittest.mock import patch

from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls


def warn_value(*, context):
    value = context.arguments['value']
    context.warn(f'Got {value}', argument='value')
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


check_native_warnings()


async def run_test():
    pass
