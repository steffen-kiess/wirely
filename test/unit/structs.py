from collections.abc import Mapping
from dataclasses import FrozenInstanceError
import enum
from unittest import TestCase

import wirely


checks = TestCase()


class Mode(enum.Enum):
    Off = 0
    On = 1


mode = wirely.types.TypeEnum('Sample.Mode', Mode, underlying_type=wirely.types.uint8)
packet = wirely.types.TypeStruct('Sample.Packet', {
    'number': wirely.types.uint16, 'mode': mode, 'flag': wirely.types.boolean,
})
settings_type = wirely.types.TypeStruct('Settings', {
    'text': wirely.types.String,
    'items': wirely.types.TypeList(wirely.types.TypeOption(packet)),
    'inner': wirely.types.TypeStruct('Nested', {'packet': packet}),
    'type': wirely.types.ValueType,
})
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
other = wirely.types.TypeStruct('Other', dict(packet.fields))
other_value = other.make_value(dict(expected))
assert expected != other_value
with checks.assertRaises(ValueError):
    packet.verify_value(other_value)
with checks.assertRaises(ValueError):
    packet.verify_value(dict(expected))

settings = wirely.ValueType(settings_type).make_struct({
    'text': 'hello',
    'items': (wirely.Some(expected), None),
    'inner': settings_type.fields['inner'].make_value({'packet': expected}),
    'type': wirely.value_types.uint8,
})
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

assert wirely.types.TypeStruct('Lists', {'items': wirely.types.TypeList(wirely.types.uint8)}).make_value({
    'items': [1, 2],
})['items'] == (1, 2)

# Exact bytes distinguish field storage slots from compact tail-padding reuse.
slot = wirely.types.TypeStruct('Slot', {'a': wirely.types.TypeOption(wirely.types.uint64), 'b': wirely.types.uint8})
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

empty = wirely.types.TypeStruct('Empty', {})
assert (empty.data_size, empty.size, empty.alignment) == (0, 0, 1)
assert empty.serialize(empty.make_value({})) == b''
assert empty.deserialize(b'') == empty.make_value({})
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
