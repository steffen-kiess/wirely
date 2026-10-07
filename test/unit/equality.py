import datetime

import wirely


def check_equality():
    at = datetime.time(8)
    later = datetime.time(8, 1)
    span = wirely.Duration(duration_ns=1)
    longer = wirely.Duration(duration_ns=2)
    instant = wirely.MonotonicTime(1, 100)
    next_instant = wirely.MonotonicTime(1, 101)
    other_generation = wirely.MonotonicTime(2, 100)
    default = wirely.MonotonicTime(0, 0)
    packet_type = wirely.ValueType(engine.get_variable('packet_left').var_type)
    nested_type = wirely.ValueType(engine.get_variable('nested_left').var_type.inner_type)
    empty_type = wirely.ValueType(engine.get_variable('empty_left').var_type)

    def packet(**changes):
        return packet_type.make_struct({
            'flag': True, 'at': at, 'span': span, 'instant': instant, **changes,
        })

    def nested(packet_value, maybe):
        return wirely.Some(nested_type.make_struct({
            'packet': packet_value, 'maybe': maybe,
        }))

    value = packet()
    nested_value = nested(wirely.Some(value), wirely.Some(None))
    # Expected results are explicit, including presence, payload, and every field.
    cases = {
        'time': [(at, datetime.time(8), True), (at, later, False)],
        'duration': [(span, wirely.Duration(duration_ns=1), True),
                     (span, longer, False)],
        'monotonic': [(instant, wirely.MonotonicTime(1, 100), True),
                      (instant, next_instant, False),
                      (instant, other_generation, False),
                      (default, wirely.MonotonicTime(0, 0), True),
                      (default, instant, False)],
        'option': [(None, None, True), (None, wirely.Some(at), False),
                   (wirely.Some(at), wirely.Some(at), True),
                   (wirely.Some(at), wirely.Some(later), False)],
        'nested_option': [(None, wirely.Some(None), False),
                          (wirely.Some(None), wirely.Some(None), True),
                          (wirely.Some(None), wirely.Some(wirely.Some(wirely.Unit())), False)],
        'packet': [(value, packet(), True), (value, packet(flag=False), False),
                   (value, packet(at=later), False),
                   (value, packet(span=longer), False),
                   (value, packet(instant=other_generation), False)],
        'nested': [(None, None, True), (None, nested_value, False),
                   (nested_value, nested(wirely.Some(packet()), wirely.Some(None)), True),
                   (nested_value, nested(None, wirely.Some(None)), False),
                   (nested_value, nested(wirely.Some(value), None), False),
                   (nested_value, nested(wirely.Some(packet(instant=default)), wirely.Some(None)), False)],
        'empty': [(empty_type.make_struct({}), empty_type.make_struct({}), True)],
    }

    # Initialize every source so the shared engine can run all comparators.
    for name, values in cases.items():
        for side, value in zip(('left', 'right'), values[0][:2]):
            engine.get_native_state(f'{name}_{side}').set_value(value)
    engine.step()
    engine.assert_value_is('LiteralEqual', True)
    engine.assert_value_is('LiteralUnequal', True)

    for name, values in cases.items():
        # Exercise changes from either input without repeating the whole matrix.
        transitions = []
        unequal = next(((left, right) for left, right, equal in values if not equal), None)
        if unequal is not None:
            left, right = unequal
            transitions = [(left, right, False), (right, right, True),
                           (right, left, False), (left, left, True)]
        for left, right, equal in values + transitions:
            engine.get_native_state(f'{name}_left').set_value(left)
            engine.get_native_state(f'{name}_right').set_value(right)
            engine.step()
            engine.assert_value_is(f'{name}.Equal', equal)
            engine.assert_value_is(f'{name}.Unequal', not equal)
            engine.assert_is_idle()


check_equality()
