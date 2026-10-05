import datetime
import enum
from unittest import TestCase

import wirely


checks = TestCase()


def check_parsing_and_conversions():
    for text, expected in [('0', False), ('false', False), ('tRUE', True)]:
        assert wirely.value_types.boolean.parse(text) is expected
    for value in (False, True):
        descriptor = wirely.value_types.boolean
        assert descriptor.json_decode(descriptor.json_encode(value)) is value
        assert descriptor.to_csv_value(value) == (str(value).lower(), False)
    with checks.assertRaises(ValueError):
        wirely.value_types.boolean.parse('2')

    for signed in (False, True):
        for bits in (8, 16, 32, 64):
            descriptor = getattr(wirely.value_types, ('int' if signed else 'uint') + str(bits))
            minimum = -(2 ** (bits - 1)) if signed else 0
            maximum = 2 ** (bits - (1 if signed else 0)) - 1
            for value in (minimum, 0, maximum):
                assert descriptor.parse(str(value)) == value
                assert descriptor.json_decode(descriptor.json_encode(value)) == value
                assert descriptor.to_csv_value(value) == (str(value), False)
            for text in (str(minimum - 1), str(maximum + 1), 'false'):
                with checks.assertRaises(ValueError):
                    descriptor.parse(text)
    integer = wirely.value_types.uint16
    for operation in (lambda: integer.json_encode('invalid'),
                      lambda: integer.json_decode('invalid'),
                      lambda: integer.to_csv_value('invalid'),
                      lambda: integer.json_encode(42, 'invalid'),
                      lambda: integer.json_decode(42, 'invalid')):
        with checks.assertRaises(ValueError):
            operation()
    assert wirely.value_types.uint16.validate(True) == 1
    assert type(wirely.value_types.uint16.validate(True)) is int
    assert wirely.value_types.string.json_decode('hello') == 'hello'
    assert wirely.value_types.string.json_encode('hello') == 'hello'
    assert wirely.value_types.time_of_day.parse('11:17') == datetime.time(11, 17)
    assert wirely.value_types.unit.parse('()') == wirely.Unit()
    for descriptor, text in [(wirely.value_types.unit, '0'),
                             (wirely.value_types.time_of_day, '123')]:
        with checks.assertRaises(ValueError):
            descriptor.parse(text)

    members = enum.Enum('Mode', {'Cool': 3, 'EmergencyHeat': 5})
    descriptor = wirely.ValueType(wirely.types.TypeEnum(
        'Mode', members, underlying_type=wirely.types.uint8,
    ))
    assert descriptor.parse('Cool') == members.Cool
    assert descriptor.json_encode(members.Cool) == 'Cool'
    assert descriptor.json_encode(members.EmergencyHeat, 'SnakeCase') == 'emergency_heat'
    assert descriptor.json_decode('Cool') == members.Cool
    for text in ('NotCool', 'cool', 'Mode.Cool'):
        with checks.assertRaises(ValueError):
            descriptor.parse(text)


def check_descriptors_and_lists():
    list_type = wirely.types.TypeList(wirely.types.uint16)
    first = wirely.ValueType(wirely.types.TypeOption(list_type))
    second = wirely.ValueType(wirely.types.TypeOption(wirely.types.TypeList(wirely.types.uint16)))
    assert first == second and first != wirely.value_types.uint16
    assert len({first, second, wirely.value_types.uint16}) == 2
    assert str(first) == 'Option[List[uint16]]'
    assert not first.supports_json and not first.supports_csv
    assert first.validate(wirely.Some([True, 7])) == wirely.Some((1, 7))
    assert first.validate(None) is None
    for value in ([7], wirely.Some(['invalid'])):
        with checks.assertRaises(ValueError):
            first.validate(value)
    for operation in (lambda: first.json_encode(None),
                      lambda: first.json_decode(None),
                      lambda: first.to_csv_value(None), lambda: first.parse('7')):
        with checks.assertRaises(ValueError):
            operation()

    nested = wirely.ValueType(wirely.types.TypeList(list_type))
    supplied = [[1, 2], [3]]
    normalized = nested.validate(supplied)
    supplied[0].append(4)
    assert normalized == ((1, 2), (3,))
    with checks.assertRaises(TypeError):
        normalized[0][0] = 7
    for invalid in ('12', {1, 2}, iter([1, 2]), ['invalid']):
        with checks.assertRaises(ValueError):
            wirely.ValueType(list_type).validate(invalid)

    optional = wirely.ValueType(wirely.types.TypeOption(wirely.types.uint16))
    assert optional.parse('none') is None
    assert optional.parse('7') == wirely.Some(7)
    assert optional.supports_csv
    assert optional.to_csv_value(None) == ('', False)
    assert optional.to_csv_value(wirely.Some(7)) == ('7', False)
    with checks.assertRaises(ValueError):
        optional.to_csv_value(wirely.Some('invalid'))
    values = wirely.ValueType(wirely.types.TypeList(wirely.types.TypeOption(wirely.types.ValueType)))
    supplied = [wirely.Some(wirely.value_types.uint16), None]
    validated = values.validate(supplied)
    supplied.append(wirely.Some(wirely.value_types.uint8))
    assert validated == (wirely.Some(wirely.value_types.uint16), None)
    with checks.assertRaises(ValueError):
        values.validate([wirely.Some(wirely.types.uint16)])
    with checks.assertRaises(ValueError):
        wirely.value_types.value_type.validate(wirely.types.uint16)
    assert wirely.value_types.value_type.validate(wirely.value_types.uint16) == wirely.value_types.uint16


