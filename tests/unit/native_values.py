from typing import NamedTuple
from unittest import TestCase

from wirely.runtime.extensions import RuntimeNativeExtension
from wirely.compiler.extensions import CompilerNativeExtension
from wirely.generated import extension as builtin_extension
from wirely.runtime.extensions import NativeTypes
from wirely.runtime.signatures import Function as FunctionSignature, Int
import wirely
from wirely.compiler import compile_strs, standard_library_sources
from wirely.compiler import program as program_compiler
from wirely.runtime.engine import Engine
from tests.infra.support import test_extension, test_constant_function, test_contracts
from wirely.runtime.signatures import Builtin
from wirely.types.value_types import NativeEnum, NativeOpaque, NativeStruct, OpenIntEnum


checks = TestCase()


def check_fixture():
    engine.step()
    engine.assert_value_is('constant', 7)
    engine.assert_value_is('unwrap', 9)
    engine.assert_is_idle()


def identity(value, *, services):
    return value


def value_classes(contracts):
    """Return the native classes of check_values() and their resource types."""
    registry = {'initializers': {}, 'procedures': {}}

    class ValuesProbe(contracts.ValuesProbeCreate):
        def initialize(self, *, services, connection, number, seed, retained, missing, absent):
            assert missing is None
            assert absent == wirely.Some(None)
            assert number == seed
            assert retained == wirely.UInt16(20)
            self.seed = seed
            return contracts.ValuesProbeCreateResult(state=self)

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

            def run(self, *, services, state, connection, seed, **values):
                # Compile-time values are also passed in each step.
                assert seed == self.seed and values['block'] == 'hello'
                return contracts.ValuesProbeRunResult(number=self.seed)

        class Empty(contracts.ValuesProbeEmpty):
            def run(self, *, services):
                return contracts.ValuesProbeEmptyResult()

        class Omitted(contracts.ValuesProbeOmitted):
            def run(self, *, services):
                return contracts.ValuesProbeOmittedResult()
    registry['initializers']['TestLibrary.ValuesProbe:create'] = ValuesProbe
    registry['procedures']['TestLibrary.ValuesProbe:run'] = ValuesProbe.Run
    registry['procedures']['TestLibrary.ValuesProbe:empty'] = ValuesProbe.Empty
    registry['procedures']['TestLibrary.ValuesProbe:omitted'] = ValuesProbe.Omitted

    class Provider(contracts.ProviderCreate):
        def initialize(self, *, services):
            return contracts.ProviderCreateResult(service=self)
    registry['initializers']['TestLibrary.Provider:create'] = Provider

    return registry, {'TestLibrary.Service': Provider, 'TestLibrary.ValuesState': ValuesProbe}


