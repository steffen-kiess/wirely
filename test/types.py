import datetime

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