def check_unit():
    unit = wirely.Unit()
    assert unit == wirely.Unit()
    checks.assertNotEqual(unit, None)
    assert len({unit, wirely.Unit()}) == 1
    with checks.assertRaises((AttributeError, TypeError)):
        unit.value = True
    for invalid in (None, (), False, wirely.Some(unit)):
        with checks.assertRaises(ValueError):
            wirely.value_types.unit.validate(invalid)
    with checks.assertRaises(ValueError):
        wirely.value_types.unit.json_encode(unit)


def check_times():
    instant = wirely.MonotonicTime(7, 5_000_000_000)
    equivalent = wirely.MonotonicTime(7, 5_000_000_000)
    span = wirely.Duration.from_seconds(2)
    assert {instant: 'time'}[equivalent] == 'time'
    assert len({span, wirely.Duration(duration_ns=2_000_000_000)}) == 1
    for value, field in [(instant, 'time_ns'), (span, 'duration_ns')]:
        with checks.assertRaises(AttributeError):
            setattr(value, field, 0)
    assert instant != wirely.MonotonicTime(8, instant.time_ns)
    invalid = wirely.MonotonicTime.create_invalid()
    assert invalid.is_invalid and not invalid.is_valid
    assert instant.is_valid and not instant.is_invalid
    assert not wirely.MonotonicTime.is_comparable(instant, invalid)
    with checks.assertRaises(Exception):
        wirely.MonotonicTime.compare(instant, invalid)
    assert wirely.Duration.parse('P1DT2H3M4.5S').total_seconds == 93784.5
    assert wirely.Duration.parse('PT2S') == span
    for text in ('P', 'PT', '-PT1S', 'P1M', 'invalid'):
        with checks.assertRaises(ValueError):
            wirely.value_types.duration.parse(text)
    with checks.assertRaises(Exception):
        wirely.Duration(duration_ns=-1)
    longer = wirely.Duration.from_seconds(5)
    assert span + longer == wirely.Duration.from_seconds(7)
    assert longer - span == wirely.Duration.from_seconds(3)
    assert span < longer and span <= span and longer > span and longer >= longer
    with checks.assertRaises(Exception):
        span - longer
    later = instant + span
    assert later - instant == span and later - span == instant
    for left, right, expected in [(instant, later, -1), (later, instant, 1),
                                  (instant, equivalent, 0)]:
        assert wirely.MonotonicTime.compare(left, right) == expected
    assert invalid + span == invalid and invalid - span == invalid
    with checks.assertRaises(Exception):
        later - invalid


def check_structs():
    packet_type = wirely.types.TypeStruct('Packet', {'flag': wirely.types.boolean})
    descriptor = wirely.ValueType(packet_type)
    packet = descriptor.make_struct({'flag': True})
    assert packet == descriptor.make_struct({'flag': True})
    assert packet != descriptor.make_struct({'flag': False})
    with checks.assertRaises(TypeError):
        packet['flag'] = False
    other = wirely.ValueType(wirely.types.TypeStruct('Other', dict(packet_type.fields)))
    assert packet != other.make_struct({'flag': True})
    with checks.assertRaises(ValueError):
        descriptor.validate(other.make_struct({'flag': True}))
    for fields in (None, [], {}, {'flag': True, 'extra': 1}, {'flag': 7}):
        with checks.assertRaises(ValueError):
            descriptor.make_struct(fields)
    with checks.assertRaises(ValueError):
        wirely.value_types.uint8.make_struct({})

    settings_type = wirely.types.TypeStruct('Settings', {
        'text': wirely.types.String,
        'items': wirely.types.TypeList(wirely.types.TypeOption(packet_type)),
        'inner': packet_type, 'type': wirely.types.ValueType,
    })
    settings_descriptor = wirely.ValueType(settings_type)
    supplied = {'text': 'hello', 'items': [wirely.Some(packet), None],
                'inner': packet, 'type': wirely.value_types.uint8}
    settings = settings_descriptor.make_struct(supplied)
    supplied['text'] = 'changed'
    supplied['items'].append(None)
    assert settings['text'] == 'hello'
    assert settings['items'] == (wirely.Some(packet), None)
    assert settings_descriptor.validate(settings) == settings
    assert not settings_descriptor.supports_json and not settings_descriptor.supports_csv
    with checks.assertRaises(ValueError):
        settings_descriptor.parse('{}')


check_parsing_and_conversions()
check_descriptors_and_lists()
check_unit()
check_times()
check_structs()
