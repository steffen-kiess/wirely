import datetime
from unittest import TestCase
from unittest.mock import patch

import wirely.types

def assert_is(value, expected):
    if value != expected:
        raise Exception(f'Expected {expected!r}, got {value!r}')

def assert_fail(fun):
    try:
        fun()
    except:
        return
    raise Exception('Should have raised an exception')

def check_type(ty):
    # assert_is(hasattr(ty, 'to_csv_value'), ty.has_to_csv_value)
    # assert_is(hasattr(ty, 'json_encode'), ty.is_json_serializable)
    # assert_is(hasattr(ty, 'json_decode'), ty.is_json_serializable)
    assert_is(hasattr(ty, 'to_csv_value') and ty.to_csv_value is not None, ty.has_to_csv_value)
    assert_is(hasattr(ty, 'json_encode') and ty.json_encode is not None, ty.is_json_serializable)
    assert_is(hasattr(ty, 'json_decode') and ty.json_decode is not None, ty.is_json_serializable)

# unit

ty = wirely.types.unit
check_type(ty)

assert_is(ty.name, 'unit')
assert_is(ty.python_type, wirely.Unit)
assert_is(ty.is_runtime_type, True)
assert_is(ty.is_json_serializable, False)
assert_is(ty.has_to_csv_value, False)

assert_is(ty.parse('()'), wirely.Unit())
assert_fail(lambda: ty.parse('0'))
# TODO: Test parse_value()?
# assert_is(ty.json_encode(False, None), False)
# assert_is(ty.json_encode(True, None), True)
# assert_is(ty.json_decode(False, None), False)
# assert_is(ty.json_decode(True, None), True)
# assert_is(ty.to_csv_value(False), ('false', False))
# assert_is(ty.to_csv_value(True), ('true', False))

# boolean

ty = wirely.types.boolean
check_type(ty)

assert_is(ty.name, 'boolean')
assert_is(ty.python_type, bool)
assert_is(ty.is_runtime_type, True)
assert_is(ty.is_json_serializable, True)
assert_is(ty.has_to_csv_value, True)

assert_is(ty.parse('0'), False)
assert_is(ty.parse('false'), False)
assert_is(ty.parse('tRUE'), True)
assert_fail(lambda: ty.parse('2'))
# TODO: Test parse_value()?
assert_is(ty.json_encode(False, None), False)
assert_is(ty.json_encode(True, None), True)
assert_is(ty.json_decode(False, None), False)
assert_is(ty.json_decode(True, None), True)
assert_is(ty.to_csv_value(False), ('false', False))
assert_is(ty.to_csv_value(True), ('true', False))

# int

for signed in [False, True]:
    for bits in [8, 16, 32, 64]:
        tname = '{}int{}'.format('' if signed else 'u', bits)
        ty = getattr(wirely.types, tname)
        check_type(ty)
        if signed:
            min = -2**(bits - 1)
            max = 2**(bits - 1) - 1
        else:
            min = 0
            max = 2**(bits) - 1

        assert_is(ty.name, tname)
        assert_is(ty.python_type, int)
        assert_is(ty.is_runtime_type, True)
        assert_is(ty.is_json_serializable, True)
        assert_is(ty.has_to_csv_value, True)

        assert_is(ty.parse('0'), 0)
        assert_is(ty.parse('10'), 10)
        if signed:
            assert_is(ty.parse('-10'), -10)
        assert_fail(lambda: ty.parse('false'))
        if bits == 8:
            assert_fail(lambda: ty.parse('300'))
            assert_fail(lambda: ty.parse('-200'))
        assert_is(ty.parse(f'{min}'), min)
        assert_is(ty.parse(f'{max}'), max)
        assert_fail(lambda: ty.parse(f'{min - 1}'))
        assert_fail(lambda: ty.parse(f'{max + 1}'))
        # TODO: Test parse_value()?
        assert_is(ty.json_encode(0, None), 0)
        assert_is(ty.json_encode(10, None), 10)
        assert_is(ty.json_decode(0, None), 0)
        assert_is(ty.json_decode(10, None), 10)
        assert_is(ty.to_csv_value(0), ('0', False))
        assert_is(ty.to_csv_value(10), ('10', False))

# ValueType

ty = wirely.types.ValueType
check_type(ty)

assert_is(ty.name, 'ValueType')
assert_is(ty.python_type, wirely.types.Type)
assert_is(ty.is_runtime_type, False)
assert_is(ty.is_json_serializable, False)
assert_is(ty.has_to_csv_value, False)

# TimeOfDay

ty = wirely.types.TimeOfDay
check_type(ty)

assert_is(ty.name, 'TimeOfDay')
assert_is(ty.python_type, datetime.time)
assert_is(ty.is_runtime_type, True)
assert_is(ty.is_json_serializable, False)
assert_is(ty.has_to_csv_value, False)

assert_is(ty.parse('11:17'), datetime.time(11, 17))
assert_fail(lambda: ty.parse('123'))
# TODO: Test parse_value()?

