import typing
from unittest import TestCase
from unittest.mock import patch

from wirely.program.limits import is_runtime_type
from wirely.runtime import value_types
from wirely.runtime.extensions import NativeEnum, NativeStruct, NativeTypes
from wirely.types.open_enum import OpenIntEnum
from wirely.generated import extension as builtin_extension
import wirely
from tests.infra.support import internal, library_type


checks = TestCase()


def check_parsing_and_conversions():
    boolean = value_types.boolean
    for text, expected in [('0', False), ('false', False), ('tRUE', True)]:
        assert boolean.parse(text, native_types=builtin_extension.native_types).extract(boolean, native_types=builtin_extension.native_types) is expected
    for value in (False, True):
        dynamic = boolean.from_native(value, native_types=builtin_extension.native_types)
        assert boolean.json_decode(dynamic.json_encode()) == dynamic
        assert dynamic.to_csv_value() == (str(value).lower(), False)
    with checks.assertRaises(ValueError):
        boolean.parse('2', native_types=builtin_extension.native_types)

    for signed in (False, True):
        for bits in (8, 16, 32, 64, 128):
            descriptor = getattr(value_types, ('int' if signed else 'uint') + str(bits))
            integer = wirely.types.integers.integer_class(bits, signed)
            minimum = -(2 ** (bits - 1)) if signed else 0
            maximum = 2 ** (bits - (1 if signed else 0)) - 1
            for value in (minimum, 0, maximum):
                dynamic = descriptor.from_native(integer(value), native_types=builtin_extension.native_types)
                assert descriptor.parse(str(value), native_types=builtin_extension.native_types) == dynamic
                assert descriptor.json_decode(dynamic.json_encode()) == dynamic
                assert dynamic.json_encode() == value
                assert dynamic.to_csv_value() == (str(value), False)
            for text in (str(minimum - 1), str(maximum + 1), 'false'):
                with checks.assertRaises(ValueError):
                    descriptor.parse(text, native_types=builtin_extension.native_types)
            # Integers are checked against their type's range; booleans are not integers.
            internal = getattr(wirely.program.types, ('int' if signed else 'uint') + str(bits))
            for value in (minimum, maximum):
                assert internal.verify_value(value) == value
            for value in (minimum - 1, maximum + 1, True):
                for check in (internal.verify_value, descriptor.json_decode):
                    with checks.assertRaises(ValueError):
                        check(value)
    integer = value_types.uint16
    for operation in (lambda: integer.json_decode('invalid'),
                      lambda: integer.from_native(wirely.UInt16(42), native_types=builtin_extension.native_types).json_encode('invalid'),
                      lambda: integer.json_decode(42, 'invalid')):
        with checks.assertRaises(ValueError):
            operation()
    string = value_types.string
    assert string.json_decode('hello').extract(string, native_types=builtin_extension.native_types) == 'hello'
    assert string.from_native('hello', native_types=builtin_extension.native_types).json_encode() == 'hello'
    time_of_day = value_types.ValueType(library_type('Schedule.TimeOfDay'))
    assert time_of_day.parse('11:17', native_types=builtin_extension.native_types).extract(time_of_day, native_types=builtin_extension.native_types) == wirely.TimeOfDay.from_parts(11, 17)
    # Fractions round to the nearest 2**-64 s; times before 24:00 stay before it.
    last = wirely.TimeOfDay(ticks=wirely.TimeOfDay.END_TICKS - 1)
    assert wirely.TimeOfDay.parse('23:59:59.99999999999999999999') == last
    assert str(last) == '23:59:59.99999999999999999995'
    assert wirely.TimeOfDay.parse('00:00:01.1') == wirely.TimeOfDay.from_parts(0, 0, 1, 1_844_674_407_370_955_162)
    assert value_types.unit.parse('()', native_types=builtin_extension.native_types).extract(value_types.unit, native_types=builtin_extension.native_types) == wirely.Unit()
    duration = value_types.ValueType(library_type('Std.Duration'))
    for descriptor, text in [(value_types.unit, '0'),
                             (time_of_day, '123'),
                             (duration, 'x'),
                             (duration, 'P')]:
        with checks.assertRaises(ValueError):
            descriptor.parse(text, native_types=builtin_extension.native_types)
    # Internal types raise ValueError too, also where parsing is not implemented.
    for value_type, text in [(wirely.program.types.boolean, '2'), (wirely.program.types.unit, '0'), (wirely.program.types.String, 'x')]:
        with checks.assertRaises(ValueError):
            value_type.parse(text)

    descriptor = value_types.ValueType(wirely.program.types.TypeEnum(
        'Mode', {'Cool': 3, 'EmergencyHeat': 5}, is_open=True, underlying_type=wirely.program.types.uint8, is_json_serializable=True,
    ))
    cool = descriptor.parse('Cool', native_types=builtin_extension.native_types)
    assert cool.to_text(native_types=builtin_extension.native_types) == 'Mode.Cool'
    assert cool.json_encode() == 'Cool'
    assert descriptor.parse('EmergencyHeat', native_types=builtin_extension.native_types).json_encode('SnakeCase') == 'emergency_heat'
    assert descriptor.json_decode('Cool') == cool
    for text in ('NotCool', 'cool', 'Mode.Cool'):
        with checks.assertRaises(ValueError):
            descriptor.parse(text, native_types=builtin_extension.native_types)


