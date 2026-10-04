import datetime
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from textwrap import indent
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type
from wirely.compiler.native_configuration import register_configuration
from wirely.compiler import model as compiler_model


checks = TestCase()
types = wirely.value_types
mode_type = engine.program.instances['thermostat'].parameters['Type']
engine.step()
engine.assert_value_is('defaults.value.', 7)
engine.assert_value_is('defaults.wrapped.', datetime.time(0, 30))
assert engine.program.instances['defaults'].parameters['Items'] == (wirely.Some(7), None)
engine.assert_value_is('overridden.value.', -12)
engine.assert_value_is('overridden.wrapped.', datetime.time(1, 30))
engine.assert_value_is('forwarded.child.value.', 42)
assert engine.program.instances['generic.child'].parameters['Value'] == wirely.Some((1, 2))
engine.assert_value_is('fallback.Value', True)

registry = {}
prepared = []
prepared_names = {}
seen = []
callback = None


class PreparedData:
    pass


def prepare_prepared_data(*, context):
    assert isinstance(context, compiler_model.NativeNodeConfigurationContext)
    assert isinstance(context.parameters, MappingProxyType)
    assert '' not in context.parameters
    prepared_names[context.name] = context.parameters
    prepared.append(context.parameters)
    return context.parameters


register_configuration(PreparedData, prepare_prepared_data)


@native_block_type(registry, has_native_state=False, data_type=PreparedData)
class ParameterProbe:
    @staticmethod
    def run(*, context):
        assert {key: (value.value if key in ('Absent', 'Missing') else value)
                for key, value in context.values.items()
                if key not in ('Absent', 'Missing') or value is not None} == prepared_names[context.name]
        callback(context)
        seen.append(context.values)


@native_block_type(registry, has_native_state=True, data_type=PreparedData)
class StateParameterProbe(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert self.values is context.values
        assert any(self.context.configuration == data for data in prepared)
        assert '' not in self.values

    def run(self, *, context):
        assert self.values == context.values
        assert self.values is not context.values
        assert context.name == self.context.name
        assert {key: (value.value if key in ('Absent', 'Missing') else value)
                for key, value in context.values.items()
                if key not in ('Absent', 'Missing') or value is not None} == prepared_names[context.name]
        callback(context)
        seen.append(context.values)


def make_probe(declaration, check, *, native=False, singleton=False, arguments='', extra=''):
    global callback
    callback = check
    class_name = 'StateParameterProbe' if native else 'ParameterProbe'
    specifier = ('singleton ' if singleton else '') + ('native ' if native else '')
    if native:
        clause = declaration.split('native procedure run', 1)[1].split(';', 1)[0]
        declaration += '\nnative initializer' + clause + ';'
    source = f'''
namespace TestLibrary {{
  {specifier}node type {class_name} {{
{indent(declaration.strip(), "    ")}
  }}
}}
node probe: TestLibrary.{class_name} {{ {arguments} }}
{extra}
'''
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', Path('wirely/lib/std.wrl').read_text()),
            ('wirely/lib/matter.wrl', Path('wirely/lib/matter.wrl').read_text()),
            ('<native-parameters>', source),
        ])
        assert graph is not None
        program = program_compiler.compile(graph)
        assert program is not None
        result = wirely.Engine(program)
    result.step()
    return result


def check_parameters(context):
    assert isinstance(context.values, Mapping)
    assert isinstance(context.values, MappingProxyType)
    assert dict(context.values) == {
        'Type': types.uint16,
        'Number': 7,
        'Flag': True,
        'Text': 'hello',
        'Unit': wirely.Unit(),
        'Present': wirely.Some(wirely.Unit()),
        'Absent': wirely.Some(None),
        'Items': (wirely.Some(7), None),
        'At': datetime.time(0, 30),
        'Duration': wirely.Duration.parse('PT1S'),
        'ModeType': context.values['ModeType'],
        'Mode': context.values['ModeType'].parse('Cool'),
        'Missing': None,
    }
    assert context.values['Missing'] is None
    with checks.assertRaises(KeyError):
        context.values['Undeclared']
    with checks.assertRaises(TypeError):
        context.values['Number'] = 8


