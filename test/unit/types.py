import dataclasses
import enum
import typing
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.runtime.enums
import wirely.runtime.structs


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
        for bits in (8, 16, 32, 64, 128):
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
            # Integers are checked against their type's range; booleans are not integers.
            internal = getattr(wirely.types, ('int' if signed else 'uint') + str(bits))
            for value in (minimum, maximum):
                assert internal.verify_value(value) == value and descriptor.validate(value) == value
            for value in (minimum - 1, maximum + 1, True):
                for check in (internal.verify_value, descriptor.validate):
                    with checks.assertRaises(ValueError):
                        check(value)
    integer = wirely.value_types.uint16
    for operation in (lambda: integer.json_encode('invalid'),
                      lambda: integer.json_decode('invalid'),
                      lambda: integer.to_csv_value('invalid'),
                      lambda: integer.json_encode(42, 'invalid'),
                      lambda: integer.json_decode(42, 'invalid')):
        with checks.assertRaises(ValueError):
            operation()
    assert wirely.value_types.string.json_decode('hello') == 'hello'
    assert wirely.value_types.string.json_encode('hello') == 'hello'
    assert wirely.value_types.time_of_day.parse('11:17') == wirely.TimeOfDay.from_parts(11, 17)
    # Fractions round to the nearest 2**-64 s; times before 24:00 stay before it.
    last = wirely.TimeOfDay(ticks=wirely.TimeOfDay.END_TICKS - 1)
    assert wirely.TimeOfDay.parse('23:59:59.99999999999999999999') == last
    assert str(last) == '23:59:59.99999999999999999995'
    assert wirely.TimeOfDay.parse('00:00:01.1') == wirely.TimeOfDay.from_parts(0, 0, 1, 1_844_674_407_370_955_162)
    assert wirely.value_types.unit.parse('()') == wirely.Unit()
    for descriptor, text in [(wirely.value_types.unit, '0'),
                             (wirely.value_types.time_of_day, '123')]:
        with checks.assertRaises(ValueError):
            descriptor.parse(text)

    descriptor = wirely.ValueType(wirely.types.TypeEnum(
        'Mode', {'Cool': 3, 'EmergencyHeat': 5}, underlying_type=wirely.types.uint8,
    ))
    mode = descriptor.enum_type
    cool = mode.from_name('Cool')
    assert descriptor.parse('Cool') == cool
    assert descriptor.json_encode(cool) == 'Cool'
    assert descriptor.json_encode(mode.from_value(5), 'SnakeCase') == 'emergency_heat'
    assert descriptor.json_decode('Cool') == cool
    for text in ('NotCool', 'cool', 'Mode.Cool'):
        with checks.assertRaises(ValueError):
            descriptor.parse(text)


def check_enum_values():
    # Native code gets EnumValues: a member number and its EnumType.
    descriptor = wirely.ValueType(wirely.types.TypeEnum(
        'Mode', {'Cool': 3, 'Heat': 4}, underlying_type=wirely.types.uint8,
    ))
    mode = descriptor.enum_type
    assert mode is descriptor.enum_type
    assert (mode.name, mode.underlying_type, dict(mode.members), mode.python_type) == (
        'Mode', wirely.value_types.uint8, {'Cool': 3, 'Heat': 4}, None)
    assert dict(mode.names) == {3: 'Cool', 4: 'Heat'}
    cool = mode.from_name('Cool')
    assert (cool.value, cool.name, cool.type) == (3, 'Cool', mode)
    assert cool == mode.from_value(3) and hash(cool) == hash(mode.from_value(3))
    assert cool != mode.from_value(4) and cool != 3
    with checks.assertRaises(dataclasses.FrozenInstanceError):
        cool.value = 4
    for invalid in (lambda: mode.from_name('Off'), lambda: mode.from_value(5),
                    lambda: mode.from_value(True), lambda: cool.native):
        with checks.assertRaises(ValueError):
            invalid()

    # Values of another enum type with the same numbers are different.
    other = wirely.ValueType(wirely.types.TypeEnum(
        'Mode', {'Cool': 3, 'Heat': 4}, underlying_type=wirely.types.uint8,
    ))
    assert cool != other.enum_type.from_value(3)
    for value in (3, other.enum_type.from_value(3)):
        with checks.assertRaisesRegex(ValueError, 'Expected an EnumValue of Mode'):
            descriptor.validate(value)
    assert descriptor.validate(cool) == cool
    with checks.assertRaisesRegex(ValueError, 'is not an enum type'):
        wirely.value_types.uint8.enum_type

    # A Python enum for the type name supplies .native.
    class Mode(enum.IntEnum):
        Cool = 3
        Heat = 4

    with patch.dict(wirely.runtime.enums.native_enum_types, {'Native.Mode': Mode}):
        native = wirely.types.TypeEnum('Native.Mode', {'Cool': 3, 'Heat': 4},
                                       underlying_type=wirely.types.uint8).enum_type
    assert native.python_type is Mode
    assert native.from_value(4).native is Mode.Heat