def check_enum_values():
    # Enum values are member numbers; values of another enum type with the same numbers differ.
    mode_type = wirely.program.types.TypeEnum('Mode', {'Cool': 3, 'Heat': 4}, is_open=True, underlying_type=wirely.program.types.uint8, is_json_serializable=True)
    descriptor = value_types.ValueType(mode_type)
    cool = descriptor.parse('Cool', native_types=builtin_extension.native_types)
    assert cool == descriptor.json_decode('Cool') and hash(cool) == hash(descriptor.json_decode('Cool'))
    assert cool != descriptor.parse('Heat', native_types=builtin_extension.native_types)
    other = value_types.ValueType(wirely.program.types.TypeEnum(
        'Mode', {'Cool': 3, 'Heat': 4}, is_open=True, underlying_type=wirely.program.types.uint8,
    ))
    assert cool != other.parse('Cool', native_types=builtin_extension.native_types)
    assert (mode_type.member_name(3), dict(mode_type.members)) == ('Cool', {'Cool': 3, 'Heat': 4})
    for invalid in (lambda: descriptor.parse('Off', native_types=builtin_extension.native_types), lambda: mode_type.verify_value(256),
                    lambda: mode_type.verify_value(True), lambda: cool.to_native(native_types=builtin_extension.native_types)):
        with checks.assertRaises(ValueError):
            invalid()

    # The Python enum registered under a native enum's native name is the native representation.
    class Mode(OpenIntEnum, minimum=0, maximum=255):
        Cool = 3
        Heat = 4

    registry = {'Native.Mode': NativeEnum(Mode, is_open=True, underlying_type='uint8')}
    native = value_types.ValueType(wirely.program.types.TypeEnum('Example.Mode', {'Cool': 3, 'Heat': 4}, is_open=True,
                                                    underlying_type=wirely.program.types.uint8, native_name='Native.Mode', is_json_serializable=True))
    native_types = NativeTypes(enums=registry)
    assert native.parse('Heat', native_types=native_types).extract(native, native_types=native_types) is Mode.Heat
    unknown = native.json_decode(5).to_native(native_types=native_types)
    assert type(unknown) is Mode and unknown == 5 and unknown.name is None
    assert native.from_native(Mode.Cool, native_types=native_types) == native.parse('Cool', native_types=native_types)
    with checks.assertRaises(ValueError):
        native.from_native(3, native_types=native_types)
    # Enums with the native name of another enum but not declared native have no native representation.
    plain = value_types.ValueType(wirely.program.types.TypeEnum('Native.Mode', {'Cool': 3}, is_open=True,
                                                   underlying_type=wirely.program.types.uint8))
    with checks.assertRaisesRegex(ValueError, 'has no native representation'):
        native_types = NativeTypes(enums=registry)
        plain.parse('Cool', native_types=native_types).to_native(native_types=native_types)


def check_open_int_enums():
    from wirely.generated.enums import KNXProtocol

    class Offset(OpenIntEnum, minimum=-128, maximum=127):
        Zero = 0

    # Unknown numbers of the underlying type are unnamed members; known
    # numbers remain the declared members.
    for unknown in (Offset(-128), Offset(127), KNXProtocol(9)):
        assert isinstance(unknown, type(unknown)) and unknown.name is None
        assert unknown == unknown.value and hash(unknown) == hash(unknown.value)
        assert unknown not in list(type(unknown))
    assert Offset(0) is Offset.Zero and KNXProtocol(1) is KNXProtocol.TCP
    assert repr(Offset(-5)) == '<Offset: -5>' and repr(Offset.Zero) == '<Offset.Zero: 0>'
    for value, error in ((128, '128 does not fit Offset'), (-129, '-129 does not fit Offset'),
                         (5.0, 'is not a valid .*Offset'), ('5', 'is not a valid .*Offset')):
        with checks.assertRaisesRegex(ValueError, error):
            Offset(value)
    with checks.assertRaisesRegex(ValueError, '256 does not fit KNXProtocol'):
        KNXProtocol(256)


