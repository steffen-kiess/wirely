from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.native import native_block_type


checks = TestCase()
with checks.assertRaisesRegex(TypeError, "unexpected keyword argument 'data_type'"):
    native_block_type({}, has_native_state=False, data_type=object)
engine.step()
engine.assert_value_is('constant', 7)
engine.assert_value_is('unwrap', 9)
engine.assert_is_idle()

registry = {}
contexts = []


def identity(*, context):
    return context.arguments['value']


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
    constant Seed: uint16 = Example.Identity(Number);
    constant Items: List[Option[uint16]] = [Some(7), None];
    input Number: resource Service;
    output Result: value uint16;
    native initializer depends on Number,
      uses Number, Seed as seed, Example.Identity(20) as "retained",
        parameter_option(Missing) as missing, parameter_option(Absent) as Absent,;
    native procedure run depends on Number, produces Result as Number,
      uses Seed as "seed", parameter_option(Missing) as Missing, parameter_option(Missing) as alias,
        parameter_option(Absent) as Absent, Default,
        Type as type, Example.Mode.On as mode, 12 as number,
        Example.Identity(40) as call,
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
  native function Identity(value: uint16): uint16;
}
node first: TestLibrary.ValuesProbe { Number: 7, Absent: None }
node second: TestLibrary.ValuesProbe { Number: 8, Absent: None }
node provider: TestLibrary.Provider {}
connect provider -> first.Number;
connect provider -> second.Number;
'''
ValuesProbe.omitted = ValuesProbe.empty
with patch.dict(wirely.native.test_library.native_block_types, registry), \
        patch.dict(native_function_impls, {('Example', 'Identity'): identity}):
    graph = compile_strs([('<values>', source)])
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    runtime = wirely.Engine(program)
    second_engine = wirely.Engine(program)
    runtime.step()
    runtime.assert_value_is('first.Result', 7)
    runtime.assert_value_is('second.Result', 8)
    runtime.assert_is_idle()
    runtime.trigger('first', 'run')
    runtime.step()
    second_engine.step()

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
        runtime = wirely.Engine(program)
        values = runtime.get_native_state('forward.child').values
        assert values['Maybe'] == expected
        assert values[''] == expected
        assert values['Type'] is None
        assert values['Default'] is None


# Singleton aliases share state within each engine.
with patch.dict(wirely.native.test_library.native_block_types, registry), \
        patch.dict(native_function_impls, {('Example', 'Identity'): identity}):
    graph = compile_strs([('<singleton-values>', '''namespace Example {
  native function Identity(value: uint16): uint16;
}
namespace TestLibrary {
  singleton native node type Provider {
    constant Shared: uint16 = Example.Identity(1);
    native initializer uses Shared, Example.Identity(2) as "initializer";
  }
}
node first: TestLibrary.Provider {}
node again: TestLibrary.Provider {}
''')])
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    runtime = wirely.Engine(program)
    another = wirely.Engine(program)
    shared = runtime.get_native_state('first')
    assert shared is runtime.get_native_state('again')
    assert shared is not another.get_native_state('first')
    assert dict(shared.values) == {'Shared': 1, 'initializer': 2}
