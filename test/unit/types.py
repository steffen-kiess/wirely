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
    # The default value's generation 0 is never current.
    default = wirely.types.MonotonicTime.default_value()
    assert default == wirely.MonotonicTime(0, 0)
    assert not wirely.MonotonicTime.is_comparable(instant, default)
    with checks.assertRaises(Exception):
        wirely.MonotonicTime.compare(instant, default)
    assert wirely.Duration.parse('P1DT2H3M4.5S').total_seconds == 93784.5
    # Durations are exact and limited to the uint64 nanosecond range.
    maximum = wirely.Duration.parse('PT18446744073.709551615S')
    assert maximum.duration_ns == wirely.Duration.MAX_NS == 2**64 - 1
    assert wirely.Duration.parse('PT18446744073.7S').duration_ns == 18_446_744_073_700_000_000
    for invalid in [-1, 2**64]:
        with checks.assertRaises(Exception):
            wirely.Duration(duration_ns=invalid)
    with checks.assertRaises(Exception):
        wirely.Duration.parse('PT18446744073.709551616S')
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
    # Arithmetic keeps generation 0, so results stay non-comparable to current times.
    assert default + span == wirely.MonotonicTime(0, span.duration_ns)
    assert not wirely.MonotonicTime.is_comparable(later, default + span)
    with checks.assertRaises(Exception):
        later - default


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


def check_default_values():
    from unittest.mock import Mock

    types = wirely.types
    members = enum.Enum('Defaults', {'First': 99, 'Second': 1})
    enum_type = types.TypeEnum('Defaults', members, underlying_type=types.uint16)
    empty = types.TypeStruct('EmptyDefault', {})
    packet = types.TypeStruct('DefaultPacket', {
        'flag': types.boolean, 'mode': enum_type, 'optional': types.TypeOption(types.uint32),
    })
    nested = types.TypeStruct('NestedDefault', {
        'packet': packet, 'empty': empty, 'items': types.TypeList(types.String),
        'type': types.ValueType,
    })
    cases = [(getattr(types, prefix + str(bits)), 0)
             for prefix in ('int', 'uint') for bits in (8, 16, 32, 64)]
    cases += [
        (types.boolean, False), (types.String, ''), (types.unit, wirely.Unit()),
        (types.Duration, wirely.Duration(duration_ns=0)),
        (types.MonotonicTime, wirely.MonotonicTime(0, 0)),
        (types.TimeOfDay, datetime.time(0, 0)), (types.ValueType, types.unit),
        (enum_type, members.First), (types.TypeList(types.String), ()),
        (types.TypeList(types.TypeList(types.uint8)), ()),
        (types.TypeOption(enum_type), None),
        (types.TypeOption(types.TypeList(types.String)), None),
        (empty, empty.make_value({})),
        (packet, packet.make_value({'flag': False, 'mode': members.First, 'optional': None})),
        (nested, nested.make_value({
            'packet': packet.default_value(), 'empty': empty.default_value(),
            'items': (), 'type': types.unit,
        })),
    ]
    for value_type, expected in cases:
        value = value_type.default_value()
        assert type(value) is type(expected)
        assert value_type.equals(value, expected)
        assert value_type.equals(value_type.verify_value(value), value)
        if value_type.is_runtime_type:
            assert value_type.equals(value_type.deserialize(value_type.serialize(value)), value)
    assert packet.serialize(packet.default_value()) == b'\x00\x00\x63\x00' + bytes(5)

    with checks.assertRaisesRegex(ValueError, 'Enum must declare at least one member'):
        types.TypeEnum('NoMembers', enum.Enum('NoMembers', {}), underlying_type=types.uint8)

    unresolved = Mock(spec=types.Type)
    unresolved.contains_parameters = True
    for value_type in (types.TypeOption(unresolved), types.TypeList(unresolved),
                       types.TypeOption(types.TypeList(unresolved))):
        with checks.assertRaisesRegex(ValueError, 'Type parameters require resolution'):
            value_type.default_value()

    wide = types.TypeStruct('WideDefault', {str(i): types.uint64 for i in range(17)})
    many = types.TypeStruct('ManyDefaults', {str(i): types.unit for i in range(128)})
    deep = types.unit
    for i in range(17):
        deep = types.TypeStruct('DeepDefault' + str(i), {'value': deep})
    for value_type in (wide, many, deep, types.TypeOption(wide)):
        assert not value_type.is_runtime_type
        value = value_type.default_value()
        assert value_type.equals(value_type.verify_value(value), value)


