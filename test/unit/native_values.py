from typing import NamedTuple
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.native import native_node_type, native_procedure
from wirely.runtime.engine import Engine
from wirely.test import test_contracts
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


def identity(*, context):
    value, = context.arguments
    return value


def value_classes(contracts):
    """Return the native classes of check_values() and their resource types."""
    registry = {}

    @native_node_type(registry, has_native_state=True)
    class ValuesProbe(contracts.ValuesProbeInitializer):
        def initialize(self, *, services, connection, number, seed, retained, missing, absent):
            assert missing is None
            assert absent == wirely.Some(None)
            assert number == seed
            assert retained == wirely.UInt16(20)
            self.seed = seed
            return contracts.ValuesProbeInitializerResult(state=self)

        @native_procedure('run')
        class Run(contracts.ValuesProbeRun):
            def prepare(self, *, services, state, connection, seed, missing, alias, absent, default, type, mode,
                        number, call, block, items, pair, unit, option, at, span, instant, descriptors):
                assert seed == state.seed
                assert missing is alias is None
                assert absent == wirely.Some(None)
                assert default is True
                assert type == wirely.value_types.uint16
                assert mode.name == 'On'
                assert number == wirely.Int32(12)
                assert call == wirely.UInt16(40)
                assert block == 'hello'
                assert items == (wirely.Some(wirely.UInt16(7)), None)
                assert pair.value is True
                assert unit == wirely.Unit()
                assert option == wirely.Some(False)
                assert at == wirely.TimeOfDay.from_parts(0, 30)
                assert span == wirely.Duration.parse('PT1S')
                assert instant is None
                assert descriptors == (wirely.Some(wirely.value_types.uint16), None)
                self.seed = seed

            def run(self, *, services, state, connection):
                return contracts.ValuesProbeRunResult(number=self.seed)

        @native_procedure('empty')
        class Empty(contracts.ValuesProbeEmpty):
            def run(self, *, services):
                return contracts.ValuesProbeEmptyResult()

        @native_procedure('omitted')
        class Omitted(contracts.ValuesProbeOmitted):
            def run(self, *, services):
                return contracts.ValuesProbeOmittedResult()

    @native_node_type(registry, has_native_state=True)
    class Provider(contracts.ProviderInitializer):
        def initialize(self, *, services):
            return contracts.ProviderInitializerResult(service=self)

    return registry, {'Service': Provider, 'ValuesState': ValuesProbe}


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
    class Mode(OpenIntEnum, minimum=0, maximum=255):
        On = 1

    class Pair(NamedTuple):
        value: bool

    enums = {'TestLibrary.Mode': NativeEnum(Mode, is_open=True, underlying_type='uint8')}
    structs = {'TestLibrary.Pair': NativeStruct(Pair, (Builtin('boolean'),))}
    with patch.dict(native_function_impls, {'Example.Identity': identity}), \
            patch.dict(wirely.runtime.enums.native_enum_types, enums), \
            patch.dict(wirely.runtime.structs.native_struct_types, structs):
        graph = compile_strs([('<values>', source)])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
        with test_contracts(graph) as contracts:
            registry, resource_types = value_classes(contracts)
            with patch.dict(wirely.native.test_library.native_node_types, registry), \
                    patch.dict(wirely.native.test_library.native_resource_types, resource_types):
                runtime = Engine(program)
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
        with test_contracts(graph) as contracts:
            registry = {}

            # The empty key becomes the keyword argument _.
            @native_node_type(registry, has_native_state=True)
            class OptionalProbe(contracts.OptionalProbeInitializer):
                def initialize(self, *, services, maybe, _, type, default):
                    self.values = {'Maybe': maybe, '': _, 'Type': type, 'Default': default}
                    return contracts.OptionalProbeInitializerResult()

            with patch.dict(wirely.native.test_library.native_node_types, registry):
                runtime = Engine(program)
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
    # Library native code reports numbers its runtime does not know, for
    # example from a newer library declaring more members.
    from wirely.native.gpio import _setting
    from wirely.native.knx import _protocol_name
    from wirely.native.mqtt import _json_format
    from wirely.runtime.enums import GPIOBias, KNXProtocol, MQTTFormat

    assert _protocol_name(wirely.Some(KNXProtocol(1))) == 'TCP' and _protocol_name(None) is None
    assert _json_format(wirely.Some(MQTTFormat(2))) == 'SnakeCase'
    assert _setting(GPIOBias(1), {GPIOBias.AsIs: 'as is'}, 'bias') == 'as is'
    for call, message in ((lambda: _protocol_name(wirely.Some(KNXProtocol(9))), 'Unsupported KNX protocol 9'),
                          (lambda: _json_format(wirely.Some(MQTTFormat(7))), 'Unsupported MQTT format 7'),
                          (lambda: _setting(GPIOBias(7), {GPIOBias.AsIs: 'as is'}, 'bias'), 'Unsupported GPIO bias 7')):
        with checks.assertRaisesRegex(ValueError, message):
            call()


