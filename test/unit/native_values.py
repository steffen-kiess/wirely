import contextlib
from dataclasses import FrozenInstanceError, replace
import io
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.compiler.parser import parse_str
from wirely.native import native_block_type


checks = TestCase()
with checks.assertRaisesRegex(TypeError, "unexpected keyword argument 'data_type'"):
    native_block_type({}, has_native_state=False, data_type=object)
engine.step()
engine.assert_value_is('constant', 7)
engine.assert_value_is('unwrap', 9)
engine.assert_is_idle()
assert not engine.program.instances['constant'].initializer_values

calls = []
registry = {}
contexts = []


def count(*, context):
    value = context.arguments['value']
    calls.append(value)
    return value


def fail(*, context):
    raise ValueError('binding failed')


@native_block_type(registry, has_native_state=True)
class ValuesProbe(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert self.values is context.values
        assert isinstance(context.values, MappingProxyType)
        assert list(self.values) == ['Number', 'seed', 'retained', 'missing', 'Absent']
        assert self.values['missing'] is None
        assert self.values['Absent'] == wirely.Some(None)
        assert self.values['Number'] == self.values['seed']
        assert self.values['retained'] == 20
        assert context.inputs['Number'] is self.inputs['Number']
        assert not hasattr(self, 'parameters')
        assert not hasattr(context, 'configuration')
        assert not hasattr(context, 'parameters')
        with checks.assertRaises(TypeError):
            context.values['Number'] = 1
        with checks.assertRaises(FrozenInstanceError):
            context.values = {}

    def run(self, *, context):
        contexts.append(context)
        assert not hasattr(context, 'configuration')
        assert not hasattr(context, 'parameters')
        assert list(context.values) == [
            'seed', 'Missing', 'alias', 'Absent', 'Default', 'type', 'mode',
            'number', 'call', 'block', 'items', 'pair', 'unit', 'option',
        ]
        assert context.values['seed'] == self.values['seed']
        assert context.values['Missing'] is context.values['alias'] is None
        assert context.values['Absent'] == wirely.Some(None)
        assert context.values['Default'] is True
        assert context.values['type'] == wirely.value_types.uint16
        assert context.values['mode'].name == 'On'
        assert context.values['number'] == 12
        assert context.values['call'] == 40
        assert context.values['block'] == 'hello'
        assert context.values['items'] == (wirely.Some(7), None)
        assert dict(context.values['pair']) == {'value': True}
        assert context.values['unit'] == wirely.Unit()
        assert context.values['option'] == wirely.Some(False)
        with checks.assertRaises(KeyError):
            context.values['Number']
        with checks.assertRaises(TypeError):
            context.values['seed'] = 1
        with checks.assertRaises(AttributeError):
            context.values = {}
        context.set_result('Number', context.values['seed'])

    def empty(self, *, context):
        assert not context.values
        with checks.assertRaises(KeyError):
            context.values['seed']


@native_block_type(registry, has_native_state=True)
class Provider(wirely.NativeState):
    pass


source = '''namespace TestLibrary {
  resource type Service;
  native node type Provider {
    default output: resource Service;
  }
  native node type ValuesProbe {
    parameter Number: uint16;
    optional parameter Missing: boolean;
    optional parameter Absent: Option[boolean];
    parameter Default: boolean = true;
    parameter Type: ValueType = uint16;
    constant Seed: uint16 = Example.Count(Number);
    constant Unused: uint16 = Example.Count(10);
    constant Items: List[Option[uint16]] = [Some(7), None];
    input Number: resource Service;
    output Result: value uint16;
    assert Example.Count(true), "checked";
    native initializer depends on Number,
      uses Number, Seed as seed, Example.Count(20) as "retained",
        parameter_option(Missing) as missing, parameter_option(Absent) as Absent,;
    native procedure run depends on Number, produces Result as Number,
      uses Seed as "seed", parameter_option(Missing) as Missing, parameter_option(Missing) as alias,
        parameter_option(Absent) as Absent, Default,
        Type as type, Example.Mode.On as mode, 12 as number,
        Example.Count(40) as call,
        { let text: String = "hello"; text } as block,
        Items as items, Example.Pair { value: true } as pair,
        () as unit, { let value: Option[boolean] = Some(false); value } as option,;
    native procedure empty uses;
    native procedure omitted;
  }
}
namespace Example {
  enum type Mode: uint8 { On = 1, }
  struct type Pair { value: boolean, }
  native function Count(value: uint16): uint16;
  native function Fail(): boolean;
}
node first: TestLibrary.ValuesProbe { Number: 7, Absent: None }
node second: TestLibrary.ValuesProbe { Number: 8, Absent: None }
node provider: TestLibrary.Provider {}
connect provider -> first.Number;
connect provider -> second.Number;
'''
# Use a boolean assertion with a separate signature to record its ordering.
source = source.replace('assert Example.Count(true)', 'assert Example.Check(true)')
source = source.replace('native function Fail()', 'native function Check(value: boolean): boolean;\n  native function Fail()')
functions = {('Example', 'Count'): count, ('Example', 'Check'): count,
             ('Example', 'Fail'): fail}
ValuesProbe.omitted = ValuesProbe.empty
with patch.dict(wirely.native.test_library.native_block_types, registry), \
        patch.dict(native_function_impls, functions):
    graph = compile_strs([('<values>', source)])
    assert graph is not None
    assert calls == []
    program = program_compiler.compile_nocatch(graph)
    assert calls == [7, 10, True, 20, 8, 10, True, 20, 40, 40]
    runtime = wirely.Engine(program)
    second_engine = wirely.Engine(program)
    runtime.step()
    runtime.assert_value_is('first.Result', 7)
    runtime.assert_value_is('second.Result', 8)
    runtime.assert_is_idle()
    runtime.trigger('first', 'run')
    runtime.step()
    second_engine.step()
    assert calls == [7, 10, True, 20, 8, 10, True, 20, 40, 40]

# Runtime model construction copies and freezes caller-owned mappings.
instance = program.instances['first']
assert not hasattr(instance, 'configuration')
assert not hasattr(ValuesProbe, 'native_block_data_type')
mutable = {'key': 1}
modified = replace(instance, initializer_values=mutable)
mutable['key'] = 2
assert modified.initializer_values['key'] == 1
procedure = next(proc for schedule in program.procs.values() for proc in schedule
                 if proc.node_id == 'first' and proc.reference[1] == 'run')
modified = replace(procedure, values=mutable)
mutable['key'] = 3
assert modified.values['key'] == 2
assert not procedure.dependencies
assert procedure.resources[0][0] == 'Number'

# Omitted and explicitly empty clauses retain distinct syntax nodes.
assert parse_str('native procedure run;', filename='<parse>')[0].uses is None
assert parse_str('native procedure run uses;', filename='<parse>')[0].uses.bindings == ()
assert parse_str('native initializer uses;', filename='<parse>')[0].uses.bindings == ()

# Binding failures carry their expression location and fail compilation.
for declaration, message in [
    ('uses Example.Fail() as failed', 'binding failed'),
    ('uses !Missing as expression', 'Unable to resolve value parameter'),
    ('uses { let value = Missing; value } as expression', 'Unable to resolve value parameter'),
]:
    with patch.dict(native_function_impls, functions):
        graph = compile_strs([('<binding-error>', f'''namespace Example {{
  native function Fail(): boolean;
}}
namespace Std {{
node type Constant {{
  optional parameter Missing: boolean;
  native procedure run {declaration};
}}
}}
node probe: Std.Constant {{}}
''')])
        assert graph is not None
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert program_compiler.compile(graph) is None
        assert message in output.getvalue(), output.getvalue()
        assert 'Internal compiler error' not in output.getvalue()

# A typed local annotation is included in inferred Wirely static usage.
graph = compile_strs([('<inferred-values>', '''node type Probe {
  parameter Type: ValueType = boolean;
  constant Flag: boolean = true;
  default output: value Type;
  procedure run produces default output as output {
    let local: Type = Flag;
    output = local;
  }
}
node probe: Probe {}
''')])
assert graph is not None
program_compiler.compile_nocatch(graph)
procedure = graph.blocks['probe'].block_procedures[0]
assert {item.name.id_str for item in procedure.static_usage} == {'Type', 'Flag'}


# Direct optional bindings also handle type values and forwarded omissions.
for arguments, expected in [
    ('', None), ('Maybe: None', wirely.Some(None)),
    ('Maybe: Some(true)', wirely.Some(wirely.Some(True))),
]:
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = compile_strs([('<optional-values>', f'''namespace TestLibrary {{
  native node type Provider {{
    optional parameter Maybe: Option[boolean];
    optional parameter Type: ValueType;
    parameter Default: Option[boolean] = None;
    native initializer uses parameter_option(Maybe) as Maybe,
      parameter_option(Maybe) as "", parameter_option(Type) as Type, Default;
  }}
}}
node type Forward {{
  optional parameter Maybe: Option[boolean];
  node child: TestLibrary.Provider {{ Maybe?: parameter_option(Maybe) }}
}}
node forward: Forward {{ {arguments} }}
''')])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
        values = program.instances['forward.child'].initializer_values
        assert values['Maybe'] == expected
        assert values[''] == expected
        assert values['Type'] is None
        assert values['Default'] is None
        assert 'Type' not in program.instances['forward.child'].parameters
        runtime = wirely.Engine(program)
        assert runtime.get_native_state('forward.child').values == values


# Singleton consumers evaluate once across aliases, independent of engines.
calls.clear()
with patch.dict(wirely.native.test_library.native_block_types, registry), \
        patch.dict(native_function_impls, functions):
    graph = compile_strs([('<singleton-values>', '''namespace Example {
  native function Count(value: uint16): uint16;
}
namespace TestLibrary {
  singleton native node type Provider {
    constant Shared: uint16 = Example.Count(1);
    native initializer uses Shared, Example.Count(2) as "initializer";
  }
}
node first: TestLibrary.Provider {}
node again: TestLibrary.Provider {}
''')])
    assert graph is not None
    assert not calls
    program = program_compiler.compile_nocatch(graph)
    assert calls == [1, 2]
    runtime = wirely.Engine(program)
    another = wirely.Engine(program)
    shared = runtime.get_native_state('first')
    assert shared is runtime.get_native_state('again')
    assert shared is not another.get_native_state('first')
    assert dict(shared.values) == {'Shared': 1, 'initializer': 2}
    assert calls == [1, 2]
