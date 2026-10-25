from unittest import TestCase

import wirely
from wirely.program import Variable, VariableTable
from wirely.generated import extension as builtin_extension
from wirely.runtime.contexts import VariableTableInstance
from wirely.runtime.native_values import check_opaque_values
from tests.infra.support import internal, library_type


checks = TestCase()
thermostat_type = wirely.program.types.TypeEnum(
    'Mode', {'Off': 0, 'Cool': 3, 'Heat': 4}, is_open=True, underlying_type=wirely.program.types.uint8,
)
mode = wirely.program.types.TypeEnum('PacketMode', {'Off': 0, 'On': 1},
                             is_open=False, underlying_type=wirely.program.types.uint8)
packet = wirely.program.types.TypeStruct('Packet', {
    'number': wirely.program.types.uint16, 'mode': mode, 'flag': wirely.program.types.boolean,
})
expected = packet.make_value({'number': 513, 'mode': mode.parse('On'), 'flag': True})


def check_scalar_codecs():
    # Runtime codecs return data bytes; aligned storage may have extra tail padding.
    for ty, value, encoded, alignment in [
        (wirely.program.types.unit, wirely.Unit(), b'', 1),
        (wirely.program.types.boolean, False, b'\x00', 1),
        (wirely.program.types.boolean, True, b'\x01', 1),
        (wirely.program.types.uint32, 0x12345678, bytes.fromhex('78 56 34 12'), 4),
        (wirely.program.types.int16, -2, bytes.fromhex('fe ff'), 2),
        (library_type('Std.Duration'), internal(wirely.Duration.from_seconds(1.5)),
         bytes(7) + b'\x80\x01' + bytes(7), 16),
        (library_type('Schedule.TimeOfDay'), internal(wirely.TimeOfDay.from_parts(0, 0, 1)),
         bytes(8) + b'\x01' + bytes(7), 16),
        (thermostat_type, 4, b'\x04', 1),
    ]:
        assert ty.alignment == alignment
        assert ty.data_size == len(encoded)
        assert ty.size == len(encoded)
        assert ty.serialize(value) == encoded
        assert ty.deserialize(encoded) == value
        assert ty.deserialize(memoryview(encoded)) == value

    # Monotonic times store 2**-64 s units, then the generation, with tail padding.
    monotonic = library_type('Timer.MonotonicTime')
    assert (monotonic.data_size, monotonic.size, monotonic.alignment) == (24, 32, 16)
    for value, encoded in [(wirely.MonotonicTime(generation=7, ticks=123), b'\x7b' + bytes(15) + b'\x07' + bytes(7)),
                           (wirely.MonotonicTime(generation=0, ticks=0), bytes(24))]:
        assert monotonic.serialize(internal(value)) == encoded
        assert monotonic.deserialize(encoded) == internal(value)

    for bits in (8, 16, 32, 64, 128):
        for signed in (False, True):
            ty = getattr(wirely.program.types, ('int' if signed else 'uint') + str(bits))
            assert ty.size == ty.data_size == ty.alignment == bits // 8
            for value in (ty.min_value, 0, ty.max_value):
                encoded = value.to_bytes(bits // 8, 'little', signed=signed)
                assert ty.serialize(value) == encoded
                assert ty.deserialize(encoded) == value
            for invalid in (ty.min_value - 1, ty.max_value + 1):
                with checks.assertRaises(ValueError):
                    ty.serialize(invalid)

    option_uint64 = wirely.program.types.TypeOption(wirely.program.types.uint64)
    nested_uint64 = wirely.program.types.TypeOption(option_uint64)
    assert (option_uint64.data_size, option_uint64.size, option_uint64.alignment) == (9, 16, 8)
    assert (nested_uint64.data_size, nested_uint64.size, nested_uint64.alignment) == (10, 16, 8)
    option_int128 = wirely.program.types.TypeOption(wirely.program.types.int128)
    assert (option_int128.data_size, option_int128.size, option_int128.alignment) == (17, 32, 16)
    for ty, value, encoded in [
        (option_uint64, None, bytes(9)),
        (option_uint64, wirely.Some(7), b'\x07' + bytes(7) + b'\x01'),
        (nested_uint64, None, bytes(10)),
        (nested_uint64, wirely.Some(None), bytes(9) + b'\x01'),
        (nested_uint64, wirely.Some(wirely.Some(7)), b'\x07' + bytes(7) + b'\x01\x01'),
        (wirely.program.types.TypeOption(wirely.program.types.unit), None, b'\x00'),
        (wirely.program.types.TypeOption(wirely.program.types.unit), wirely.Some(wirely.Unit()), b'\x01'),
    ]:
        assert ty.serialize(value) == encoded
        assert ty.deserialize(encoded) == value
        for malformed in (encoded[:-1], encoded + b'\x00', encoded + b'\x01'):
            with checks.assertRaises(ValueError):
                ty.deserialize(malformed)

    for ty, malformed in [
        (wirely.program.types.boolean, b'\x02'),
        (wirely.program.types.boolean, b'\xff'),
        (option_uint64, bytes(8) + b'\x02'),
        (option_uint64, b'\x01' + bytes(8)),
        (nested_uint64, bytes(8) + b'\x01\x00'),
        (nested_uint64, b'\x01' + bytes(8) + b'\x01'),
        (nested_uint64, bytes(8) + b'\x02\x01'),
        (wirely.program.types.TypeEnum('Closed', {'A': 0, 'B': 1},
                               is_open=False, underlying_type=wirely.program.types.uint8), b'\x02'),
    ]:
        with checks.assertRaises(ValueError):
            ty.deserialize(malformed)
    # Deserialization checks the layout; the implementation of an opaque struct checks its values.
    time_of_day = library_type('Schedule.TimeOfDay')
    late = time_of_day.deserialize((wirely.TimeOfDay.END_TICKS + 1).to_bytes(16, 'little'))
    with checks.assertRaises(ValueError):
        check_opaque_values(time_of_day, late, native_types=builtin_extension.native_types)

    # Nanosecond values up to 24:00 round-trip.
    for text in ('00:00', '00:00:00.00000000000000000005', '23:59:59.99999999999999999995', '24:00'):
        value = internal(wirely.TimeOfDay.parse(text))
        time_of_day = library_type('Schedule.TimeOfDay')
        assert time_of_day.deserialize(time_of_day.serialize(value)) == value

    for ty, value in [
        (library_type('Std.Duration'), wirely.Duration(ticks=wirely.Duration.MAX_TICKS)),
        (library_type('Timer.MonotonicTime'),
         wirely.MonotonicTime(generation=wirely.MonotonicTime.MAX_GENERATION, ticks=wirely.MonotonicTime.MAX_TICKS)),
    ]:
        assert ty.deserialize(ty.serialize(internal(value))) == internal(value)

    for ty in (wirely.program.types.String, wirely.program.types.ValueType,
               wirely.program.types.TypeList(wirely.program.types.uint8), wirely.program.types.TypeOption(wirely.program.types.String)):
        for operation in (lambda: ty.size, lambda: ty.data_size, lambda: ty.alignment,
                          lambda: ty.serialize(None), lambda: ty.deserialize(b'')):
            with checks.assertRaises(ValueError):
                operation()

    # Enum codecs support every integer width and validate their member ranges.
    for signed in (False, True):
        for bits in (8, 16, 32, 64, 128):
            underlying = getattr(wirely.program.types, ('int' if signed else 'uint') + str(bits))
            members = {'Minimum': underlying.min_value, 'Maximum': underlying.max_value}
            descriptor = wirely.program.types.TypeEnum('Boundary', members, is_open=True, underlying_type=underlying)
            assert descriptor.json_encode(underlying.max_value, None) == 'Maximum'
            assert descriptor.json_decode('Minimum', None) == underlying.min_value
            for number in (underlying.min_value, 0, 7, underlying.max_value):
                encoded = number.to_bytes(bits // 8, 'little', signed=signed)
                assert descriptor.serialize(number) == encoded
                assert descriptor.deserialize(encoded) == number

    signed_members = {'Negative': -2, 'Positive': 300}
    signed_type = wirely.program.types.TypeEnum('SignedMode', signed_members, is_open=True, underlying_type=wirely.program.types.int16)
    assert (signed_type.size, signed_type.data_size, signed_type.alignment) == (2, 2, 2)
    with checks.assertRaises(ValueError):
        wirely.program.types.TypeEnum('TooSmall', signed_members, is_open=True, underlying_type=wirely.program.types.uint8)


def check_closed_enum_codecs():
    for count in (1, 256, 257, 65536, 65537):
        underlying = wirely.program.types.closed_enum_underlying_type(count)
        value_type = wirely.program.types.TypeEnum('Closed', {f'M{i}': i for i in range(count)},
                                          is_open=False, underlying_type=underlying)
        assert value_type.size == underlying.size
        assert value_type.default_value() == 0
        for number in (0, count - 1):
            encoded = number.to_bytes(underlying.size, 'little')
            assert value_type.serialize(number) == encoded
            assert value_type.deserialize(encoded) == number
        if count <= underlying.max_value:
            with checks.assertRaises(ValueError):
                value_type.deserialize(count.to_bytes(underlying.size, 'little'))



def check_struct_codecs():
    # Exact bytes distinguish field storage slots from compact tail-padding reuse.
    slot = wirely.program.types.TypeStruct('Slot', {'a': wirely.program.types.TypeOption(wirely.program.types.uint64), 'b': wirely.program.types.uint8})
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

    empty = wirely.program.types.TypeStruct('Empty', {})
    assert (empty.data_size, empty.size, empty.alignment) == (0, 0, 1)
    assert empty.serialize(empty.make_value({})) == b''
    assert empty.deserialize(b'') == empty.make_value({})
    assert packet.serialize(expected) == b'\x01\x02\x01\x01'
    assert packet.deserialize(b'\x01\x02\x01\x01') == expected
    for invalid in [b'\x01\x02\x02\x01', b'\x01\x02\x01\x02']:
        with checks.assertRaises(ValueError):
            packet.deserialize(invalid)

    nested = wirely.program.types.TypeStruct('Nested', {'first': wirely.program.types.uint8, 'slot': slot, 'last': wirely.program.types.uint16})
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
    optional = wirely.program.types.TypeOption(slot)
    assert optional.serialize(wirely.Some(value[nested.index('slot')])) == encoded + b'\x01'
    assert optional.deserialize(encoded + b'\x01') == wirely.Some(value[nested.index('slot')])
    assert optional.serialize(None) == bytes(18)
    assert optional.deserialize(bytes(18)) is None

    zero_fields = wirely.program.types.TypeStruct('ZeroFields', {
        'empty': empty, 'unit': wirely.program.types.unit, 'number': wirely.program.types.uint16, 'end': empty,
    })
    zero_value = zero_fields.make_value({'empty': empty.make_value({}), 'unit': wirely.Unit(), 'number': 42, 'end': empty.make_value({})})
    assert zero_fields.serialize(zero_value) == b'\x2a\x00'
    assert zero_fields.deserialize(b'\x2a\x00') == zero_value


def check_variable_storage():
    flag = Variable(wirely.program.types.boolean, name='flag')
    small = Variable(wirely.program.types.uint16, name='small')
    first_unit = Variable(wirely.program.types.unit, name='first_unit')
    second_unit = Variable(wirely.program.types.unit, name='second_unit')
    nested = Variable(wirely.program.types.TypeOption(wirely.program.types.TypeOption(wirely.program.types.uint64)), name='nested')
    clock = Variable(library_type('Timer.MonotonicTime'), name='clock')
    table = VariableTable([flag, small, first_unit, nested, clock, second_unit], 'storage')
    values = VariableTableInstance(table)
    independent = VariableTableInstance(table)
    expected = {flag: True, small: 65535, first_unit: wirely.Unit(),
                nested: wirely.Some(None), clock: internal(wirely.MonotonicTime(generation=7, ticks=123)),
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
    foreign = Variable(wirely.program.types.boolean, name='flag')
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


def check_unnamed_storage():
    small = Variable(wirely.program.types.uint8)
    wide = Variable(wirely.program.types.uint64)
    units = (Variable(wirely.program.types.unit), Variable(wirely.program.types.unit))
    same = (Variable(wirely.program.types.boolean, name='same'), Variable(wirely.program.types.boolean, name='same'))
    variables = (small, *units, wide, *same)
    table = VariableTable(variables, 'storage')
    assert table.variables == variables
    assert table.offsets[wide] == 0 and table.offsets[small] == 8
    assert len(table.variable_set) == len(variables)
    storage = VariableTableInstance(table)
    for variable, value in zip(variables, (7, wirely.Unit(), wirely.Unit(), 9, True, False)):
        storage.add(variable, value)
    assert storage[same[0]] is True and storage[same[1]] is False
    assert storage[units[0]] == storage[units[1]] == wirely.Unit()
    assert small.name is None and table.label(small) == 'storage[1]'
    with checks.assertRaisesRegex(ValueError, 'more than once'):
        VariableTable((small, small), 'duplicate')


check_scalar_codecs()
check_closed_enum_codecs()
check_struct_codecs()
check_variable_storage()
check_unnamed_storage()


async def run_test():
    pass