def check_runtime_limits():
    from unittest.mock import patch

    from wirely.program import Variable
    from wirely.program.limits import MAX_NESTING_DEPTH, MAX_STORAGE_SIZE, MAX_TOTAL_MEMBER_COUNT

    assert (MAX_STORAGE_SIZE, MAX_NESTING_DEPTH, MAX_TOTAL_MEMBER_COUNT) == (128, 16, 128)
    types = wirely.types
    empty = types.TypeStruct('Empty', {})
    enum_type = types.TypeEnum('Mode', enum.Enum('Mode', {'On': 1}), underlying_type=types.uint64)
    for primitive in (types.unit, types.boolean, types.uint64, enum_type, types.String, types.ValueType):
        assert (primitive.nesting_depth, primitive.total_member_count) == (0, 1)
    assert (empty.nesting_depth, empty.total_member_count, empty.size) == (1, 1, 0)
    assert empty.is_runtime_type and empty.runtime_type_error() is None

    pair = types.TypeStruct('Pair', {'first': types.unit, 'second': empty})
    repeated = types.TypeStruct('Repeated', {'first': pair, 'second': pair})
    assert (pair.nesting_depth, pair.total_member_count) == (2, 3)
    assert (repeated.nesting_depth, repeated.total_member_count) == (3, 7)
    items = types.TypeList(types.TypeOption(repeated))
    assert (items.nesting_depth, items.total_member_count) == (5, 9)
    assert not items.has_runtime_representation and not items.is_runtime_type
    assert 'parameter-only' in items.runtime_type_error()
    settings = types.TypeStruct('Settings', {'payload': types.TypeOption(types.String)})
    assert not settings.has_runtime_representation
    assert settings.runtime_type_error() == 'field payload: inner type: String is parameter-only'

    depth = types.unit
    for _ in range(MAX_NESTING_DEPTH):
        depth = types.TypeOption(depth)
    assert depth.nesting_depth == MAX_NESTING_DEPTH and depth.is_runtime_type
    too_deep = types.TypeOption(depth)
    assert too_deep.runtime_type_error() == 'nesting depth 17 exceeds 16'

    members = types.TypeStruct('Members', {str(i): empty for i in range(127)})
    assert members.total_member_count == MAX_TOTAL_MEMBER_COUNT and members.size == 0
    assert members.is_runtime_type
    too_many = types.TypeOption(members)
    assert too_many.has_runtime_representation
    assert too_many.runtime_type_error() == 'total member count 129 exceeds 128'
    assert too_many.verify_value(None) is None
    assert too_many.equals(None, None)

    storage = types.TypeStruct('Storage', {str(i): types.uint64 for i in range(16)})
    assert storage.size == MAX_STORAGE_SIZE and storage.is_runtime_type
    too_large = types.TypeOption(storage)
    assert too_large.runtime_type_error() == 'storage size 136 bytes exceeds 128 bytes'
    # A presence byte can fit in the final field's unused tail padding.
    padded = types.TypeStruct('Padded', {
        **{str(i): types.uint64 for i in range(15)}, 'last': types.uint8,
    })
    assert (padded.data_size, padded.size) == (121, 128)
    assert types.TypeOption(padded).size == 128
    assert types.TypeOption(padded).is_runtime_type
    # Storage size, rather than encoded data size, is limited.
    padded_overflow = types.TypeStruct('PaddedOverflow', {
        **dict(storage.fields), 'last': types.uint8,
    })
    assert padded_overflow.runtime_type_error() == 'storage size 136 bytes exceeds 128 bytes'

    for value_type, value in (
            (storage, storage.make_value({str(i): i for i in range(16)})),
            (members, members.make_value({str(i): empty.make_value({}) for i in range(127)})),
            (depth, None)):
        assert value_type.equals(value, value_type.deserialize(value_type.serialize(value)))
        Variable('boundary', value_type)

    # Representation support takes precedence; then depth, count, and storage.
    unsupported = types.TypeStruct('Unsupported', {'large': too_large, 'text': types.String})
    assert unsupported.runtime_type_error() == 'field text: String is parameter-only'
    multiple_limits = types.TypeStruct('MultipleLimits', {str(i): types.uint64 for i in range(128)})
    assert multiple_limits.runtime_type_error() == 'total member count 129 exceeds 128'
    deep_and_wide = types.TypeStruct('DeepAndWide', {'deep': too_deep, 'wide': multiple_limits})
    assert deep_and_wide.runtime_type_error() == 'nesting depth 18 exceeds 16'

    for value_type in (types.String, settings, too_deep, too_many, too_large):
        assert not value_type.is_runtime_type
        reason = value_type.runtime_type_error()
        for operation in (lambda: value_type.size, lambda: value_type.data_size,
                          lambda: value_type.alignment, lambda: value_type.serialize(None),
                          lambda: value_type.deserialize(b''), lambda: Variable('value', value_type)):
            with checks.assertRaises(ValueError) as raised:
                operation()
            assert reason in str(raised.exception)
    with patch.object(types.Type, 'runtime_type_error', side_effect=AssertionError('formatted a diagnostic')):
        assert storage.is_runtime_type
        assert not too_large.is_runtime_type
        assert not settings.is_runtime_type


check_parsing_and_conversions()
check_descriptors_and_lists()
check_unit()
check_times()
check_structs()
check_default_values()

check_runtime_limits()
