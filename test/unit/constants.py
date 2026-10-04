import contextlib
from dataclasses import dataclass
import io
from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.compiler.native_configuration import register_configuration
import wirely.native.test_library
from wirely.native import native_block_type


engine.step()
for name, expected in [('enabled', 12), ('disabled', 0), ('nested.child', 0)]:
    engine.assert_value_is(name, expected)
    engine.assert_value_is(name + '.Initial', expected)
    engine.assert_value_is(name + '.child', expected)
    engine.assert_value_is(name + '.compared', expected == 12)
    engine.assert_value_is(name + '.selected', expected)
    engine.assert_value_is(name + '.SameUnit', True)
    engine.assert_value_is(name + '.items', wirely.Some(12) if expected else None)
    pair = engine.get_value(name + '.PairCopy')
    assert pair['number'] == expected
    assert pair['enabled'] is (expected == 12)
    captured = engine.program.instances[name + '.captured'].parameters
    assert captured['Text'] == 'hello'
    assert captured['Items'] == (wirely.Some(expected), None)
    assert dict(engine.program.instances[name].parameters) == {
        'Enabled': expected == 12, 'Default': expected,
    }
assert engine.program.instances['generic.child'].parameters['Value'] == wirely.Some((7, 8))
assert engine.program.instances['typed_list.child'].parameters['Value'] == (wirely.Some(12), None)
assert engine.program.instances['type_value.child'].parameters['Value'] == wirely.value_types.uint8
assert dict(engine.get_value('typed_struct')) == {'enabled': True, 'number': 12}
engine.assert_value_is('root_child', 12)
engine.assert_value_is('root_result', 12)
engine.assert_value_is('shared', True)
assert engine.get_variable('shared') is engine.get_variable('shared_again')
assert not engine.program.instances['Shared'].parameters


def compile_program(source):
    graph = compile_strs([('<functions>', declarations), ('<constants>', source)])
    assert graph is not None
    program = program_compiler.compile(graph)
    assert program is not None
    return program


calls = []


def count(*, context):
    result = context.arguments['argument']
    calls.append(result)
    return result


def fail(*, context):
    raise ValueError('initializer failed')


declarations = """native function Count(argument: boolean): boolean;
native function Fail(): boolean;
"""
functions = {('Count',): count, ('Fail',): fail}
with patch.dict(native_function_impls, functions):
    program = compile_program('''constant Root: boolean = Count(true);
node type Probe {
  parameter Enabled: boolean;
  constant Selected: boolean = Count(Enabled);
  constant Unused: boolean = Count(false);
  constant Branch: boolean = if Enabled { Selected } else { false };
  parameter Default: boolean = Selected;
  default output: value boolean;
  procedure run produces default output as output {
    output = Selected && Branch && Default;
  }
}
node type Unused {
  constant Value: boolean = Fail();
}
singleton node type Shared {
  constant Unused: boolean = Count(true);
}
node first: Probe { Enabled: true }
node second: Probe { Enabled: false }
node shared: Shared {}
node repeated: Shared {}
''')
    assert calls == [True, True, False, False, False, True], calls
    runtime = wirely.Engine(program)
    runtime.step()
    runtime.assert_value_is('first', True)
    runtime.assert_value_is('second', False)
    assert calls == [True, True, False, False, False, True]

    compile_program('''constant If: boolean = if true { true } else { Fail() };
constant And: boolean = false && Fail();
constant Or: boolean = true || Fail();
''')
    # Even an unused constant in a program with no runtime members evaluates.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        graph = compile_strs([('<functions>', declarations),
                              ('<constants>', 'constant Bad: boolean = Fail();')])
        assert graph is not None
        assert program_compiler.compile(graph) is None
    assert 'initializer failed' in output.getvalue()
    assert 'Internal compiler error' not in output.getvalue()


# Constants resolve before native configuration but only parameters reach Python.
@dataclass(frozen=True)
class PreparedConstants:
    enabled: bool


registry = {}
prepared = []


def prepare(*, context):
    assert len(calls) == len(prepared) + 1
    assert dict(context.parameters) == {'Enabled': True}
    prepared.append(context.name)
    return PreparedConstants(context.parameters['Enabled'])


register_configuration(PreparedConstants, prepare)


@native_block_type(registry, has_native_state=True, data_type=PreparedConstants)
class NativeConstantProbe(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert dict(context.values) == {'Enabled': True}
        assert context.configuration.enabled is True


@native_block_type(registry, has_native_state=True)
class NativeConstantSingleton(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert not context.values


calls.clear()
with patch.dict(native_function_impls, functions), patch.dict(
        wirely.native.test_library.native_block_types, registry):
    native = compile_program('''namespace TestLibrary {
  native node type NativeConstantProbe {
    parameter Enabled: boolean;
    constant Unused: boolean = Count(Enabled);
    native initializer uses Enabled;
  }
  singleton native node type NativeConstantSingleton {
    constant Unused: boolean = Count(false);
  }
}
node type Forward {
  constant Flag: boolean = true;
  node child: TestLibrary.NativeConstantProbe { Enabled: Flag }
}
node first: Forward {}
node second: Forward {}
node shared: TestLibrary.NativeConstantSingleton {}
node repeated: TestLibrary.NativeConstantSingleton {}
''')
    assert calls == [True, True, False]
    native_engine = wirely.Engine(native)
    assert calls == [True, True, False]
    assert prepared == ['first.child', 'second.child']