parameter_declarations = '''
parameter Type: ValueType = uint16;
parameter Number: Type = 7;
parameter Flag: boolean = true;
parameter Text: String = "hello";
parameter Unit: unit = ();
parameter Present: Option[unit] = Some(());
optional parameter Absent: Option[unit];
optional parameter Missing: boolean;
parameter Items: List[Option[Type]] = [Some(Number), None];
parameter At: TimeOfDay = TimeOfDay.AddWrap("23:30", "01:00");
parameter Duration: Duration = "PT1S";
parameter ModeType: ValueType = ThermostatSystemModeEnum;
parameter Mode: ThermostatSystemModeEnum = ThermostatSystemModeEnum.Cool;
native procedure run uses Type, Number, Flag, Text, Unit, Present, parameter_option(Absent) as Absent,
  parameter_option(Missing) as Missing, Items, At, Duration, ModeType, Mode;
'''
for native in [False, True]:
    probe = make_probe(parameter_declarations, check_parameters,
                       native=native, arguments='Absent: None')
    assert prepared[-1]['Number'] == 7
    assert prepared[-1]['Type'] == types.uint16
    probe.step()
    probe.assert_is_idle()


def check_composite(context):
    first = context.values['First']
    second = context.values['Second']
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
parameter First: ValueType = Option[List[uint16]];
parameter Second: ValueType = Option[List[uint16]];
native procedure run uses First, Second;
''', check_composite)


def check_optional_descriptor(context):
    descriptor = context.values['Type']
    assert descriptor.parse('none') is None
    assert descriptor.parse('7') == wirely.Some(7)
    assert descriptor.supports_csv
    assert descriptor.to_csv_value(None) == ('', False)
    assert descriptor.to_csv_value(wirely.Some(7)) == ('7', False)
    with checks.assertRaises(ValueError):
        descriptor.to_csv_value(wirely.Some('invalid'))


make_probe('''
parameter Type: ValueType = Option[uint16];
native procedure run uses Type;
''', check_optional_descriptor)

make_probe('native procedure run;',
           lambda context: checks.assertEqual(dict(context.values), {}),
           native=True, singleton=True)

nested = make_probe('native procedure run;',
                    lambda context: checks.assertEqual(dict(context.values), {}),
                    native=True, extra='''
node type Outer {
  node child: TestLibrary.StateParameterProbe {}
}
node outer: Outer {}
namespace TestLibrary {
  singleton node type SharedContainer {
    node child: StateParameterProbe {}
  }
}
node shared_container: TestLibrary.SharedContainer {}
node shared_again: TestLibrary.SharedContainer {}
''')
assert nested.get_native_state('outer.child').context.name == 'outer.child'
assert nested.get_native_state('probe').context.name == 'probe'
shared_path = nested.get_block_path('shared_container')
shared_child = nested.get_native_state('shared_container.child')
assert shared_child.context.name == 'TestLibrary.SharedContainer.child'
assert shared_path == nested.get_block_path('shared_again')


def check_type_values(context):
    expected = (wirely.Some(types.uint16), None)
    assert context.values['Values'] == expected
    descriptor = context.values['Type']
    assert descriptor.validate(expected) == expected
    supplied = list(expected)
    assert descriptor.validate(supplied) == expected
    with checks.assertRaises(ValueError):
        descriptor.validate([wirely.Some(wirely.types.uint16)])
    supplied.append(wirely.Some(types.uint8))
    assert context.values['Values'] == expected


make_probe('''
parameter Type: ValueType = List[Option[ValueType]];
parameter Values: Type = [Some(uint16), None];
native procedure run uses Type, Values;
''', check_type_values)

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
assert mode_type.json_encode(
    mode_type.parse('EmergencyHeat'), 'SnakeCase',
) == 'emergency_heat'
assert mode_type.json_decode('Cool') == mode_type.parse('Cool')

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
                  lambda: mode_type.json_decode('invalid')]:
    with checks.assertRaises(ValueError):
        operation()