def check_state_types():
    # Procedures of generic native state types require the state's type.
    from wirely.native import check_state_type

    check_state_type('node', wirely.value_types.uint8, wirely.value_types.uint8)
    with checks.assertRaisesRegex(ValueError, 'node: procedure type String does not match state type uint8'):
        check_state_type('node', wirely.value_types.string, wirely.value_types.uint8)


def check_native_conversions():
    # Values convert recursively between internal and native representations;
    # type variable positions of a signature become DynamicValues.
    from wirely import types
    from wirely.runtime import signatures
    from wirely.runtime.native_values import from_native, to_native

    class Mode(OpenIntEnum, minimum=0, maximum=255):
        On = 1

    class Pair(NamedTuple):
        flag: bool
        count: wirely.UInt8

    enums = {'Test.Mode': NativeEnum(Mode, is_open=True, underlying_type='uint8')}
    structs = {'Test.Pair': NativeStruct(Pair, (Builtin('boolean'), signatures.Int(8, False)))}
    with patch.dict(wirely.runtime.enums.native_enum_types, enums), \
            patch.dict(wirely.runtime.structs.native_struct_types, structs):
        mode = types.TypeEnum('Mode', {'On': 1}, is_open=True, underlying_type=types.uint8, native_name='Test.Mode')
        pair = types.TypeStruct('Pair', {'flag': types.boolean, 'count': types.uint8}, native_name='Test.Pair')
        concrete = [
            (types.int16, -3, wirely.Int16(-3)),
            (types.boolean, True, True),
            (types.String, 'text', 'text'),
            (types.unit, wirely.Unit(), wirely.Unit()),
            (types.Duration, wirely.Duration(ticks=5), wirely.Duration(ticks=5)),
            (types.ValueType, types.uint8, wirely.value_types.uint8),
            (mode, 1, Mode.On),
            (pair, (True, 7), Pair(True, wirely.UInt8(7))),
            (types.TypeList(types.TypeOption(types.TypeOption(types.uint8))), (None, wirely.Some(None), wirely.Some(wirely.Some(2))),
             (None, wirely.Some(None), wirely.Some(wirely.Some(wirely.UInt8(2))))),
        ]
        for value_type, internal, native in concrete:
            converted = to_native(None, value_type, internal)
            assert converted == native and type(converted) is type(native), (value_type, converted)
            assert from_native(None, value_type, native) == internal
        # Open enums keep numbers the runtime does not name.
        unknown = to_native(None, mode, 9)
        assert type(unknown) is Mode and unknown.name is None and from_native(None, mode, unknown) == 9

        # A type variable inside an option becomes a DynamicValue of the bound type.
        option = types.TypeOption(types.TypeOption(types.uint16))
        signature = signatures.Option(signatures.TypeVar('Type'))
        native = to_native(signature, option, wirely.Some(wirely.Some(4)))
        bound = wirely.value_types.option(wirely.value_types.uint16)
        assert native == wirely.Some(bound.from_native(wirely.Some(wirely.UInt16(4))))
        assert from_native(signature, option, native) == wirely.Some(wirely.Some(4))
        assert to_native(signature, option, wirely.Some(None)) == wirely.Some(bound.from_native(None))
        assert to_native(signature, option, None) is None

        for signature, value_type, value, message in [
            (signature, option, wirely.Some(wirely.value_types.uint16.from_native(wirely.UInt16(4))),
             'in option: Expected a DynamicValue of Option\\[uint16\\], got one of uint16'),
            (signature, option, wirely.Some(wirely.Some(wirely.UInt16(4))), 'in option: Expected a DynamicValue'),
            (None, pair, Pair(True, 7), "in field 'count': Expected a wirely.UInt8 for uint8, got 7"),
            (None, pair, (True, wirely.UInt8(7)), 'Expected a Pair for Pair'),
            (None, mode, 1, 'Expected a Mode for Mode'),
            (None, types.TypeList(types.boolean), [True], 'Expected a tuple'),
            (None, types.TypeList(types.boolean), (True, 1), 'in item 1: Expected a value of boolean'),
            (None, types.uint8, wirely.Int8(1), 'Expected a wirely.UInt8 for uint8'),
            (None, types.uint8, True, 'Expected a wirely.UInt8 for uint8'),
            (None, types.TypeOption(types.boolean), True, 'Expected None or wirely.Some'),
            (None, types.ValueType, types.uint8, 'Expected a wirely.ValueType'),
        ]:
            with checks.assertRaisesRegex(ValueError, message):
                from_native(signature, value_type, value)

    # Without their Python types, native enums and structs cannot be converted.
    for value_type, value in ((mode, 1), (pair, (True, 7))):
        with checks.assertRaisesRegex(ValueError, 'has no native representation'):
            to_native(None, value_type, value)


check_fixture()
check_values()
check_optional_values()
check_type_member_collision()
check_unknown_native_enum_values()
check_state_types()
check_native_conversions()


async def run_test():
    pass