def check_values():
    source = '''namespace TestLibrary {
  resource type Service;
  resource type ValuesState;
  node type Provider {
    default output: fixed Service;
    native initializer create writes default output as "service";
  }
  node type ValuesProbe {
    parameter Number: uint16;
    optional parameter Missing: boolean;
    optional parameter Absent: Option[boolean];
    parameter Default: boolean = true;
    parameter Type: type = uint16;
    constant Seed: uint16 = Example.Same(Number);
    constant Items: List[Option[uint16]] = [Some(7), None];
    constant At: Schedule.TimeOfDay = "00:30";
    constant Span: Std.Duration = "PT1S";
    constant Instant: Option[Timer.MonotonicTime] = None;
    constant Types: List[Option[type]] = [Some(uint16), None];
    input Connection: fixed Service;
    output Result: value uint16;
    binding state: fixed ValuesState;
    native initializer create
      reads Connection, Number, Seed as seed, Example.Same(20) as "retained",
        optional(Missing) as missing, optional(Absent) as Absent,
      writes state as "state",;
    native procedure run
      reads state as "state", Connection, Seed as "seed", optional(Missing) as Missing,
        optional(Missing) as alias, optional(Absent) as Absent, Default,
        Type as type, Example.Mode.On as mode, 12 as number,
        Example.Same(40) as call,
        { let text: string = "hello"; text } as block,
        Items as items, Example.Pair { value: true } as pair,
        () as unit, { let value: Option[boolean] = Some(false); value } as option,
        At as at, Span as span, Instant as instant, Types as descriptors,
      writes Result as Number;
    native procedure empty reads;
    native procedure omitted;
  }
}
namespace Example {
  native open enum type Mode as "TestLibrary.Mode": uint8 { On = 1, }
  native struct type Pair as "TestLibrary.Pair" { value: boolean, }
  native constant function Same as "TestLibrary.Identity"(number: uint16): uint16;
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
    compiler_bindings = CompilerNativeExtension(functions={'TestLibrary.Identity': test_constant_function('TestLibrary.Identity', identity, FunctionSignature((Int(16, False),), Int(16, False)))})
    enum_bindings = RuntimeNativeExtension(enums=enums)
    struct_bindings = RuntimeNativeExtension(structs=structs)
    graph = compile_strs([*standard_library_sources(['std', 'schedule', 'timer']), ('<values>', source)],
                         override_extensions=[enum_bindings, struct_bindings], compiler_override_extensions=[compiler_bindings])
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    with test_contracts(graph) as contracts:
        registry, resource_types = value_classes(contracts)
        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources=resource_types, contracts=contracts)
        runtime = Engine(program, override_extensions=[enum_bindings, struct_bindings, bindings, resource_bindings])
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
      node type OptionalProbe {{
        optional parameter Maybe: Option[boolean];
        optional parameter Type: type;
        parameter Default: Option[boolean] = None;
        native initializer create reads optional(Maybe) as Maybe,
          optional(Maybe) as "", optional(Type) as Type, Default;
      }}
    }}
    node type Forward {{
      optional parameter Maybe: Option[boolean];
      optional parameter Type: type;
      node child: TestLibrary.OptionalProbe {{
        Maybe?: optional(Maybe), Type?: optional(Type),
      }}
    }}
    node forward: Forward {{ {arguments} }}
    ''')])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
        with test_contracts(graph) as contracts:
            registry = {'initializers': {}, 'procedures': {}}

            # The empty key becomes the keyword argument _.
            class OptionalProbe(contracts.OptionalProbeCreate):
                def initialize(self, *, services, maybe, _, type, default):
                    self.values = {'Maybe': maybe, '': _, 'Type': type, 'Default': default}
                    return contracts.OptionalProbeCreateResult()
            registry['initializers']['TestLibrary.OptionalProbe:create'] = OptionalProbe

            bindings = test_extension(**registry, contracts=contracts)
            runtime = Engine(program, override_extensions=[bindings])
        values = runtime.get_native_state('forward.child.create').values
        assert values['Maybe'] == expected
        assert values[''] == expected
        assert values['Type'] == (None if expected_type is None else wirely.Some(expected_type))
        assert values['Default'] is None


def check_type_member_collision():
    # A namespace function named like a type member is ambiguous at the call.
    diagnostics = []
    compiler_bindings = CompilerNativeExtension(functions={'TestLibrary.WrappingAdd': test_constant_function('TestLibrary.WrappingAdd', identity, FunctionSignature((Int(16, True), Int(16, True)), Int(16, True)))})
    graph = compile_strs([('<collision>', '''namespace int16 {
      native constant function WrappingAdd as "TestLibrary.WrappingAdd"(a: int16, b: int16): int16;
    }
    constant Value: int16 = int16.WrappingAdd(1, 2);
    ''')], on_diagnostic=diagnostics.append, compiler_override_extensions=[compiler_bindings])
    assert graph is None
    assert any('`int16.WrappingAdd` is both a type member and a function' in diagnostic.message
               for diagnostic in diagnostics), diagnostics


