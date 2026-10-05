import datetime
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.native import native_block_type


checks = TestCase()


def check_fixture():
    engine.step()
    engine.assert_value_is('constant', 7)
    engine.assert_value_is('unwrap', 9)
    engine.assert_is_idle()


registry = {}


def identity(*, context):
    return context.arguments['value']


@native_block_type(registry, has_native_state=True)
class ValuesProbe(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert self.values['missing'] is None
        assert self.values['Absent'] == wirely.Some(None)
        assert self.values['Number'] == self.values['seed']
        assert self.values['retained'] == 20
        with checks.assertRaises(TypeError):
            context.values['Number'] = 1

    def run(self, *, context):
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
        assert context.values['at'] == datetime.time(0, 30)
        assert context.values['span'] == wirely.Duration.parse('PT1S')
        assert context.values['instant'] == wirely.MonotonicTime.create_invalid()
        assert context.values['descriptors'] == (wirely.Some(wirely.value_types.uint16), None)
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

    omitted = empty


@native_block_type(registry, has_native_state=True)
class Provider(wirely.NativeState):
    pass


def check_values():
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
    constant At: TimeOfDay = "00:30";
    constant Span: Duration = "PT1S";
    constant Instant: MonotonicTime = MonotonicTime.Invalid;
    constant Types: List[Option[ValueType]] = [Some(uint16), None];
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
        () as unit, { let value: Option[boolean] = Some(false); value } as option,
        At as at, Span as span, Instant as instant, Types as descriptors;
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
    with patch.dict(wirely.native.test_library.native_block_types, registry), \
            patch.dict(native_function_impls, {('Example', 'Identity'): identity}):
        graph = compile_strs([('<values>', source)])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
        runtime = wirely.Engine(program)
        runtime.step()
        runtime.assert_value_is('first.Result', 7)
        runtime.assert_value_is('second.Result', 8)
        runtime.assert_is_idle()


def check_optional_values():
    # Direct optional bindings also handle type values and forwarded omissions.
    for arguments, expected, expected_type in [
        ('', None, None), ('Maybe: None', wirely.Some(None), None),
        ('Maybe: Some(true), Type: uint16', wirely.Some(wirely.Some(True)), wirely.value_types.uint16),
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
      optional parameter Type: ValueType;
      node child: TestLibrary.Provider {{
        Maybe?: parameter_option(Maybe), Type?: parameter_option(Type),
      }}
    }}
    node forward: Forward {{ {arguments} }}
    ''')])
            assert graph is not None
            program = program_compiler.compile_nocatch(graph)
            runtime = wirely.Engine(program)
            values = runtime.get_native_state('forward.child').values
            assert values['Maybe'] == expected
            assert values[''] == expected
            assert values['Type'] == (None if expected_type is None else wirely.Some(expected_type))
            assert values['Default'] is None


check_fixture()
check_values()
check_optional_values()
