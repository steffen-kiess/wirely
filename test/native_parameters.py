import datetime
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from textwrap import indent
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type


checks = TestCase()
types = wirely.value_types
engine.step()
engine.assert_value_is('defaults.value.', 7)
engine.assert_value_is('defaults.wrapped.', datetime.time(0, 30))
engine.assert_value_is('defaults.items.', (wirely.Some(7), None))
engine.assert_value_is('overridden.value.', -12)
engine.assert_value_is('overridden.wrapped.', datetime.time(1, 30))
engine.assert_value_is('forwarded.child.value.', 42)
engine.assert_value_is('generic.child.value.', wirely.Some((1, 2)))
engine.assert_value_is('fallback.Value', True)

registry = {}
prepared = []
prepared_names = {}
seen = []
callback = None


class PreparedData:
    @staticmethod
    def create(*, context):
        assert isinstance(context, wirely.NativeNodeConfigurationContext)
        assert isinstance(context.parameters, MappingProxyType)
        assert '' not in context.parameters
        prepared_names[id(context.parameters)] = context.name
        prepared.append(context.parameters)
        return context.parameters


@native_block_type(registry, has_native_state=False, data_type=PreparedData)
class ParameterProbe:
    @staticmethod
    def run(*, context):
        assert context.name == prepared_names[id(context.parameters)]
        callback(context)
        seen.append(context.parameters)


@native_block_type(registry, has_native_state=True, data_type=PreparedData)
class StateParameterProbe(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert self.parameters is context.parameters
        assert any(self.context.configuration is data for data in prepared)
        assert '' not in self.parameters

    def run(self, *, context):
        assert self.parameters is context.parameters
        assert context.name == self.context.name
        assert context.name == prepared_names[id(context.parameters)]
        callback(context)
        seen.append(context.parameters)


def make_probe(declaration, check, *, native=False, singleton=False, arguments='', extra=''):
    global callback
    callback = check
    class_name = 'StateParameterProbe' if native else 'ParameterProbe'
    specifier = ('singleton ' if singleton else '') + ('native ' if native else '')
    source = f'''
namespace TestLibrary {{
  {specifier}node type {class_name} {{
{indent(declaration.strip(), "    ")}
  }}
}}
node probe : TestLibrary.{class_name}{arguments};
{extra}
'''
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', Path('wirely/lib/std.wrl').read_text()),
            ('<native-parameters>', source),
        ])
        assert graph is not None
        program = wirely.Program.compile(graph)
        assert program is not None
        result = wirely.Engine(program)
    result.step()
    return result


def check_parameters(context):
    assert isinstance(context.parameters, Mapping)
    assert isinstance(context.parameters, MappingProxyType)
    assert dict(context.parameters) == {
        'Type': types.uint16,
        'Number': 7,
        'Flag': True,
        'Text': 'hello',
        'Unit': wirely.Unit(),
        'Present': wirely.Some(wirely.Unit()),
        'Absent': None,
        'Items': (wirely.Some(7), None),
        'At': datetime.time(0, 30),
        'Duration': wirely.Duration.parse('PT1S'),
        'Mode': wirely.enum.ThermostatSystemMode.Cool,
    }
    assert 'Missing' not in context.parameters
    with checks.assertRaises(KeyError):
        context.parameters['Missing']
    with checks.assertRaises(TypeError):
        context.parameters['Number'] = 8


parameter_declarations = '''
parameter Type : ValueType = uint16;
parameter Number : Type = 7;
parameter Flag : boolean = true;
parameter Text : String = "hello";
parameter Unit : unit = ();
parameter Present : Option[unit] = Some(());
optional parameter Absent : Option[unit];
optional parameter Missing : boolean;
parameter Items : List[Option[Type]] = [Some(Number), None];
parameter At : TimeOfDay = TimeOfDay_AddWrap("23:30", "01:00");
parameter Duration : Duration = "PT1S";
parameter Mode : ThermostatSystemModeEnum = ThermostatSystemModeEnum.Cool;
native procedure run;
'''
for native in [False, True]:
    probe = make_probe(parameter_declarations, check_parameters,
                       native=native, arguments='(Absent = None)')
    assert prepared[-1]['Number'] == 7
    assert prepared[-1]['Type'] == types.uint16
    probe.step()
    probe.assert_is_idle()


def check_composite(context):
    first = context.parameters['First']
    second = context.parameters['Second']
    assert first == second
    assert hash(first) == hash(second)
    assert str(first) == 'Option[List[uint16]]'
    assert len({first, second, types.uint16}) == 2
    assert first != types.uint16
    assert first != wirely.types.TypeOption(wirely.types.TypeList(wirely.types.uint16))
    assert first.validate(wirely.Some([True, 7])) == wirely.Some((1, 7))
    assert first.validate(None) is None
    assert not first.supports_json and not first.supports_csv
    for invalid in [[7], wirely.Some(['invalid'])]:
        with checks.assertRaises(ValueError):
            first.validate(invalid)
    for operation in [lambda: first.json_encode(None),
                      lambda: first.json_decode(None),
                      lambda: first.to_csv_value(None),
                      lambda: first.parse('7')]:
        with checks.assertRaises(ValueError):
            operation()
    with checks.assertRaises(FrozenInstanceError):
        first._type = wirely.types.boolean
    for name in ['contains_parameters', 'resolve_self', 'parse_value', 'inner_type']:
        assert not hasattr(first, name)


make_probe('''
parameter First : ValueType = Option[List[uint16]];
parameter Second : ValueType = Option[List[uint16]];
native procedure run;
''', check_composite)