# String

ty = wirely.types.String
check_type(ty)

assert_is(ty.name, 'String')
assert_is(ty.python_type, str)
assert_is(ty.is_runtime_type, False)
assert_is(ty.is_json_serializable, True)
assert_is(ty.has_to_csv_value, False)

# TODO: Test parse_value()?
assert_is(ty.json_encode('qwe', None), 'qwe')
assert_is(ty.json_decode('ewq', None), 'ewq')

# ThermostatSystemMode

ty = wirely.types.ThermostatSystemMode
check_type(ty)

assert_is(ty.name, 'ThermostatSystemModeEnum')
assert_is(ty.python_type, wirely.enum.ThermostatSystemMode)
assert_is(ty.is_runtime_type, True)
assert_is(ty.is_json_serializable, True)
# TODO: Implement
assert_is(ty.has_to_csv_value, False)

for member in wirely.enum.ThermostatSystemMode:
    assert_is(ty.parse(member.name), member)
assert_fail(lambda: ty.parse('NotCool'))
assert_fail(lambda: ty.parse('cool'))
assert_fail(lambda: ty.parse('ThermostatSystemModeEnum.Cool'))
# TODO: Test parse_value()?
assert_is(ty.json_encode(wirely.enum.ThermostatSystemMode.Cool, None), 'Cool')
assert_is(ty.json_encode(wirely.enum.ThermostatSystemMode.EmergencyHeat, 'SnakeCase'), 'emergency_heat')
assert_is(ty.json_decode('Heat', None), wirely.enum.ThermostatSystemMode.Heat)
# TODO: Test SnakeCase decoding
# TODO: CSV


checks = TestCase()


list_type = wirely.types.TypeList(wirely.types.uint8)
for supplied in [[], (), [True, 7], (True, 7)]:
    normalized = list_type.verify_value(supplied)
    assert type(normalized) is tuple
    assert normalized == tuple(int(value) for value in supplied)
    assert all(type(value) is int for value in normalized)
assert list_type.equals([1, 7], (1, 7))
assert not list_type.equals((1,), [1, 7])
assert not list_type.equals([1], (2,))
for invalid in ['12', {1, 2}, iter([1, 2]), ['invalid'], (None,)]:
    with checks.assertRaises(Exception):
        list_type.verify_value(invalid)

nested_list_type = wirely.types.TypeList(list_type)
supplied = [[1, 2], [3]]
normalized = nested_list_type.verify_value(supplied)
supplied[0].append(4)
supplied.append([5])
assert normalized == ((1, 2), (3,))
with checks.assertRaises(TypeError):
    normalized[0][0] = 7
assert nested_list_type.equals(normalized, [[1, 2], [3]])

optional_list_type = wirely.types.TypeOption(list_type)
assert optional_list_type.verify_value(wirely.Some([1, 2])) == wirely.Some((1, 2))
assert optional_list_type.equals(wirely.Some([1, 2]), wirely.Some((1, 2)))


def check_immutable_time(value, equivalent, fields):
    mapping = {value: 'timestamp'}
    values = {value}
    for name in fields:
        with checks.assertRaises(AttributeError):
            setattr(value, name, 0)
        with checks.assertRaises(AttributeError):
            delattr(value, name)
    assert mapping[equivalent] == 'timestamp'
    assert equivalent in values
    assert hash(value) == hash(equivalent)


monotonic = wirely.MonotonicTime(7, 5_000_000_000)
equivalent = wirely.MonotonicTime(generation=7, time_ns=5_000_000_000)
check_immutable_time(monotonic, equivalent, ['generation', 'time_ns'])
assert repr(monotonic) == 'MonotonicTime(generation=7, time_ns=5000000000)'
assert monotonic != wirely.MonotonicTime(8, monotonic.time_ns)
for arguments in [('7', 5), (7, '5')]:
    with checks.assertRaises(Exception):
        wirely.MonotonicTime(*arguments)
with checks.assertRaises(Exception):
    wirely.MonotonicTime(generation='7', time_ns=5)
with patch('wirely.types.time.time.clock_gettime_ns', return_value=123):
    assert wirely.MonotonicTime.current_time() == wirely.MonotonicTime(1, 123)

invalid = wirely.MonotonicTime.create_invalid()
check_immutable_time(invalid, wirely.MonotonicTime(0, 0), ['generation', 'time_ns'])
assert invalid.is_invalid and not invalid.is_valid
assert monotonic.is_valid and not monotonic.is_invalid
assert repr(invalid) == 'MonotonicTime.invalid'
assert not wirely.MonotonicTime.is_comparable(monotonic, invalid)
with checks.assertRaises(Exception):
    wirely.MonotonicTime.compare(monotonic, invalid)