def check_open_and_closed_enums():
    from wirely.program import types

    for count, underlying in ((1, types.uint8), (256, types.uint8),
                              (257, types.uint16), (65536, types.uint16),
                              (65537, types.uint32), (0xffffffff, types.uint32)):
        assert types.closed_enum_underlying_type(count) is underlying
    for count in (0, -1, 0x100000000, True):
        with checks.assertRaises(ValueError):
            types.closed_enum_underlying_type(count)

    closed = types.TypeEnum('State', {'Idle': 0, 'Running': 1},
                            is_open=False, underlying_type=types.uint8)
    assert closed.default_value() == 0
    assert not closed.is_open
    for value in (0, 1):
        assert closed.verify_value(value) == value
    for value in (-1, 2, 255, True):
        with checks.assertRaises(ValueError):
            closed.verify_value(value)
    for members, underlying in (({'One': 1}, types.uint8),
                                ({'First': 0, 'Second': 2}, types.uint8),
                                ({'First': 0}, types.int8),
                                ({'First': 0}, types.uint64)):
        with checks.assertRaises(ValueError):
            types.TypeEnum('Bad', members, is_open=False, underlying_type=underlying)

    for underlying in (types.int8, types.uint8, types.int128, types.uint128):
        for members in ({}, {'Named': 1}):
            opened = types.TypeEnum('Open', members, is_open=True, underlying_type=underlying, is_json_serializable=True)
            descriptor = value_types.ValueType(opened)
            assert opened.default_value() == 0
            assert opened.is_open
            for number in (underlying.min_value, 0, underlying.max_value):
                dynamic = descriptor.parse(str(number), native_types=builtin_extension.native_types)
                assert dynamic.to_text(native_types=builtin_extension.native_types) == str(number)
                assert dynamic.json_encode() == number
                assert dynamic.json_encode('SnakeCase') == number
                assert descriptor.json_decode(number) == dynamic
                assert repr(dynamic) == f'DynamicValue(Open: {number})'
                with checks.assertRaises(ValueError):
                    opened.member_name(number)
            for number in (underlying.min_value - 1, underlying.max_value + 1, True, 1.0):
                with checks.assertRaises(ValueError):
                    opened.verify_value(number)
                with checks.assertRaises(ValueError):
                    descriptor.json_decode(number)

    opened = types.TypeEnum('Open', {}, is_open=True, underlying_type=types.uint8, is_json_serializable=True)
    nested_type = types.TypeStruct('Packet', {'mode': types.TypeOption(opened)})
    assert nested_type.verify_value((wirely.Some(255),)) == (wirely.Some(255),)


def check_descriptors_and_lists():
    list_type = wirely.program.types.TypeList(wirely.program.types.uint16)
    first = value_types.ValueType(wirely.program.types.TypeOption(list_type))
    second = value_types.ValueType(wirely.program.types.TypeOption(wirely.program.types.TypeList(wirely.program.types.uint16)))
    assert first == second and first != value_types.uint16
    assert len({first, second, value_types.uint16}) == 2
    assert str(first) == 'Option[List[uint16]]'
    assert not first.supports_json and not first.supports_csv
    number = wirely.UInt16
    assert first.from_native(wirely.Some((number(1), number(7))), native_types=builtin_extension.native_types).extract(first, native_types=builtin_extension.native_types) == wirely.Some((number(1), number(7)))
    assert first.from_native(None, native_types=builtin_extension.native_types).extract(first, native_types=builtin_extension.native_types) is None
    for value in ((number(7),), wirely.Some([number(1)]), wirely.Some(('invalid',)),
                  wirely.Some((True, number(7))), wirely.Some((7,))):
        with checks.assertRaises(ValueError):
            first.from_native(value, native_types=builtin_extension.native_types)
    for operation in (lambda: first.from_native(None, native_types=builtin_extension.native_types).json_encode(),
                      lambda: first.json_decode(None),
                      lambda: first.from_native(None, native_types=builtin_extension.native_types).to_csv_value(), lambda: first.parse('7', native_types=builtin_extension.native_types)):
        with checks.assertRaises(ValueError):
            operation()

    nested = value_types.ValueType(wirely.program.types.TypeList(list_type))
    native = ((number(1), number(2)), (number(3),))
    assert nested.from_native(native, native_types=builtin_extension.native_types).extract(nested, native_types=builtin_extension.native_types) == native
    for invalid in ('12', {1, 2}, iter([1, 2]), [number(1)], ('invalid',)):
        with checks.assertRaises(ValueError):
            value_types.ValueType(list_type).from_native(invalid, native_types=builtin_extension.native_types)

    optional = value_types.ValueType(wirely.program.types.TypeOption(wirely.program.types.uint16))
    assert optional.parse('none', native_types=builtin_extension.native_types).extract(optional, native_types=builtin_extension.native_types) is None
    assert optional.parse('7', native_types=builtin_extension.native_types).extract(optional, native_types=builtin_extension.native_types) == wirely.Some(number(7))
    assert optional.supports_csv
    assert optional.from_native(None, native_types=builtin_extension.native_types).to_csv_value() == ('', False)
    assert optional.parse('7', native_types=builtin_extension.native_types).to_csv_value() == ('7', False)
    values = value_types.ValueType(wirely.program.types.TypeList(wirely.program.types.TypeOption(wirely.program.types.ValueType)))
    supplied = (wirely.Some(value_types.uint16), None)
    assert values.from_native(supplied, native_types=builtin_extension.native_types).extract(values, native_types=builtin_extension.native_types) == supplied
    with checks.assertRaises(ValueError):
        values.from_native((wirely.Some(wirely.program.types.uint16),), native_types=builtin_extension.native_types)
    value_type = value_types.value_type
    with checks.assertRaises(ValueError):
        value_type.from_native(wirely.program.types.uint16, native_types=builtin_extension.native_types)
    assert value_type.from_native(value_types.uint16, native_types=builtin_extension.native_types).extract(value_type, native_types=builtin_extension.native_types) == value_types.uint16


