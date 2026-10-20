import wirely
from tests.infra.support import internal


def make(struct_type, **fields):
    """Return the value of a struct type with these fields by name."""
    return tuple(fields[name] for name in struct_type.fields)


def check_equality():
    at = internal(wirely.TimeOfDay.from_parts(8, 0))
    later = internal(wirely.TimeOfDay.from_parts(8, 1))
    span = internal(wirely.Duration(ticks=1))
    longer = internal(wirely.Duration(ticks=2))
    instant = internal(wirely.MonotonicTime(generation=1, ticks=100))
    next_instant = internal(wirely.MonotonicTime(generation=1, ticks=101))
    other_generation = internal(wirely.MonotonicTime(generation=2, ticks=100))
    default = internal(wirely.MonotonicTime(generation=0, ticks=0))
    packet_type = engine.get_variable('packet_left').var_type
    nested_type = engine.get_variable('nested_left').var_type.inner_type

    def packet(**changes):
        return make(packet_type, **{'flag': True, 'at': at, 'span': span, 'instant': instant, **changes})

    def nested(packet_value, maybe):
        return wirely.Some(make(nested_type, packet=packet_value, maybe=maybe))

    value = packet()
    nested_value = nested(wirely.Some(value), wirely.Some(None))
    # Expected results are explicit, including presence, payload, and every field.
    cases = {
        'time': [(at, internal(wirely.TimeOfDay.from_parts(8, 0)), True), (at, later, False)],
        'duration': [(span, internal(wirely.Duration(ticks=1)), True),
                     (span, longer, False)],
        'monotonic': [(instant, internal(wirely.MonotonicTime(generation=1, ticks=100)), True),
                      (instant, next_instant, False),
                      (instant, other_generation, False),
                      (default, internal(wirely.MonotonicTime(generation=0, ticks=0)), True),
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
        'empty': [((), (), True)],
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


async def run_test():
    pass