short = wirely.Duration(duration_ns=2_000_000_000)
long = wirely.Duration(duration_ns=5_000_000_000)
check_immutable_time(short, wirely.Duration.from_seconds(2), ['duration_ns'])
assert repr(short) == 'Duration(2000000000)'
assert short.total_seconds == 2
assert wirely.Duration(duration_ns=True).duration_ns == 1
assert type(wirely.Duration(duration_ns=True).duration_ns) is int
with checks.assertRaises(TypeError):
    wirely.Duration(2)
for value in [-1, '2', 2.5]:
    with checks.assertRaises(Exception):
        wirely.Duration(duration_ns=value)
assert wirely.Duration.parse('PT2S') == short
assert wirely.Duration.parse('P1DT2H3M4.5S').total_seconds == 93784.5
for text in ['P', 'PT', '-PT1S', 'P1M', 'invalid']:
    with checks.assertRaises(Exception):
        wirely.Duration.parse(text)
assert short + long == wirely.Duration.from_seconds(7)
assert long - short == wirely.Duration.from_seconds(3)
assert short < long and short <= long and long > short and long >= short
assert short <= wirely.Duration.from_seconds(2)
assert short >= wirely.Duration.from_seconds(2)
assert short != long and short != 2
with checks.assertRaises(Exception):
    short - long

later = monotonic + short
assert later == wirely.MonotonicTime(7, 7_000_000_000)
assert later - short == monotonic
assert later - monotonic == short
assert monotonic.time_ns == 5_000_000_000
assert short.duration_ns == 2_000_000_000
assert wirely.MonotonicTime.compare(monotonic, later) == -1
assert wirely.MonotonicTime.compare(later, monotonic) == 1
assert wirely.MonotonicTime.compare(monotonic, equivalent) == 0
assert invalid + short is invalid
assert invalid - short is invalid
with checks.assertRaises(Exception):
    later - invalid

# Runtime eligibility follows option payloads; lists remain parameter-only.
assert not wirely.types.String.is_runtime_type
assert not wirely.types.TypeList(wirely.types.uint8).is_runtime_type
assert wirely.types.TypeOption(wirely.types.uint8).is_runtime_type
assert not wirely.types.TypeOption(wirely.types.String).is_runtime_type
assert not wirely.types.TypeOption(wirely.types.TypeList(wirely.types.uint8)).is_runtime_type

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
    (wirely.types.MonotonicTime, wirely.MonotonicTime.create_invalid(), bytes(16), 8),
    (wirely.types.TimeOfDay, datetime.time(0, 0, 1),
     bytes.fromhex('00 ca 9a 3b 00 00 00 00'), 8),
    (wirely.types.ThermostatSystemMode, wirely.enum.ThermostatSystemMode.Heat, b'\x04', 1),
]:
    assert ty.alignment == alignment
    assert ty.data_size == len(encoded)
    assert ty.size == len(encoded)
    assert ty.serialize(value) == encoded
    assert ty.deserialize(encoded) == value
    assert ty.deserialize(memoryview(encoded)) == value

for bits in (8, 16, 32, 64):
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
    (wirely.types.ThermostatSystemMode, b'\x02'),
    (wirely.types.TimeOfDay, (86_400_000_000_000).to_bytes(8, 'little')),
    (wirely.types.TimeOfDay, (86_400_000_000_001).to_bytes(8, 'little')),
    (wirely.types.TimeOfDay, (1).to_bytes(8, 'little')),
]:
    with checks.assertRaises(ValueError):
        ty.deserialize(malformed)

for value in (datetime.time(0), datetime.time(23, 59, 59, 999999)):
    assert wirely.types.TimeOfDay.deserialize(wirely.types.TimeOfDay.serialize(value)) == value
with checks.assertRaises(ValueError):
    wirely.types.TimeOfDay.serialize(datetime.time(12, tzinfo=datetime.timezone.utc))

maximum = 2**64 - 1
for ty, value in [
    (wirely.types.Duration, wirely.Duration(duration_ns=maximum)),
    (wirely.types.MonotonicTime, wirely.MonotonicTime(maximum, maximum)),
]:
    assert ty.deserialize(ty.serialize(value)) == value
for ty, invalid in [
    (wirely.types.Duration, wirely.Duration(duration_ns=maximum + 1)),
    (wirely.types.MonotonicTime, wirely.MonotonicTime(-1, 0)),
    (wirely.types.MonotonicTime, wirely.MonotonicTime(1, -1)),
    (wirely.types.MonotonicTime, wirely.MonotonicTime(maximum + 1, 0)),
    (wirely.types.MonotonicTime, wirely.MonotonicTime(1, maximum + 1)),
]:
    with checks.assertRaises(ValueError):
        ty.serialize(invalid)

for ty in (wirely.types.String, wirely.types.ValueType,
           wirely.types.TypeList(wirely.types.uint8), wirely.types.TypeOption(wirely.types.String)):
    for operation in (lambda: ty.size, lambda: ty.data_size, lambda: ty.alignment,
                      lambda: ty.serialize(None), lambda: ty.deserialize(b'')):
        with checks.assertRaises(ValueError):
            operation()
