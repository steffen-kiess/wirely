from typing import NamedTuple
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.native import native_node_type
from wirely.runtime.contexts import NativeState
from wirely.test import test_engine
from wirely.runtime.signatures import Builtin
from wirely.types.value_types import NativeEnum, NativeStruct, OpenIntEnum
import wirely.runtime.enums
import wirely.runtime.structs


checks = TestCase()


def check_fixture():
    engine.step()
    engine.assert_value_is('constant', 7)
    engine.assert_value_is('unwrap', 9)
    engine.assert_is_idle()


registry = {}


def identity(*, context):
    value, = context.arguments
    return value


@native_node_type(registry, has_native_state=True)
class ValuesProbe(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)
        assert self.values['missing'] is None
        assert self.values['Absent'] == wirely.Some(None)
        assert self.values['Number'] == self.values['seed']
        assert self.values['retained'] == 20
        with checks.assertRaises(TypeError):
            context.values['Number'] = 1

    @staticmethod
    def run(*, context):
        assert context.values['seed'] == context.inputs['state'].object.values['seed']
        assert context.values['Missing'] is context.values['alias'] is None
        assert context.values['Absent'] == wirely.Some(None)
        assert context.values['Default'] is True
        assert context.values['type'] == wirely.value_types.uint16
        assert context.values['mode'].name == 'On'
        assert context.values['number'] == 12
        assert context.values['call'] == 40
        assert context.values['block'] == 'hello'
        assert context.values['items'] == (wirely.Some(7), None)
        pair = context.values['pair']
        assert (pair.type.name, pair.type.fields[0][0], pair.values) == ('Example.Pair', 'value', (True,))
        assert context.values['unit'] == wirely.Unit()
        assert context.values['option'] == wirely.Some(False)
        assert context.values['at'] == wirely.TimeOfDay.from_parts(0, 30)
        assert context.values['span'] == wirely.Duration.parse('PT1S')
        assert context.values['instant'] is None
        assert context.values['descriptors'] == (wirely.Some(wirely.value_types.uint16), None)
        with checks.assertRaises(KeyError):
            context.values['Number']
        with checks.assertRaises(TypeError):
            context.values['seed'] = 1
        with checks.assertRaises(AttributeError):
            context.values = {}
        context.set_output('Number', context.values['seed'])

    @staticmethod
    def empty(*, context):
        assert not context.values
        with checks.assertRaises(KeyError):
            context.values['seed']

    omitted = empty