def check_optional_descriptor(context):
    descriptor = context.parameters['Type']
    assert descriptor.parse('none') is None
    assert descriptor.parse('7') == wirely.Some(7)
    assert descriptor.supports_csv
    assert descriptor.to_csv_value(None) == ('', False)
    assert descriptor.to_csv_value(wirely.Some(7)) == ('7', False)
    with checks.assertRaises(ValueError):
        descriptor.to_csv_value(wirely.Some('invalid'))


make_probe('''
parameter Type : ValueType = Option[uint16];
native procedure run;
''', check_optional_descriptor)

make_probe('native procedure run;',
           lambda context: checks.assertEqual(dict(context.parameters), {}),
           native=True, singleton=True)

nested = make_probe('native procedure run;',
                    lambda context: checks.assertEqual(dict(context.parameters), {}),
                    native=True, extra='''
node type Outer {
  node child : TestLibrary.StateParameterProbe;
}
node outer : Outer;
namespace TestLibrary {
  singleton node type SharedContainer {
    node child : StateParameterProbe;
  }
}
node shared_container : TestLibrary.SharedContainer;
node shared_again : TestLibrary.SharedContainer;
''')
assert nested.get_native_state('outer.child').context.name == 'outer.child'
assert nested.get_native_state('probe').context.name == 'probe'
shared_path = nested.get_block_path('shared_container')
shared_child = nested.native_states[shared_path + (shared_path[0].blocks['child'],)]
assert shared_child.context.name == 'TestLibrary.SharedContainer.child'
assert shared_path == nested.get_block_path('shared_again')


def check_type_state(context):
    previous = context.inputs['previous']
    assert isinstance(previous, wirely.ValueType)
    context.set_result('selected', types.uint8)
    context.set_result('output', previous)


probe = make_probe('''
state selected : ValueType = uint16;
implicit output : value ValueType;
native procedure run
  depends on selected.old as "previous"
  produces selected, implicit output as "output";
''', check_type_state)
probe.assert_value_is('probe', wirely.types.uint16)
probe.step()
probe.assert_value_is('probe', wirely.types.uint8)
probe.assert_is_idle()


def check_type_values(context):
    expected = (wirely.Some(types.uint16), None)
    assert context.parameters['Values'] == expected
    assert context.inputs['input'] == expected
    descriptor = context.parameters['Type']
    assert descriptor.validate(expected) == expected
    supplied = list(expected)
    assert descriptor.validate(supplied) == expected
    with checks.assertRaises(ValueError):
        descriptor.validate([wirely.Some(wirely.types.uint16)])
    with checks.assertRaises(ValueError):
        context.set_result('output', [wirely.Some(wirely.types.uint16)])
    context.set_result('output', supplied)
    supplied.append(wirely.Some(types.uint8))
    assert context.parameters['Values'] == expected


probe = make_probe('''
parameter Type : ValueType = List[Option[ValueType]];
parameter Values : Type = [Some(uint16), None];
implicit input : value Type;
implicit output : value Type;
native procedure run
  depends on implicit input as "input"
  produces implicit output as "output";
''', check_type_values, extra='''
node source : Std.Constant(
  Type = List[Option[ValueType]],
  Value = [Some(uint16), None]
);
connect source -> probe;
''')
probe.assert_value_is('probe', (wirely.Some(wirely.types.uint16), None))

for name, descriptor in vars(types).items():
    if isinstance(descriptor, wirely.ValueType):
        assert str(descriptor)
        assert isinstance(descriptor.supports_json, bool)
        assert isinstance(descriptor.supports_csv, bool)

assert types.unit.validate(wirely.Unit()) == wirely.Unit()
assert types.unit.parse('()') == wirely.Unit()
assert types.uint16.validate(True) == 1
assert type(types.uint16.validate(True)) is int
assert types.uint16.parse('65535') == 65535
assert types.uint16.json_encode(42) == 42
assert types.uint16.json_decode(42) == 42
assert types.uint16.to_csv_value(42) == ('42', False)
assert types.boolean.json_encode(True) is True
assert types.boolean.json_decode(False) is False
assert types.boolean.to_csv_value(True) == ('true', False)
assert types.string.json_encode('hello') == 'hello'
assert types.string.json_decode('hello') == 'hello'
assert types.duration.parse('PT1S') == wirely.Duration.parse('PT1S')
assert types.time_of_day.parse('12:34') == datetime.time(12, 34)
assert types.value_type.validate(types.uint16) == types.uint16
assert types.thermostat_system_mode.json_encode(
    wirely.enum.ThermostatSystemMode.EmergencyHeat, 'SnakeCase',
) == 'emergency_heat'
assert types.thermostat_system_mode.json_decode('Cool') == wirely.enum.ThermostatSystemMode.Cool

for operation in [lambda: types.unit.validate(None),
                  lambda: types.uint16.parse('65536'),
                  lambda: types.uint16.json_encode('invalid'),
                  lambda: types.uint16.json_decode('invalid'),
                  lambda: types.uint16.json_encode(42, 'invalid'),
                  lambda: types.uint16.json_decode(42, 'invalid'),
                  lambda: types.uint16.to_csv_value('invalid'),
                  lambda: types.unit.json_encode(wirely.Unit()),
                  lambda: types.unit.json_decode(None),
                  lambda: types.unit.to_csv_value(wirely.Unit()),
                  lambda: types.value_type.validate(wirely.types.uint16),
                  lambda: types.thermostat_system_mode.json_decode('invalid')]:
    with checks.assertRaises(ValueError):
        operation()