def check_descriptors_and_lists():
    list_type = wirely.types.TypeList(wirely.types.uint16)
    first = wirely.ValueType(wirely.types.TypeOption(list_type))
    second = wirely.ValueType(wirely.types.TypeOption(wirely.types.TypeList(wirely.types.uint16)))
    assert first == second and first != wirely.value_types.uint16
    assert len({first, second, wirely.value_types.uint16}) == 2
    assert str(first) == 'Option[List[uint16]]'
    assert not first.supports_json and not first.supports_csv
    assert first.validate(wirely.Some([1, 7])) == wirely.Some((1, 7))
    assert first.validate(None) is None
    for value in ([7], wirely.Some(['invalid']), wirely.Some([True, 7]), wirely.Some([70000])):
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
    second = wirely.types.time.TICKS_PER_SECOND
    instant = wirely.MonotonicTime(generation=7, ticks=5 * second)
    equivalent = wirely.MonotonicTime(generation=7, ticks=5 * second)
    span = wirely.Duration.from_seconds(2)
    assert {instant: 'time'}[equivalent] == 'time'
    assert len({span, wirely.Duration(ticks=2 * second)}) == 1
    for value in (instant, span):
        with checks.assertRaises(AttributeError):
            value.ticks = 0
    assert instant != wirely.MonotonicTime(generation=8, ticks=instant.ticks)
    # Generations and timestamps are limited to their uint64 and uint128 storage.
    max_generation, max_ticks = wirely.MonotonicTime.MAX_GENERATION, wirely.MonotonicTime.MAX_TICKS
    for generation, ticks in [(-1, 0), (1, -1), (max_generation + 1, 0), (1, max_ticks + 1)]:
        with checks.assertRaises(ValueError):
            wirely.MonotonicTime(generation=generation, ticks=ticks)
    # The default value's generation 0 is never current.
    default = wirely.types.MonotonicTime.default_value()
    assert default == wirely.MonotonicTime(generation=0, ticks=0)
    assert not wirely.MonotonicTime.is_comparable(instant, default)
    with checks.assertRaises(Exception):
        wirely.MonotonicTime.compare(instant, default)
    assert wirely.Duration.parse('P1DT2H3M4.5S').total_seconds == 93784.5
    # Durations round to the nearest 2**-64 s and are limited to the uint128 range.
    maximum = wirely.Duration.parse('P213503982334601DT7H15.99999999999999999995S')
    assert maximum.ticks == wirely.Duration.MAX_TICKS == 2**128 - 1
    assert wirely.Duration.parse('PT0.1S').ticks == 1_844_674_407_370_955_162
    assert wirely.Duration.parse('PT0.000000000000000000027S').ticks == 0
    assert wirely.Duration.parse('PT0.000000000000000000028S').ticks == 1
    for invalid in [-1, 2**128]:
        with checks.assertRaises(Exception):
            wirely.Duration(ticks=invalid)
    with checks.assertRaises(Exception):
        wirely.Duration.parse('P213503982334601DT7H16S')
    assert wirely.Duration.parse('PT2S') == span
    for text in ('P', 'PT', '-PT1S', 'P1M', 'invalid'):
        with checks.assertRaises(ValueError):
            wirely.value_types.duration.parse(text)
    with checks.assertRaises(Exception):
        wirely.Duration(ticks=-1)
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
    assert default + span == wirely.MonotonicTime(generation=0, ticks=span.ticks)
    assert not wirely.MonotonicTime.is_comparable(later, default + span)
    with checks.assertRaises(Exception):
        later - default


def check_type_names():
    # Types print in Wirely syntax.
    types = wirely.types
    pair = types.TypeStruct('Pair', {'flag': types.boolean})
    nested = types.TypeOption(types.TypeList(types.TypeOption(types.uint8)))
    assert str(nested) == 'Option[List[Option[uint8]]]'
    assert str(types.TypeOption(pair)) == 'Option[Pair]'
    assert str(wirely.ValueType(nested)) == 'Option[List[Option[uint8]]]'