@native_node_type(registry, has_native_state=True)
class Provider(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('service', self)


@native_node_type(registry, has_native_state=True)
class OptionalProbe(NativeState):
    pass


def check_values():
    source = '''namespace TestLibrary {
  resource type Service;
  resource type ValuesState;
  native node type Provider {
    default output: resource Service;
    native initializer produces default output as "service";
  }
  native node type ValuesProbe {
    parameter Number: uint16;
    optional parameter Missing: boolean;
    optional parameter Absent: Option[boolean];
    parameter Default: boolean = true;
    parameter Type: ValueType = uint16;
    constant Seed: uint16 = Example.Same(Number);
    constant Items: List[Option[uint16]] = [Some(7), None];
    constant At: TimeOfDay = "00:30";
    constant Span: Duration = "PT1S";
    constant Instant: Option[MonotonicTime] = None;
    constant Types: List[Option[ValueType]] = [Some(uint16), None];
    input Connection: resource Service;
    output Result: value uint16;
    binding state: resource ValuesState;
    native initializer depends on Connection, produces state as "state",
      uses Number, Seed as seed, Example.Same(20) as "retained",
        optional(Missing) as missing, optional(Absent) as Absent,;
    native procedure run depends on state as "state", Connection, produces Result as Number,
      uses Seed as "seed", optional(Missing) as Missing, optional(Missing) as alias,
        optional(Absent) as Absent, Default,
        Type as type, Example.Mode.On as mode, 12 as number,
        Example.Same(40) as call,
        { let text: String = "hello"; text } as block,
        Items as items, Example.Pair { value: true } as pair,
        () as unit, { let value: Option[boolean] = Some(false); value } as option,
        At as at, Span as span, Instant as instant, Types as descriptors;
    native procedure empty uses;
    native procedure omitted;
  }
}
namespace Example {
  native open enum type Mode as "TestLibrary.Mode": uint8 { On = 1, }
  native struct type Pair as "TestLibrary.Pair" { value: boolean, }
  native constant function Same as "Example.Identity"(number: uint16): uint16;
}
node first: TestLibrary.ValuesProbe { Number: 7, Absent: None }
node second: TestLibrary.ValuesProbe { Number: 8, Absent: None }
node provider: TestLibrary.Provider {}
connect provider -> first.Connection;
connect provider -> second.Connection;
'''
    resource_types = {'Service': Provider, 'ValuesState': ValuesProbe}
    class Mode(OpenIntEnum, minimum=0, maximum=255):
        On = 1

    class Pair(NamedTuple):
        value: bool

    enums = {'TestLibrary.Mode': NativeEnum(Mode, is_open=True, underlying_type='uint8')}
    structs = {'TestLibrary.Pair': NativeStruct(Pair, (Builtin('boolean'),))}
    with patch.dict(wirely.native.test_library.native_node_types, registry), \
            patch.dict(wirely.native.test_library.native_resource_types, resource_types), \
            patch.dict(native_function_impls, {'Example.Identity': identity}), \
            patch.dict(wirely.runtime.enums.native_enum_types, enums), \
            patch.dict(wirely.runtime.structs.native_struct_types, structs):
        graph = compile_strs([('<values>', source)])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
        runtime = test_engine(program)
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
        with patch.dict(wirely.native.test_library.native_node_types, registry):
            graph = compile_strs([('<optional-values>', f'''namespace TestLibrary {{
      native node type OptionalProbe {{
        optional parameter Maybe: Option[boolean];
        optional parameter Type: ValueType;
        parameter Default: Option[boolean] = None;
        native initializer uses optional(Maybe) as Maybe,
          optional(Maybe) as "", optional(Type) as Type, Default;
      }}
    }}
    node type Forward {{
      optional parameter Maybe: Option[boolean];
      optional parameter Type: ValueType;
      node child: TestLibrary.OptionalProbe {{
        Maybe?: optional(Maybe), Type?: optional(Type),
      }}
    }}
    node forward: Forward {{ {arguments} }}
    ''')])
            assert graph is not None
            program = program_compiler.compile_nocatch(graph)
            runtime = test_engine(program)
            values = runtime.get_native_state('forward.child').values
            assert values['Maybe'] == expected
            assert values[''] == expected
            assert values['Type'] == (None if expected_type is None else wirely.Some(expected_type))
            assert values['Default'] is None



def check_type_member_collision():
    # A namespace function named like a type member is ambiguous at the call.
    diagnostics = []
    with patch.dict(native_function_impls, {'int16.WrappingAdd': identity}):
        graph = compile_strs([('<collision>', '''namespace int16 {
      native constant function WrappingAdd(a: int16, b: int16): int16;
    }
    constant Value: int16 = int16.WrappingAdd(1, 2);
    ''')], on_diagnostic=diagnostics.append)
    assert graph is None
    assert any('int16.WrappingAdd is both a type member and a function' in diagnostic.message
               for diagnostic in diagnostics), diagnostics


def check_unknown_native_enum_values():
    # Library native code reports numbers its runtime does not know.
    from wirely.native.gpio import _setting
    from wirely.native.knx import _protocol_name
    from wirely.native.mqtt import _json_format
    from wirely.runtime.enums import GPIOBias

    def value(name, members):
        enum_type = wirely.types.TypeEnum(name, members, is_open=True, underlying_type=wirely.types.uint8,
                                          native_name=name).enum_type
        return lambda number: enum_type.from_value(number)

    protocol = value('KNX.Protocol', {'TCP': 1, 'WebSocket': 9})
    json_format = value('MQTT.Format', {'Default': 1})
    bias = value('GPIO.Bias', {'AsIs': 1})
    assert _protocol_name(wirely.Some(protocol(1))) == 'TCP' and _protocol_name(None) is None
    assert _json_format(wirely.Some(json_format(2))) == 'SnakeCase'
    assert _setting(bias(1), {GPIOBias.AsIs: 'as is'}, 'bias') == 'as is'
    for call, message in ((lambda: _protocol_name(wirely.Some(protocol(9))), 'Unsupported KNX protocol 9'),
                          (lambda: _json_format(wirely.Some(json_format(7))), 'Unsupported MQTT format 7'),
                          (lambda: _setting(bias(7), {GPIOBias.AsIs: 'as is'}, 'bias'), 'Unsupported GPIO bias 7')):
        with checks.assertRaisesRegex(ValueError, message):
            call()


def check_state_types():
    # Procedures of generic native state types require the state's type.
    from types import SimpleNamespace
    from wirely.native import check_state_type

    state = SimpleNamespace(values={'Type': wirely.value_types.uint8})
    check_state_type(SimpleNamespace(name='node', values={'Type': wirely.value_types.uint8}), state)
    with checks.assertRaisesRegex(ValueError, 'node: procedure type String does not match state type uint8'):
        check_state_type(SimpleNamespace(name='node', values={'Type': wirely.value_types.string}), state)


check_fixture()
check_values()
check_optional_values()
check_type_member_collision()
check_unknown_native_enum_values()
check_state_types()


async def run_test():
    pass
