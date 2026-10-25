import contextlib
import io

import wirely
from tests.infra.support import send_event_text

engine.get_native_state('in.create').set_value(False)
engine.get_native_state('initial_true.create').set_value(True)

# Std.Constant

def check(*args):
    engine.step()

    for i, v in enumerate(args):
        engine.assert_value_is('c{}'.format(i), v)

    engine.assert_is_idle()

# Initialization records the input without emitting an edge.
engine.step()
for name in ('edge', 'rising', 'both', 'initial_falling', 'initial_rising', 'initial_both'):
    engine.assert_value_is(name, None)

# Std.DebugValueSource outputs Initial until a value is set.
engine.assert_value_is('initial', 7)
engine.get_native_state('initial.create').set_value(3)
engine.step()
engine.assert_value_is('initial', 3)

check(True, 1000)
check(True, 1000)

# Std.DetectEdge

def check_edges(source_name, names, cases):
    source = engine.get_native_state(source_name + '.create')
    for value, expected, settle in cases:
        source.set_value(value)
        engine.step()
        for name, event in zip(names, expected):
            engine.assert_value_is(name, event)
        if settle:
            engine.step()
            for name in names:
                engine.assert_value_is(name, None)
            engine.assert_is_idle()


rising = (None, wirely.Some(True), wirely.Some(True))
falling = (wirely.Some(False), None, wirely.Some(False))
unchanged = (None, None, None)
check_edges('in', ('edge', 'rising', 'both'), [
    (True, rising, True),
    (True, unchanged, True),
    (False, falling, True),
    (False, unchanged, True),
    (True, rising, False),
    (False, falling, False),
    (True, rising, True),
])
check_edges('initial_true', ('initial_falling', 'initial_rising', 'initial_both'), [
    (True, unchanged, True),
    (False, falling, True),
    (False, unchanged, True),
    (True, rising, True),
])

# Std.WarnOnEvent

def step_output():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        engine.step()
    return output.getvalue()

engine.get_native_state('unit_events.create').send_event(wirely.Some(wirely.Unit()))
engine.get_native_state('bool_events.create').send_event(wirely.Some(True))
assert sorted(step_output().splitlines()) == ['Warning: [w_bool]: Bool warning true!', 'Warning: [w_unit]: Unit warning']
assert step_output() == ''
engine.assert_is_idle()

# An identical event in the next step produces another warning.
engine.get_native_state('bool_events.create').send_event(wirely.Some(True))
assert step_output() == 'Warning: [w_bool]: Bool warning true!\n'
engine.get_native_state('bool_events.create').send_event(wirely.Some(True))
assert step_output() == 'Warning: [w_bool]: Bool warning true!\n'
assert step_output() == ''
engine.assert_is_idle()

send_event_text(engine.get_native_state('edge_events.create'), 'Falling')
assert step_output() == 'Warning: [w_edge]: Edge Std.EdgeType.Falling\n'
assert step_output() == ''
engine.assert_is_idle()


async def run_test():
    pass
