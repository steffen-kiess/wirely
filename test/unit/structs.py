from collections.abc import Mapping
from dataclasses import FrozenInstanceError
import datetime
from unittest import TestCase

import wirely
import wirely.compiler
from wirely.compiler import parser
from wirely.compiler.types import parse_value
from wirely.types.value_types import _from_native, _to_native


checks = TestCase()
graph = engine.program.graph
sample = graph.libraries['Sample']
packet = sample.struct_types['Packet'].value_type
mode = sample.enum_types['Mode']
expected = packet.make_value({'flag': True, 'mode': mode.parse('On'), 'number': 513})
assert isinstance(expected, Mapping)
assert list(expected) == ['number', 'mode', 'flag']
assert expected == packet.make_value({'number': 513, 'flag': True, 'mode': mode.parse('On')})
assert packet.equals(expected, expected)
assert str(packet) == 'Sample.Packet'
with checks.assertRaises(TypeError):
    expected['flag'] = False
with checks.assertRaises((FrozenInstanceError, AttributeError)):
    expected._fields = {}
other = graph.struct_types['Other'].value_type
other_value = other.make_value(dict(expected))
assert expected != other_value
with checks.assertRaises(ValueError):
    packet.verify_value(other_value)
with checks.assertRaises(ValueError):
    packet.verify_value(dict(expected))

engine.step()
engine.assert_value_is('source', expected)
engine.assert_value_is('forward', expected)
engine.assert_value_is('delay', packet.make_value({'number': 0, 'mode': mode.parse('Off'), 'flag': False}))
engine.step()
engine.assert_value_is('delay', expected)
engine.get_native_state('events').send_event(wirely.Some(expected))
engine.step()
engine.assert_value_is('events', wirely.Some(expected))

clock = sample.struct_types['Clock'].value_type
assert engine.program.instances['configured.value'].parameters['Value'] == clock.make_value({
    'number': 7, 'at': datetime.time(0, 30),
})
settings_type = graph.struct_types['Settings'].value_type
assert not settings_type.is_runtime_type
settings = engine.program.instances['settings'].parameters['Value']
assert settings['text'] == 'hello'
assert settings['items'] == (wirely.Some(expected), None)
assert settings['inner']['packet'] == expected
assert settings['type'] == wirely.value_types.uint8
assert _from_native(settings_type, settings)['type'] is wirely.types.uint8
assert _to_native(settings_type, _from_native(settings_type, settings)) == settings
native_type = wirely.ValueType(settings_type)
constructed = native_type.make_struct(dict(settings))
assert constructed == settings
assert native_type.validate(constructed) == constructed
assert not native_type.supports_json and not native_type.supports_csv
with checks.assertRaises(ValueError):
    native_type.parse('{}')
with checks.assertRaises(ValueError):
    wirely.value_types.uint8.make_struct({})
for fields in [None, [], {}, {**dict(settings), 'extra': 1}, {**dict(settings), 'text': 7}]:
    with checks.assertRaises(ValueError):
        native_type.make_struct(fields)
mutable = dict(settings)
constructed = native_type.make_struct(mutable)
mutable['text'] = 'changed'
assert constructed['text'] == 'hello'

escaped = engine.program.instances['escaped'].parameters['Value']
assert escaped['class'] == 5 and escaped['struct'] == wirely.Unit()
assert wirely.types.TypeStruct('Lists', {'items': wirely.types.TypeList(wirely.types.uint8)}).make_value({
    'items': [1, 2],
})['items'] == (1, 2)