def check_integers():
    import pickle

    classes = (wirely.Int8, wirely.Int16, wirely.Int32, wirely.Int64, wirely.Int128,
               wirely.UInt8, wirely.UInt16, wirely.UInt32, wirely.UInt64, wirely.UInt128)
    for cls in classes:
        assert wirely.types.integers.integer_class(cls.bits, cls.signed) is cls
        minimum = -(2 ** (cls.bits - 1)) if cls.signed else 0
        maximum = 2 ** (cls.bits - (1 if cls.signed else 0)) - 1
        for value in (minimum, 0, maximum):
            number = cls(value)
            assert number.value == value and number == cls(value) and hash(number) == hash(cls(value))
            assert pickle.loads(pickle.dumps(number)) == number
        for value, message in ((minimum - 1, 'out of range'), (maximum + 1, 'out of range'),
                               (True, 'Expected an int'), (1.0, 'Expected an int'), (cls(0), 'Expected an int')):
            with checks.assertRaisesRegex(ValueError, message):
                cls(value)
    with checks.assertRaises(ValueError):
        wirely.types.integers.integer_class(24, True)

    # Values compare only with values of the same class.
    number = wirely.UInt8(7)
    assert number != 7 and number != wirely.Int8(7) and number != wirely.UInt16(7)
    assert len({wirely.UInt8(7), wirely.Int8(7), wirely.UInt8(7)}) == 2
    assert wirely.UInt8(1) < number <= wirely.UInt8(7) and max(wirely.UInt8(9), number) == wirely.UInt8(9)
    for operation in (lambda: number < 8, lambda: number < wirely.Int8(8), lambda: number + 1, lambda: [0][number]):
        with checks.assertRaises(TypeError):
            operation()
    assert repr(wirely.Int16(-3)) == 'Int16(-3)'
    for operation in (lambda: setattr(number, '_value', 8), lambda: setattr(number, 'extra', 8),
                      lambda: delattr(number, '_value')):
        with checks.assertRaises(AttributeError):
            operation()
    with checks.assertRaises(TypeError):
        wirely.types.integers.Integer(1)


def check_dynamic_values():
    uint16 = value_types.uint16
    seven = uint16.from_native(wirely.UInt16(7), native_types=builtin_extension.native_types)
    assert seven.type == uint16 and seven.extract(uint16, native_types=builtin_extension.native_types) == wirely.UInt16(7) and seven.to_native(native_types=builtin_extension.native_types) == wirely.UInt16(7)
    assert seven == uint16.from_native(wirely.UInt16(7), native_types=builtin_extension.native_types) and hash(seven) == hash(uint16.from_native(wirely.UInt16(7), native_types=builtin_extension.native_types))
    assert seven != uint16.from_native(wirely.UInt16(8), native_types=builtin_extension.native_types)
    assert seven != value_types.uint8.from_native(wirely.UInt8(7), native_types=builtin_extension.native_types) and seven != wirely.UInt16(7)
    assert (seven.to_text(native_types=builtin_extension.native_types), seven.json_encode(), seven.to_csv_value()) == ('7', 7, ('7', False))
    assert repr(seven) == 'DynamicValue(uint16: 7)'
    with checks.assertRaisesRegex(ValueError, 'Expected a value of type `uint8`, got `uint16`'):
        seven.extract(value_types.uint8, native_types=builtin_extension.native_types)
    for value in (7, wirely.UInt8(7), True, None):
        with checks.assertRaises(ValueError):
            uint16.from_native(value, native_types=builtin_extension.native_types)
    for operation in (lambda: value_types.DynamicValue(), lambda: __import__('pickle').dumps(seven)):
        with checks.assertRaises(TypeError):
            operation()
    with checks.assertRaises(AttributeError):
        seven._value = 8

    # Composite descriptors keep nested options and lists.
    nested = value_types.option(value_types.option(value_types.list_of(uint16)))
    assert str(nested) == 'Option[Option[List[uint16]]]'
    for native in (None, wirely.Some(None), wirely.Some(wirely.Some((wirely.UInt16(1), wirely.UInt16(2))))):
        value = nested.from_native(native, native_types=builtin_extension.native_types)
        assert value.extract(nested, native_types=builtin_extension.native_types) == native and value.type == nested
    assert nested.from_native(None, native_types=builtin_extension.native_types) != nested.from_native(wirely.Some(None), native_types=builtin_extension.native_types)
    for invalid in (wirely.Some(wirely.Some([wirely.UInt16(1)])), wirely.Some(wirely.Some((1,))), wirely.Some(7)):
        with checks.assertRaises(ValueError):
            nested.from_native(invalid, native_types=builtin_extension.native_types)

    # Formatting and conversions depend on the type.
    text = value_types.string.from_native('a"b', native_types=builtin_extension.native_types)
    assert (text.to_text(native_types=builtin_extension.native_types), text.json_encode()) == ('"a\\"b"', 'a"b')
    time = value_types.ValueType(library_type('Schedule.TimeOfDay')).from_native(wirely.TimeOfDay.from_parts(11, 17), native_types=builtin_extension.native_types)
    for operation in (time.json_encode, time.to_csv_value):
        with checks.assertRaises(ValueError):
            operation()
    descriptor = value_types.value_type.from_native(uint16, native_types=builtin_extension.native_types)
    assert descriptor.extract(value_types.value_type, native_types=builtin_extension.native_types) == uint16 and descriptor.to_text(native_types=builtin_extension.native_types) == 'uint16'

    # Enums and structs that are not native have no native representation.
    mode = value_types.ValueType(wirely.program.types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False,
                                                  underlying_type=wirely.program.types.uint8))
    with checks.assertRaisesRegex(ValueError, 'Enum `Mode` has no native representation'):
        mode.from_native(1, native_types=builtin_extension.native_types)


