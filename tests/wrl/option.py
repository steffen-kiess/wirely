from dataclasses import FrozenInstanceError
from unittest import TestCase

import wirely

checks = TestCase()
some = wirely.Some(None)
assert some.value is None
assert some is not None
assert some != wirely.Some(True)
assert some == wirely.Some(None)
assert repr(some) == 'Some(value=None)'
with checks.assertRaises(FrozenInstanceError):
    some.value = True
with checks.assertRaises(FrozenInstanceError):
    del some.value
assert not hasattr(some, '__dict__')

payload = [1, 2]
some_list = wirely.Some(payload)
payload.append(3)
assert some_list.value is payload
assert some_list == wirely.Some([1, 2, 3])

option_type = wirely.types.TypeOption(wirely.types.boolean)
assert option_type.verify_value(None) is None
assert option_type.verify_value(wirely.Some(False)) == wirely.Some(False)
assert option_type.parse('none') is None
assert option_type.parse('true') == wirely.Some(True)
assert option_type.to_csv_value(None) == ('', False)
assert option_type.to_csv_value(wirely.Some(False)) == ('false', False)
assert option_type.equals(None, None)
assert not option_type.equals(None, wirely.Some(False))
assert not option_type.equals(wirely.Some(False), None)
assert option_type.equals(wirely.Some(True), wirely.Some(True))
assert not option_type.equals(wirely.Some(False), wirely.Some(True))
for invalid in [(True,), (), (True, False), True, wirely.Some(None),
                wirely.Some(1)]:
    with checks.assertRaises(Exception):
        option_type.verify_value(invalid)
    with checks.assertRaises(Exception):
        option_type.equals(invalid, wirely.Some(True))

# Equality must use the declared payload type's validation, rather than
# Python equality alone, which considers 1 equal to True.
assert wirely.Some(1) == wirely.Some(True)

nested_type = wirely.types.TypeOption(option_type)
assert nested_type.verify_value(wirely.Some(None)) == wirely.Some(None)
assert nested_type.verify_value(wirely.Some(wirely.Some(True))) == wirely.Some(wirely.Some(True))
assert not nested_type.equals(wirely.Some(None), wirely.Some(wirely.Some(True)))
for invalid in [wirely.Some(True), wirely.Some((True,)),
                wirely.Some(wirely.Some(1))]:
    with checks.assertRaises(Exception):
        nested_type.verify_value(invalid)


class IntegerSubclass(int):
    pass


integer_option = wirely.types.TypeOption(wirely.types.int8)
assert integer_option.verify_value(wirely.Some(7)) == wirely.Some(7)
# Integer payloads must be plain ints in range.
for payload in (IntegerSubclass(7), True, 128):
    with checks.assertRaises(ValueError):
        integer_option.verify_value(wirely.Some(payload))
with checks.assertRaises(Exception):
    integer_option.parse('128')

list_option = wirely.types.TypeOption(wirely.types.TypeList(option_type))
assert list_option.verify_value(wirely.Some([None, wirely.Some(True)])) == wirely.Some((None, wirely.Some(True)))
with checks.assertRaises(Exception):
    list_option.verify_value(wirely.Some([wirely.Some(1)]))

inp_count = 3
inp = [engine.get_native_state('inp{}'.format(i)) for i in range(inp_count)]
number_inp = engine.get_native_state('number_inp')
nested_input = engine.get_native_state('nested_input')
nested_input.set_value(None)


def check(option_value, has_value, bool_value, number):
    for i, value in enumerate((option_value, has_value, bool_value)):
        inp[i].set_value(value)
    number_inp.set_value(number)

    engine.step()
    engine.assert_value_is('val0', None)
    engine.assert_value_is('is_some0', False)
    engine.assert_value_is('val1', wirely.Some(option_value))
    engine.assert_value_is('is_some1', True)
    engine.assert_value_is('val2', wirely.Some(bool_value) if has_value else None)
    engine.assert_value_is('is_some2', has_value)
    engine.assert_value_is('val3', wirely.Some(number) if has_value else None)
    engine.assert_value_is('is_some3', has_value)
    engine.assert_value_is('unwrapped', number if has_value else 77)
    engine.assert_is_idle()


check(False, False, False, 42)
check(True, False, True, 123)
check(False, True, False, 0)
check(True, True, True, 65535)

# Outer absence and a present option with an absent payload remain distinct.
for value, has_value in [(None, False), (None, True),
                         (wirely.Some(False), True), (wirely.Some(True), True),
                         (wirely.Some(False), False)]:
    nested_input.set_value(value)
    inp[1].set_value(has_value)
    engine.step()
    engine.assert_value_is('nested_some', wirely.Some(value))
    engine.assert_value_is('nested_created', wirely.Some(value) if has_value else None)
    engine.assert_value_is('nested_is_some', has_value)
    engine.assert_value_is('nested_unwrapped', value if has_value else wirely.Some(True))
    engine.assert_is_idle()


async def run_test():
    pass
