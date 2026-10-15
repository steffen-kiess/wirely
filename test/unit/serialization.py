from unittest import TestCase

import wirely
from wirely.program import Variable, VariableTable
from wirely.runtime.contexts import VariableTableInstance


checks = TestCase()
thermostat_type = wirely.types.TypeEnum(
    'Mode', {'Off': 0, 'Cool': 3, 'Heat': 4}, underlying_type=wirely.types.uint8,
)
mode = wirely.types.TypeEnum('PacketMode', {'On': 1},
                             underlying_type=wirely.types.uint8)
packet = wirely.types.TypeStruct('Packet', {
    'number': wirely.types.uint16, 'mode': mode, 'flag': wirely.types.boolean,
})
expected = packet.make_value({'number': 513, 'mode': mode.parse('On'), 'flag': True})


def check_scalar_codecs():
    # Runtime codecs return data bytes; aligned storage may have extra tail padding.
    for ty, value, encoded, alignment in [
        (wirely.types.unit, wirely.Unit(), b'', 1),
        (wirely.types.boolean, False, b'\x00', 1),
        (wirely.types.boolean, True, b'\x01', 1),
        (wirely.types.uint32, 0x12345678, bytes.fromhex('78 56 34 12'), 4),
        (wirely.types.int16, -2, bytes.fromhex('fe ff'), 2),
        (wirely.types.Duration, wirely.Duration(duration_ns=1_000_000),
         bytes.fromhex('40 42 0f 00 00 00 00 00'), 8),
        (wirely.types.MonotonicTime, wirely.MonotonicTime(7, 123),
         bytes.fromhex('07 00 00 00 00 00 00 00 7b 00 00 00 00 00 00 00'), 8),
        (wirely.types.MonotonicTime, wirely.MonotonicTime(0, 0), bytes(16), 8),
        (wirely.types.TimeOfDay, wirely.TimeOfDay.from_parts(0, 0, 1),
         bytes.fromhex('00 ca 9a 3b 00 00 00 00'), 8),
        (thermostat_type, 4, b'\x04', 1),
    ]:
        assert ty.alignment == alignment
        assert ty.data_size == len(encoded)
        assert ty.size == len(encoded)
        assert ty.serialize(value) == encoded
        assert ty.deserialize(encoded) == value
        assert ty.deserialize(memoryview(encoded)) == value

    for bits in (8, 16, 32, 64, 128):
        for signed in (False, True):
            ty = getattr(wirely.types, ('int' if signed else 'uint') + str(bits))
            assert ty.size == ty.data_size == ty.alignment == bits // 8
            for value in (ty.min_value, 0, ty.max_value):
                encoded = value.to_bytes(bits // 8, 'little', signed=signed)
                assert ty.serialize(value) == encoded
                assert ty.deserialize(encoded) == value
            for invalid in (ty.min_value - 1, ty.max_value + 1):
                with checks.assertRaises(ValueError):
                    ty.serialize(invalid)

    option_uint64 = wirely.types.TypeOption(wirely.types.uint64)
    nested_uint64 = wirely.types.TypeOption(option_uint64)
    assert (option_uint64.data_size, option_uint64.size, option_uint64.alignment) == (9, 16, 8)
    assert (nested_uint64.data_size, nested_uint64.size, nested_uint64.alignment) == (10, 16, 8)
    option_int128 = wirely.types.TypeOption(wirely.types.int128)
    assert (option_int128.data_size, option_int128.size, option_int128.alignment) == (17, 32, 16)
    for ty, value, encoded in [
        (option_uint64, None, bytes(9)),
        (option_uint64, wirely.Some(7), b'\x07' + bytes(7) + b'\x01'),
        (nested_uint64, None, bytes(10)),
        (nested_uint64, wirely.Some(None), bytes(9) + b'\x01'),
        (nested_uint64, wirely.Some(wirely.Some(7)), b'\x07' + bytes(7) + b'\x01\x01'),
        (wirely.types.TypeOption(wirely.types.unit), None, b'\x00'),
        (wirely.types.TypeOption(wirely.types.unit), wirely.Some(wirely.Unit()), b'\x01'),
    ]:
        assert ty.serialize(value) == encoded
        assert ty.deserialize(encoded) == value
        for malformed in (encoded[:-1], encoded + b'\x00', encoded + b'\x01'):
            with checks.assertRaises(ValueError):
                ty.deserialize(malformed)

    for ty, malformed in [
        (wirely.types.boolean, b'\x02'),
        (wirely.types.boolean, b'\xff'),
        (option_uint64, bytes(8) + b'\x02'),
        (option_uint64, b'\x01' + bytes(8)),
        (nested_uint64, bytes(8) + b'\x01\x00'),
        (nested_uint64, b'\x01' + bytes(8) + b'\x01'),
        (nested_uint64, bytes(8) + b'\x02\x01'),
        (thermostat_type, b'\x02'),
        (wirely.types.TimeOfDay, (86_400_000_000_001).to_bytes(8, 'little')),
    ]:
        with checks.assertRaises(ValueError):
            ty.deserialize(malformed)

    # Nanosecond values up to 24:00 round-trip.
    for text in ('00:00', '00:00:00.000000001', '23:59:59.999999999', '24:00'):
        value = wirely.TimeOfDay.parse(text)
        assert wirely.types.TimeOfDay.deserialize(wirely.types.TimeOfDay.serialize(value)) == value

    maximum = 2**64 - 1
    for ty, value in [
        (wirely.types.Duration, wirely.Duration(duration_ns=maximum)),
        (wirely.types.MonotonicTime, wirely.MonotonicTime(maximum, maximum)),
    ]:
        assert ty.deserialize(ty.serialize(value)) == value

    for ty in (wirely.types.String, wirely.types.ValueType,
               wirely.types.TypeList(wirely.types.uint8), wirely.types.TypeOption(wirely.types.String)):
        for operation in (lambda: ty.size, lambda: ty.data_size, lambda: ty.alignment,
                          lambda: ty.serialize(None), lambda: ty.deserialize(b'')):
            with checks.assertRaises(ValueError):
                operation()

    # Enum codecs support every integer width and validate their member ranges.
    for signed in (False, True):
        for bits in (8, 16, 32, 64, 128):
            underlying = getattr(wirely.types, ('int' if signed else 'uint') + str(bits))
            members = {'Minimum': underlying.min_value, 'Maximum': underlying.max_value}
            descriptor = wirely.types.TypeEnum('Boundary', members, underlying_type=underlying)
            assert descriptor.json_encode(underlying.max_value, None) == 'Maximum'
            assert descriptor.json_decode('Minimum', None) == underlying.min_value
            for number in members.values():
                encoded = number.to_bytes(bits // 8, 'little', signed=signed)
                assert descriptor.serialize(number) == encoded
                assert descriptor.deserialize(encoded) == number

    signed_members = {'Negative': -2, 'Positive': 300}
    signed_type = wirely.types.TypeEnum('SignedMode', signed_members, underlying_type=wirely.types.int16)
    assert (signed_type.size, signed_type.data_size, signed_type.alignment) == (2, 2, 2)
    with checks.assertRaises(ValueError):
        wirely.types.TypeEnum('TooSmall', signed_members, underlying_type=wirely.types.uint8)


def check_struct_codecs():
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
    assert optional.serialize(wirely.Some(value[nested.index('slot')])) == encoded + b'\x01'
    assert optional.deserialize(encoded + b'\x01') == wirely.Some(value[nested.index('slot')])
    assert optional.serialize(None) == bytes(18)
    assert optional.deserialize(bytes(18)) is None

    zero_fields = wirely.types.TypeStruct('ZeroFields', {
        'empty': empty, 'unit': wirely.types.unit, 'number': wirely.types.uint16, 'end': empty,
    })
    zero_value = zero_fields.make_value({'empty': empty.make_value({}), 'unit': wirely.Unit(), 'number': 42, 'end': empty.make_value({})})
    assert zero_fields.serialize(zero_value) == b'\x2a\x00'
    assert zero_fields.deserialize(b'\x2a\x00') == zero_value


def check_variable_storage():
    flag = Variable('flag', wirely.types.boolean)
    small = Variable('small', wirely.types.uint16)
    first_unit = Variable('first_unit', wirely.types.unit)
    second_unit = Variable('second_unit', wirely.types.unit)
    nested = Variable('nested', wirely.types.TypeOption(wirely.types.TypeOption(wirely.types.uint64)))
    clock = Variable('clock', wirely.types.MonotonicTime)
    table = VariableTable([flag, small, first_unit, nested, clock, second_unit], 'storage')
    values = VariableTableInstance(table)
    independent = VariableTableInstance(table)
    expected = {flag: True, small: 65535, first_unit: wirely.Unit(),
                nested: wirely.Some(None), clock: wirely.MonotonicTime(7, 123),
                second_unit: wirely.Unit()}
    for variable, value in expected.items():
        with checks.assertRaisesRegex(Exception, 'not yet set'):
            values[variable]
        values.add(variable, value)
    for variable, value in expected.items():
        assert values[variable] == value
        with checks.assertRaisesRegex(Exception, 'not yet set'):
            independent[variable]
    for variable in (flag, first_unit):
        with checks.assertRaisesRegex(Exception, 'already set'):
            values.add(variable, expected[variable])
    foreign = Variable('flag', wirely.types.boolean)
    with checks.assertRaisesRegex(Exception, 'not in VariableTable'):
        values.add(foreign, True)
    with checks.assertRaisesRegex(Exception, 'not in VariableTable'):
        values[foreign]
    with checks.assertRaises(ValueError):
        independent.add(small, 65536)
    with checks.assertRaisesRegex(Exception, 'not yet set'):
        independent[small]
    independent.add(small, 7)
    independent.add(nested, None)
    assert independent[small] == 7 and independent[nested] is None
    assert values[small] == 65535 and values[nested] == wirely.Some(None)
    VariableTableInstance(VariableTable([], 'empty'))
    units = VariableTableInstance(VariableTable([first_unit, second_unit], 'units'))
    units.add(first_unit, wirely.Unit())
    with checks.assertRaisesRegex(Exception, 'not yet set'):
        units[second_unit]
    units.add(second_unit, wirely.Unit())
    assert units[first_unit] == units[second_unit] == wirely.Unit()


check_scalar_codecs()
check_struct_codecs()
check_variable_storage()


async def run_test():
    pass
