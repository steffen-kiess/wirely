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