# Exact bytes distinguish field storage slots from compact tail-padding reuse.
slot = graph.struct_types['Slot'].value_type
value = slot.make_value({'a': wirely.Some(0x0102030405060708), 'b': 9})
encoded = bytes.fromhex('08 07 06 05 04 03 02 01 01 00 00 00 00 00 00 00 09')
assert (slot.data_size, slot.size, slot.alignment) == (17, 24, 8)
assert slot.serialize(value) == encoded
assert slot.deserialize(encoded) == value
assert slot.serialize(slot.make_value({'a': None, 'b': 9})) == bytes(16) + b'\x09'
for offset in range(9, 16):
    invalid = bytearray(encoded)
    invalid[offset] = 1
    with checks.assertRaises(ValueError):
        slot.deserialize(invalid)
for invalid in [encoded[:-1], encoded + b'\x00', bytes(24), bytes(8) + b'\x02' + bytes(8)]:
    with checks.assertRaises(ValueError):
        slot.deserialize(invalid)
with checks.assertRaises(ValueError):
    slot.deserialize(b'\x01' + bytes(15) + b'\x09')

empty = graph.struct_types['Empty'].value_type
assert (empty.data_size, empty.size, empty.alignment) == (0, 0, 1)
assert empty.serialize(empty.make_value({})) == b''
assert empty.deserialize(b'') == empty.make_value({})
engine.assert_value_is('empty', empty.make_value({}))
assert packet.serialize(expected) == b'\x01\x02\x01\x01'
assert packet.deserialize(b'\x01\x02\x01\x01') == expected
for invalid in [b'\x01\x02\x02\x01', b'\x01\x02\x01\x02']:
    with checks.assertRaises(ValueError):
        packet.deserialize(invalid)

nested = wirely.types.TypeStruct('Nested', {'first': wirely.types.uint8, 'slot': slot, 'last': wirely.types.uint16})
value = nested.make_value({'last': 0x1234, 'slot': value, 'first': 1})
encoded_nested = b'\x01' + bytes(7) + encoded + bytes(7) + b'\x34\x12'
assert (nested.data_size, nested.size, nested.alignment) == (34, 40, 8)
assert nested.serialize(value) == encoded_nested
assert nested.deserialize(encoded_nested) == value
for offset in [1, 7, 17, 25, 31]:
    invalid = bytearray(encoded_nested)
    invalid[offset] = 1
    with checks.assertRaises(ValueError):
        nested.deserialize(invalid)
optional = wirely.types.TypeOption(slot)
assert optional.serialize(wirely.Some(value['slot'])) == encoded + b'\x01'
assert optional.deserialize(encoded + b'\x01') == wirely.Some(value['slot'])
assert optional.serialize(None) == bytes(18)
assert optional.deserialize(bytes(18)) is None

zero_fields = wirely.types.TypeStruct('ZeroFields', {
    'empty': empty, 'unit': wirely.types.unit, 'number': wirely.types.uint16, 'end': empty,
})
zero_value = zero_fields.make_value({'empty': empty.make_value({}), 'unit': wirely.Unit(), 'number': 42, 'end': empty.make_value({})})
assert zero_fields.serialize(zero_value) == b'\x2a\x00'
assert zero_fields.deserialize(b'\x2a\x00') == zero_value
for parameter_type in [settings_type, wirely.types.TypeOption(settings_type)]:
    with checks.assertRaises(ValueError):
        parameter_type.alignment

# Equivalent declarations in separate compilations must keep distinct identities.
first = wirely.compiler.compile_strs([('<first>', 'struct type Record { value : uint8, }')])
second = wirely.compiler.compile_strs([('<second>', 'struct type Record { value : uint8, }')])
assert first is not None and second is not None
first_type = first.struct_types['Record'].value_type
second_type = second.struct_types['Record'].value_type
assert first_type is not second_type
with checks.assertRaises(ValueError):
    first_type.verify_value(second_type.make_value({'value': 1}))


literal = parser.parse_str(
    'node type Settings { parameter Value : Sample.Packet = '
    'Sample.Packet { flag : true, mode : Sample.Mode.On, number : 513 }; }',
    filename='<struct-literal>',
)[0].declarations[0].default_value
assert parse_value(packet, literal, scope=graph) == expected