def check_to_text():
    # Values format as Wirely literals; types without literals use fixed forms.
    types = wirely.types
    mode = types.TypeEnum('Example.Mode', {'Off': 0, 'Heat': 1}, underlying_type=types.int8)
    reading = types.TypeStruct('Example.Reading', {'mode': mode, 'level': types.int16})
    cases = [
        (types.unit, wirely.Unit(), '()'),
        (types.boolean, False, 'false'),
        (types.int8, -42, '-42'),
        (types.uint64, 2**64 - 1, '18446744073709551615'),
        (mode, 1, 'Example.Mode.Heat'),
        (types.TimeOfDay, wirely.types.time.TimeOfDay.parse('08:00:30.5'), '08:00:30.5'),
        (types.TimeOfDay, wirely.types.time.TimeOfDay.parse('24:00'), '24:00'),
        (types.MonotonicTime, wirely.MonotonicTime(generation=1, ticks=12345 * 2**64 + 3 * 2**62), 'MonotonicTime(1, 12345.75s)'),
        (types.MonotonicTime, wirely.MonotonicTime(generation=2, ticks=3 * 2**64), 'MonotonicTime(2, 3s)'),
        (types.MonotonicTime, wirely.MonotonicTime(generation=0, ticks=5), 'MonotonicTime(invalid)'),
        (types.TypeOption(reading), wirely.Some((1, -5)), 'Some(Example.Reading { mode: Example.Mode.Heat, level: -5 })'),
        (types.TypeOption(types.uint8), None, 'None'),
        (types.TypeStruct('Empty', {}), (), 'Empty {}'),
        (types.String, 'a"b\\\n\x01é', r'"a\"b\\\n\u{1}é"'),
        (types.TypeList(types.uint8), (1, 2), '[1, 2]'),
        (types.TypeList(types.uint8), (), '[]'),
        (types.TypeValueType(), types.TypeOption(reading), 'Option[Example.Reading]'),
    ]
    for value_type, value, text in cases:
        assert value_type.to_text(value) == text, (value_type, value_type.to_text(value))
    # Text is the shortest decimal that parses back to the same duration.
    second = wirely.types.time.TICKS_PER_SECOND
    for ticks, text in [(0, 'PT0S'), (3 * second // 2, 'PT1.5S'), (1, 'PT0.00000000000000000005S'),
                        (1_844_674_407_370_955_162, 'PT0.1S'),
                        (9_000 * second, 'PT2H30M'), (86_400 * second, 'P1D'),
                        (100_800 * second, 'P1DT4H'), (3_661 * second + second // 4, 'PT1H1M1.25S'),
                        (60 * second - 1, 'PT59.99999999999999999995S'),
                        (2**128 - 1, 'P213503982334601DT7H15.99999999999999999995S')]:
        duration = wirely.Duration(ticks=ticks)
        assert types.Duration.to_text(duration) == text, duration.to_iso()
        assert wirely.Duration.parse(text) == duration
    # Descriptors format native values, including StructValues and EnumValues.
    heat = wirely.ValueType(mode).enum_type.from_name('Heat')
    value = wirely.Some(wirely.ValueType(reading).struct_type.make((heat, 3)))
    text = wirely.ValueType(types.TypeOption(reading)).to_text(value)
    assert text == 'Some(Example.Reading { mode: Example.Mode.Heat, level: 3 })', text
    with checks.assertRaises(ValueError):
        wirely.value_types.uint8.to_text(256)


def check_structs():
    # Native code gets StructValues: field values by ordinal and their StructType.
    packet_type = wirely.types.TypeStruct('Packet', {'flag': wirely.types.boolean, 'count': wirely.types.uint8})
    descriptor = wirely.ValueType(packet_type)
    packet_struct = descriptor.struct_type
    assert packet_struct is descriptor.struct_type
    assert (packet_struct.name, packet_struct.fields, packet_struct.python_type) == (
        'Packet', (('flag', wirely.value_types.boolean), ('count', wirely.value_types.uint8)), None)
    assert (packet_struct.index('flag'), packet_struct.index('count')) == (0, 1)
    packet = packet_struct.make((True, 7))
    assert (packet.type, packet.values) == (packet_struct, (True, 7))
    assert packet == packet_struct.make([True, 7]) and hash(packet) == hash(packet_struct.make((True, 7)))
    assert packet != packet_struct.make((False, 7)) and packet != (True, 7)
    with checks.assertRaises(dataclasses.FrozenInstanceError):
        packet.values = (False, 7)
    for invalid in (lambda: packet_struct.index('extra'), lambda: packet_struct.make((True,)),
                    lambda: packet_struct.make((True, 7, 8)), lambda: packet_struct.make((True, 'seven')),
                    lambda: packet_struct.make((True, 300)),
                    lambda: packet.native, lambda: packet_struct.from_native((True, 7)),
                    lambda: wirely.value_types.uint8.struct_type):
        with checks.assertRaises(ValueError):
            invalid()

    # Values of another struct type with the same fields are different.
    other = wirely.ValueType(wirely.types.TypeStruct('Packet', dict(packet_type.fields))).struct_type
    assert packet != other.make((True, 7))
    for value in (other.make((True, 7)), (True, 7), {'flag': True, 'count': 7}):
        with checks.assertRaisesRegex(ValueError, 'Expected a StructValue of Packet'):
            descriptor.validate(value)
    assert descriptor.validate(packet) == packet

    settings_type = wirely.types.TypeStruct('Settings', {
        'text': wirely.types.String,
        'items': wirely.types.TypeList(wirely.types.TypeOption(packet_type)),
        'inner': packet_type, 'type': wirely.types.ValueType,
    })
    settings_descriptor = wirely.ValueType(settings_type)
    items = [wirely.Some(packet), None]
    settings = settings_descriptor.struct_type.make(('hello', items, packet, wirely.value_types.uint8))
    items.append(None)
    assert settings.values == ('hello', (wirely.Some(packet), None), packet, wirely.value_types.uint8)
    assert settings_descriptor.validate(settings) == settings
    assert not settings_descriptor.supports_json and not settings_descriptor.supports_csv
    with checks.assertRaises(ValueError):
        settings_descriptor.parse('{}')

    # A Python type for the struct type name supplies .native.
    class Point(typing.NamedTuple):
        x: int
        y: int

    with patch.dict(wirely.runtime.structs.native_struct_types, {'Native.Point': Point}):
        point_type = wirely.types.TypeStruct('Native.Point', {'x': wirely.types.int8, 'y': wirely.types.int8}).struct_type
    assert point_type.python_type is Point
    point = point_type.from_native(Point(1, -2))
    assert point.values == (1, -2) and point.native == Point(1, -2)


def check_default_values():
    from unittest.mock import Mock

    types = wirely.types
    enum_type = types.TypeEnum('Defaults', {'First': 99, 'Second': 1}, underlying_type=types.uint16)
    empty = types.TypeStruct('EmptyDefault', {})
    packet = types.TypeStruct('DefaultPacket', {
        'flag': types.boolean, 'mode': enum_type, 'optional': types.TypeOption(types.uint32),
    })
    nested = types.TypeStruct('NestedDefault', {
        'packet': packet, 'empty': empty, 'items': types.TypeList(types.String),
        'type': types.ValueType,
    })
    cases = [(getattr(types, prefix + str(bits)), 0)
             for prefix in ('int', 'uint') for bits in (8, 16, 32, 64, 128)]
    cases += [
        (types.boolean, False), (types.String, ''), (types.unit, wirely.Unit()),
        (types.Duration, wirely.Duration(ticks=0)),
        (types.MonotonicTime, wirely.MonotonicTime(generation=0, ticks=0)),
        (types.TimeOfDay, wirely.TimeOfDay.from_parts(0, 0)), (types.ValueType, types.unit),
        (enum_type, 99), (types.TypeList(types.String), ()),
        (types.TypeList(types.TypeList(types.uint8)), ()),
        (types.TypeOption(enum_type), None),
        (types.TypeOption(types.TypeList(types.String)), None),
        (empty, empty.make_value({})),
        (packet, packet.make_value({'flag': False, 'mode': 99, 'optional': None})),
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
        types.TypeEnum('NoMembers', {}, underlying_type=types.uint8)

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
    enum_type = types.TypeEnum('Mode', {'On': 1}, underlying_type=types.uint64)
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
check_enum_values()
check_descriptors_and_lists()
check_unit()
check_times()
check_type_names()
check_to_text()
check_structs()
check_default_values()

check_runtime_limits()


async def run_test():
    pass