def check_unknown_native_enum_values():
    # Library native code reports numbers its runtime does not know, for
    # example from a newer library declaring more members.
    from wirely.native.gpio import _setting
    from wirely.native.knx import _protocol_name
    from wirely.native.mqtt import _json_format
    from wirely.generated.enums import GPIOBias, KNXProtocol, MQTTFormat

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
    with checks.assertRaisesRegex(ValueError, 'node: procedure type `string` does not match state type `uint8`'):
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
    # Opaque structs convert with the from_fields and to_fields of their class.
    opaque_types = {'Test.Span': NativeOpaque((signatures.Int(128, False),), lambda: wirely.Duration)}
    native_types = NativeTypes(enums=enums, structs=structs, opaque_types=opaque_types)
    span = types.TypeStruct('Span', {'units': types.uint128}, native_name='Test.Span', is_opaque=True)
    mode = types.TypeEnum('Mode', {'On': 1}, is_open=True, underlying_type=types.uint8, native_name='Test.Mode')
    pair = types.TypeStruct('Pair', {'flag': types.boolean, 'count': types.uint8}, native_name='Test.Pair')
    concrete = [
        (types.int16, -3, wirely.Int16(-3)),
        (types.boolean, True, True),
        (types.String, 'text', 'text'),
        (types.unit, wirely.Unit(), wirely.Unit()),
        (span, (5,), wirely.Duration(ticks=5)),
        (types.ValueType, types.uint8, wirely.value_types.uint8),
        (mode, 1, Mode.On),
        (pair, (True, 7), Pair(True, wirely.UInt8(7))),
        (types.TypeList(types.TypeOption(types.TypeOption(types.uint8))), (None, wirely.Some(None), wirely.Some(wirely.Some(2))),
         (None, wirely.Some(None), wirely.Some(wirely.Some(wirely.UInt8(2))))),
    ]
    for value_type, internal, native in concrete:
        converted = to_native(None, value_type, internal, native_types=native_types)
        assert converted == native and type(converted) is type(native), (value_type, converted)
        assert from_native(None, value_type, native, native_types=native_types) == internal
    # Open enums keep numbers the runtime does not name.
    unknown = to_native(None, mode, 9, native_types=native_types)
    assert type(unknown) is Mode and unknown.name is None and from_native(None, mode, unknown, native_types=native_types) == 9

    # A type variable inside an option becomes a DynamicValue of the bound type.
    option = types.TypeOption(types.TypeOption(types.uint16))
    signature = signatures.Option(signatures.TypeVar('Type'))
    native = to_native(signature, option, wirely.Some(wirely.Some(4)), native_types=native_types)
    bound = wirely.value_types.option(wirely.value_types.uint16)
    assert native == wirely.Some(bound.from_native(wirely.Some(wirely.UInt16(4)), native_types=native_types))
    assert from_native(signature, option, native, native_types=native_types) == wirely.Some(wirely.Some(4))
    assert to_native(signature, option, wirely.Some(None), native_types=native_types) == wirely.Some(bound.from_native(None, native_types=native_types))
    assert to_native(signature, option, None, native_types=native_types) is None

    for signature, value_type, value, message in [
        (signature, option, wirely.Some(wirely.value_types.uint16.from_native(wirely.UInt16(4), native_types=native_types)),
         'in option: Expected a DynamicValue of `Option\\[uint16\\]`, got one of `uint16`'),
        (signature, option, wirely.Some(wirely.Some(wirely.UInt16(4))), 'in option: Expected a DynamicValue'),
        (None, pair, Pair(True, 7), "in field `count`: Expected a wirely.UInt8 for `uint8`, got 7"),
        (None, pair, (True, wirely.UInt8(7)), 'Expected a Pair for `Pair`'),
        (None, mode, 1, 'Expected a Mode for `Mode`'),
        (None, types.TypeList(types.boolean), [True], 'Expected a tuple'),
        (None, types.TypeList(types.boolean), (True, 1), 'in item 1: Expected a value of `boolean`'),
        (None, types.uint8, wirely.Int8(1), 'Expected a wirely.UInt8 for `uint8`'),
        (None, types.uint8, True, 'Expected a wirely.UInt8 for `uint8`'),
        (None, types.TypeOption(types.boolean), True, 'Expected None or wirely.Some'),
        (None, types.ValueType, types.uint8, 'Expected a wirely.ValueType'),
    ]:
        with checks.assertRaisesRegex(ValueError, message):
            from_native(signature, value_type, value, native_types=native_types)

    # Without their Python types, native enums and structs cannot be converted.
    for value_type, value in ((mode, 1), (pair, (True, 7))):
        with checks.assertRaisesRegex(ValueError, 'has no native representation'):
            to_native(None, value_type, value, native_types=builtin_extension.native_types)


check_fixture()
check_values()
check_optional_values()
check_type_member_collision()
check_unknown_native_enum_values()
check_state_types()
check_native_conversions()


async def run_test():
    pass
