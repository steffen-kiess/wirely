import contextlib
import io

import wirely

engine.get_native_state('in').set_value(False)

# Std.Constant

def check(*args):
    engine.step()

    for i, v in enumerate(args):
        engine.assert_value_is('c{}'.format(i), v)

    engine.assert_is_idle()

# Needed because DetectEdge.run() will run twice
engine.step()

check(True, 1000)
check(True, 1000)

# Std.DetectEdge

engine.assert_value_is('edge', None)

engine.get_native_state('in').set_value(True)
engine.step()
engine.assert_value_is('edge', None)
engine.step()
engine.assert_value_is('edge', None)
engine.assert_is_idle()

engine.get_native_state('in').set_value(True)
engine.step()
engine.assert_value_is('edge', None)
engine.step()
engine.assert_value_is('edge', None)
engine.assert_is_idle()

engine.get_native_state('in').set_value(False)
engine.step()
engine.assert_value_is('edge', wirely.Some(False))
engine.step()
engine.assert_value_is('edge', None)
engine.assert_is_idle()

engine.get_native_state('in').set_value(False)
engine.step()
engine.assert_value_is('edge', None)
engine.step()
engine.assert_value_is('edge', None)
engine.assert_is_idle()

# Std.WarnOnEvent

def step_output():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        engine.step()
    return output.getvalue()

engine.get_native_state('unit_events').send_event(wirely.Some(wirely.Unit()))
engine.get_native_state('bool_events').send_event(wirely.Some(True))
assert sorted(step_output().splitlines()) == ['Warning: [w_bool]: Bool warning true!', 'Warning: [w_unit]: Unit warning']
assert step_output() == ''
engine.assert_is_idle()

# An identical event in the next step produces another warning.
engine.get_native_state('bool_events').send_event(wirely.Some(True))
assert step_output() == 'Warning: [w_bool]: Bool warning true!\n'
engine.get_native_state('bool_events').send_event(wirely.Some(True))
assert step_output() == 'Warning: [w_bool]: Bool warning true!\n'
assert step_output() == ''
engine.assert_is_idle()

engine.get_native_state('edge_events').debug_command(['send', 'Falling'])
assert step_output() == 'Warning: [w_edge]: Edge Std.EdgeType.Falling\n'
assert step_output() == ''
engine.assert_is_idle()


async def run_test():
    pass