def check_unit():
    unit = wirely.Unit()
    assert unit == wirely.Unit()
    checks.assertNotEqual(unit, None)
    assert len({unit, wirely.Unit()}) == 1
    with checks.assertRaises((AttributeError, TypeError)):
        unit.value = True
    for invalid in (None, (), False, wirely.Some(unit)):
        with checks.assertRaises(ValueError):
            value_types.unit.from_native(invalid, native_types=builtin_extension.native_types)
    with checks.assertRaises(ValueError):
        value_types.unit.from_native(unit, native_types=builtin_extension.native_types).json_encode()


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
    default = wirely.MonotonicTime(generation=0, ticks=0)
    assert library_type('Timer.MonotonicTime').default_value() == internal(default)
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
            value_types.ValueType(library_type('Std.Duration')).parse(text, native_types=builtin_extension.native_types)
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
    types = wirely.program.types
    pair = types.TypeStruct('Pair', {'flag': types.boolean})
    nested = types.TypeOption(types.TypeList(types.TypeOption(types.uint8)))
    assert str(nested) == 'Option[List[Option[uint8]]]'
    assert str(types.TypeOption(pair)) == 'Option[Pair]'
    assert str(value_types.ValueType(nested)) == 'Option[List[Option[uint8]]]'


def check_to_text():
    # Values format as Wirely literals; types without literals use fixed forms.
    types = wirely.program.types
    mode = types.TypeEnum('Example.Mode', {'Off': 0, 'Heat': 1}, is_open=True, underlying_type=types.int8)
    reading = types.TypeStruct('Example.Reading', {'mode': mode, 'level': types.int16})
    time_of_day, monotonic_time = library_type('Schedule.TimeOfDay'), library_type('Timer.MonotonicTime')
    cases = [
        (types.unit, wirely.Unit(), '()'),
        (types.boolean, False, 'false'),
        (types.int8, -42, '-42'),
        (types.uint64, 2**64 - 1, '18446744073709551615'),
        (mode, 1, 'Example.Mode.Heat'),
        (time_of_day, internal(wirely.TimeOfDay.parse('08:00:30.5')), '08:00:30.5'),
        (time_of_day, internal(wirely.TimeOfDay.parse('24:00')), '24:00'),
        (monotonic_time, internal(wirely.MonotonicTime(generation=1, ticks=12345 * 2**64 + 3 * 2**62)),
         'MonotonicTime(1, 12345.75s)'),
        (monotonic_time, internal(wirely.MonotonicTime(generation=2, ticks=3 * 2**64)), 'MonotonicTime(2, 3s)'),
        (monotonic_time, internal(wirely.MonotonicTime(generation=0, ticks=5)), 'MonotonicTime(invalid)'),
        (types.TypeOption(reading), wirely.Some((1, -5)), 'Some(Example.Reading { mode: Example.Mode.Heat, level: -5 })'),
        (types.TypeOption(types.uint8), None, 'None'),
        (types.TypeStruct('Empty', {}), (), 'Empty {}'),
        (types.String, 'a"b\\\n\x01é', r'"a\"b\\\n\u{1}é"'),
        (types.TypeList(types.uint8), (1, 2), '[1, 2]'),
        (types.TypeList(types.uint8), (), '[]'),
        (types.TypeValueType(), types.TypeOption(reading), 'Option[Example.Reading]'),
    ]
    native_types = builtin_extension.native_types
    for value_type, value, text in cases:
        assert value_type.to_text(value, native_types=native_types) == text, value_type
    # Opaque structs need the native types implementing them, also inside other types.
    noon = internal(wirely.TimeOfDay.parse('12:00'))
    for value_type, value in [(time_of_day, noon), (types.TypeList(time_of_day), (noon,))]:
        with checks.assertRaisesRegex(ValueError, 'needs native types'):
            value_type.to_text(value)
    # Text is the shortest decimal that parses back to the same duration.
    second = wirely.types.time.TICKS_PER_SECOND
    for ticks, text in [(0, 'PT0S'), (3 * second // 2, 'PT1.5S'), (1, 'PT0.00000000000000000005S'),
                        (1_844_674_407_370_955_162, 'PT0.1S'),
                        (9_000 * second, 'PT2H30M'), (86_400 * second, 'P1D'),
                        (100_800 * second, 'P1DT4H'), (3_661 * second + second // 4, 'PT1H1M1.25S'),
                        (60 * second - 1, 'PT59.99999999999999999995S'),
                        (2**128 - 1, 'P213503982334601DT7H15.99999999999999999995S')]:
        duration = wirely.Duration(ticks=ticks)
        assert library_type('Std.Duration').to_text(internal(duration), native_types=native_types) == text, duration.to_iso()
        assert wirely.Duration.parse(text) == duration
    # DynamicValues format the same way.
    optional = value_types.option(value_types.uint8)
    assert optional.from_native(wirely.Some(wirely.UInt8(3)), native_types=builtin_extension.native_types).to_text(native_types=builtin_extension.native_types) == 'Some(3)'
    with checks.assertRaises(ValueError):
        value_types.uint8.from_native(256, native_types=builtin_extension.native_types)


def check_structs():
    # Struct values are tuples of their field values in field order.
    packet_type = wirely.program.types.TypeStruct('Packet', {'flag': wirely.program.types.boolean, 'count': wirely.program.types.uint8})
    assert (packet_type.index('flag'), packet_type.index('count')) == (0, 1)
    assert packet_type.verify_value((True, 7)) == (True, 7)
    for invalid in ((True,), (True, 7, 8), (True, 'seven'), (True, 300), [True, 7]):
        with checks.assertRaises(ValueError):
            packet_type.verify_value(invalid)
    with checks.assertRaises(ValueError):
        packet_type.index('extra')

    # Values of another struct type with the same fields are different.
    packet = value_types.DynamicValue._make(packet_type, (True, 7))
    other = wirely.program.types.TypeStruct('Packet', dict(packet_type.fields))
    assert packet == value_types.DynamicValue._make(packet_type, (True, 7))
    assert packet != value_types.DynamicValue._make(other, (True, 7))
    assert packet != value_types.DynamicValue._make(packet_type, (False, 7))

    settings_type = wirely.program.types.TypeStruct('Settings', {
        'text': wirely.program.types.String,
        'items': wirely.program.types.TypeList(wirely.program.types.TypeOption(packet_type)),
        'inner': packet_type, 'type': wirely.program.types.ValueType,
    })
    settings_descriptor = value_types.ValueType(settings_type)
    assert not settings_descriptor.supports_json and not settings_descriptor.supports_csv
    with checks.assertRaises(ValueError):
        settings_descriptor.parse('{}', native_types=builtin_extension.native_types)

    # The Python type registered under a native struct's native name is the native representation.
    class Point(typing.NamedTuple):
        x: wirely.Int8
        y: wirely.Int8

    fields = (wirely.runtime.signatures.Int(8, True),) * 2
    registry = {'Native.Point': NativeStruct(Point, fields)}
    point_fields = {'x': wirely.program.types.int8, 'y': wirely.program.types.int8}
    point_type = value_types.ValueType(wirely.program.types.TypeStruct('Example.Point', point_fields, native_name='Native.Point'))
    plain_type = value_types.ValueType(wirely.program.types.TypeStruct('Native.Point', point_fields))
    native_types = NativeTypes(structs=registry)
    point = Point(wirely.Int8(1), wirely.Int8(-2))
    assert point_type.from_native(point, native_types=native_types).extract(point_type, native_types=native_types) == point
    for call in (lambda: point_type.from_native((wirely.Int8(1), wirely.Int8(-2)), native_types=native_types),
                 lambda: point_type.from_native(Point(1, -2), native_types=native_types), lambda: plain_type.from_native(point, native_types=native_types)):
        with checks.assertRaises(ValueError):
            call()


def check_unnamed_types():
    # Types loaded without symbols have no type, field, or member names.
    types = wirely.program.types
    point = types.TypeStruct(None, (types.uint8, types.boolean))
    assert point.field_types == (types.uint8, types.boolean) and point.field_names is None
    assert point.to_text((7, True)) == 'struct { 7, true }'
    assert point.deserialize(point.serialize((7, True))) == (7, True)
    native = types.TypeStruct(None, (), native_name='Native.Empty')
    assert native.to_text(()) == 'Native.Empty {}'
    for convenience in (lambda: point.fields, lambda: point.make_value({}), lambda: point.index('x')):
        with checks.assertRaisesRegex(ValueError, 'has no field names'):
            convenience()

    closed = types.TypeEnum(None, {}, is_open=False, underlying_type=types.uint8, member_count=3)
    assert (closed.member_count, closed.to_text(2), closed.parse('2')) == (3, '2', 2)
    with checks.assertRaises(ValueError):
        closed.verify_value(3)
    partial = types.TypeEnum(None, {'First': 0}, is_open=False, underlying_type=types.uint8, member_count=2)
    assert partial.to_text(1) == '1' and partial.parse('1') == 1
    named = types.TypeEnum(None, {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8, is_json_serializable=True)
    assert (str(named), named.to_text(1)) == ('enum', 'On')

    # Enabled enums default to member names; struct metadata requires explicit names.
    assert named.json_names == {0: 'Off', 1: 'On'} and named.json_encode(1, None) == 'On'
    level = types.TypeEnum('Level', {'Low': 0}, is_open=True, underlying_type=types.uint8,
                           json_names={0: 'low', 5: 'high'}, is_json_serializable=True)
    assert (level.json_encode(5, None), level.json_encode(0, None), level.json_encode(7, None)) == ('high', 'low', 7)
    assert (level.json_decode('high', None), level.json_decode('7', None), level.to_text(5)) == (5, 7, '5')
    with checks.assertRaisesRegex(ValueError, 'Unknown member'):
        level.json_decode('Low', None)
    assert closed.json_names == {} and point.json_names is None
    renamed = types.TypeStruct('Pair', {'flag': types.boolean}, json_names=('Flag',))
    assert renamed.json_names == ('Flag',)
    assert types.TypeStruct('Pair', {'flag': types.boolean}).json_names is None


def check_json_defaults():
    from wirely.program import types

    mode = types.TypeEnum('Plain', {'Off': 0}, is_open=False, underlying_type=types.uint8)
    descriptor = value_types.ValueType(mode)
    assert not descriptor.supports_json and mode.json_names == {}
    with checks.assertRaises(ValueError):
        descriptor.json_decode('Off')
    with checks.assertRaises(ValueError):
        descriptor.parse('Off', native_types=builtin_extension.native_types).json_encode()
    record = types.TypeStruct('Record', {'mode': mode})
    assert record.json_names is None and not value_types.ValueType(record).supports_json
    assert types.TypeStruct('Empty', {}).json_names is None
    assert types.TypeStruct('Empty', {}, json_names=()).json_names == ()


def check_default_values():
    from unittest.mock import Mock

    types = wirely.program.types
    enum_type = types.TypeEnum('Defaults', {'First': 99, 'Second': 1}, is_open=True, underlying_type=types.uint16)
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
        (library_type('Std.Duration'), internal(wirely.Duration(ticks=0))),
        (library_type('Timer.MonotonicTime'), internal(wirely.MonotonicTime(generation=0, ticks=0))),
        (library_type('Schedule.TimeOfDay'), internal(wirely.TimeOfDay.from_parts(0, 0))), (types.ValueType, types.unit),
        (enum_type, 0), (types.TypeList(types.String), ()),
        (types.TypeList(types.TypeList(types.uint8)), ()),
        (types.TypeOption(enum_type), None),
        (types.TypeOption(types.TypeList(types.String)), None),
        (empty, empty.make_value({})),
        (packet, packet.make_value({'flag': False, 'mode': 0, 'optional': None})),
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
        if is_runtime_type(value_type):
            assert value_type.equals(value_type.deserialize(value_type.serialize(value)), value)
    assert packet.serialize(packet.default_value()) == bytes(9)

    with checks.assertRaisesRegex(ValueError, 'Closed enum must declare at least one member'):
        types.TypeEnum('NoMembers', {}, is_open=False, underlying_type=types.uint8)

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
        assert not is_runtime_type(value_type)
        value = value_type.default_value()
        assert value_type.equals(value_type.verify_value(value), value)


def check_runtime_limits():
    from unittest.mock import patch

    from wirely.program.program import Variable
    from wirely.program.limits import MAX_NESTING_DEPTH, MAX_STORAGE_SIZE, MAX_TOTAL_MEMBER_COUNT, runtime_type_error

    assert (MAX_STORAGE_SIZE, MAX_NESTING_DEPTH, MAX_TOTAL_MEMBER_COUNT) == (128, 16, 128)
    types = wirely.program.types
    empty = types.TypeStruct('Empty', {})
    enum_type = types.TypeEnum('Mode', {'On': 1}, is_open=True, underlying_type=types.uint64)
    for primitive in (types.unit, types.boolean, types.uint64, enum_type, types.String, types.ValueType):
        assert (primitive.nesting_depth, primitive.total_member_count) == (0, 1)
    assert (empty.nesting_depth, empty.total_member_count, empty.size) == (1, 1, 0)
    assert is_runtime_type(empty) and runtime_type_error(empty) is None

    pair = types.TypeStruct('Pair', {'first': types.unit, 'second': empty})
    repeated = types.TypeStruct('Repeated', {'first': pair, 'second': pair})
    assert (pair.nesting_depth, pair.total_member_count) == (2, 3)
    assert (repeated.nesting_depth, repeated.total_member_count) == (3, 7)
    items = types.TypeList(types.TypeOption(repeated))
    assert (items.nesting_depth, items.total_member_count) == (5, 9)
    assert not items.has_runtime_representation and not is_runtime_type(items)
    assert 'parameter-only' in runtime_type_error(items)
    settings = types.TypeStruct('Settings', {'payload': types.TypeOption(types.String)})
    assert not settings.has_runtime_representation
    assert runtime_type_error(settings) == 'field `payload`: inner type: `string` is parameter-only'

    depth = types.unit
    for _ in range(MAX_NESTING_DEPTH):
        depth = types.TypeOption(depth)
    assert depth.nesting_depth == MAX_NESTING_DEPTH and is_runtime_type(depth)
    too_deep = types.TypeOption(depth)
    assert runtime_type_error(too_deep) == 'nesting depth 17 exceeds 16'

    members = types.TypeStruct('Members', {str(i): empty for i in range(127)})
    assert members.total_member_count == MAX_TOTAL_MEMBER_COUNT and members.size == 0
    assert is_runtime_type(members)
    too_many = types.TypeOption(members)
    assert too_many.has_runtime_representation
    assert runtime_type_error(too_many) == 'total member count 129 exceeds 128'
    assert too_many.verify_value(None) is None
    assert too_many.equals(None, None)

    storage = types.TypeStruct('Storage', {str(i): types.uint64 for i in range(16)})
    assert storage.size == MAX_STORAGE_SIZE and is_runtime_type(storage)
    too_large = types.TypeOption(storage)
    assert runtime_type_error(too_large) == 'storage size 136 bytes exceeds 128 bytes'
    # A presence byte can fit in the final field's unused tail padding.
    padded = types.TypeStruct('Padded', {
        **{str(i): types.uint64 for i in range(15)}, 'last': types.uint8,
    })
    assert (padded.data_size, padded.size) == (121, 128)
    assert types.TypeOption(padded).size == 128
    assert is_runtime_type(types.TypeOption(padded))
    # Storage size, rather than encoded data size, is limited.
    padded_overflow = types.TypeStruct('PaddedOverflow', {
        **dict(storage.fields), 'last': types.uint8,
    })
    assert runtime_type_error(padded_overflow) == 'storage size 136 bytes exceeds 128 bytes'

    for value_type, value in (
            (storage, storage.make_value({str(i): i for i in range(16)})),
            (members, members.make_value({str(i): empty.make_value({}) for i in range(127)})),
            (depth, None)):
        assert value_type.equals(value, value_type.deserialize(value_type.serialize(value)))
        Variable(value_type)

    # Representation support takes precedence; then depth, count, and storage.
    unsupported = types.TypeStruct('Unsupported', {'large': too_large, 'text': types.String})
    assert runtime_type_error(unsupported) == 'field `text`: `string` is parameter-only'
    multiple_limits = types.TypeStruct('MultipleLimits', {str(i): types.uint64 for i in range(128)})
    assert runtime_type_error(multiple_limits) == 'total member count 129 exceeds 128'
    deep_and_wide = types.TypeStruct('DeepAndWide', {'deep': too_deep, 'wide': multiple_limits})
    assert runtime_type_error(deep_and_wide) == 'nesting depth 18 exceeds 16'

    # Types without a runtime representation have no layout; types beyond the limits have one but
    # cannot be runtime variables.
    for value_type in (types.String, settings, too_deep, too_many, too_large):
        assert not is_runtime_type(value_type)
        reason = runtime_type_error(value_type)
        operations = [lambda: Variable(value_type)]
        if not value_type.has_runtime_representation:
            operations += [lambda: value_type.size, lambda: value_type.data_size, lambda: value_type.alignment,
                           lambda: value_type.serialize(None), lambda: value_type.deserialize(b'')]
        for operation in operations:
            with checks.assertRaises(ValueError) as raised:
                operation()
            assert reason in str(raised.exception)
    assert too_large.size == 136
    with patch('wirely.program.limits.runtime_type_error', side_effect=AssertionError('formatted a diagnostic')):
        assert is_runtime_type(storage)
        assert not is_runtime_type(too_large)
        assert not is_runtime_type(settings)


def check_immutability():
    # Types cannot change after construction; primitives compare by contents.
    types = wirely.program.types
    mode = types.TypeEnum('Mode', {'On': 0}, is_open=False, underlying_type=types.uint8)
    for value_type in (types.uint8, types.TypeStruct('Point', {'x': types.int8}), mode, types.TypeOption(mode)):
        with checks.assertRaises(AttributeError):
            value_type.name = 'other'
    unsigned = types.TypeInt(is_signed=False, bits=8)
    assert unsigned == types.uint8 and hash(unsigned) == hash(types.uint8) and unsigned != types.int8


check_parsing_and_conversions()
check_enum_values()
check_open_int_enums()
check_open_and_closed_enums()
check_descriptors_and_lists()
check_integers()
check_dynamic_values()
check_unit()
check_times()
check_type_names()
check_to_text()
check_structs()
check_unnamed_types()
check_default_values()

check_runtime_limits()
check_immutability()


async def run_test():
    check_json_defaults()
    pass
