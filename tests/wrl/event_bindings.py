import contextlib
import io

import wirely


sources = {name: engine.get_native_state(name) for name in ('number', 'unit', 'nested')}


def step(events):
    for name, event in events.items():
        sources[name].send_event(event)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        engine.step()
    number = events.get('number')
    for name, source in (('relay', 'number'), ('unit_relay', 'unit'), ('nested_relay', 'nested')):
        expected = events.get(source)
        engine.assert_value_is(name, expected)
        engine.assert_value_is(name + '.Optional', expected)
    for name in ('first', 'second'):
        engine.assert_value_is(name, number)
    engine.assert_value_is('filtered', number if number is not None and number.value != 0 else None)
    engine.assert_value_is('sum', 0 if number is None else 2 * number.value)
    expected_output = '' if number is None else f'Warning: [warning]: Binding {number.value}\n'
    assert output.getvalue() == expected_output, output.getvalue()
    if events.get('unit') is None:
        engine.assert_value_is('timestamp', None)
    else:
        assert engine.get_value('timestamp') is not None


step({})
engine.assert_is_idle()
step({'number': wirely.Some(5), 'unit': wirely.Some(wirely.Unit()), 'nested': wirely.Some(None)})
# Equal payloads in consecutive steps still reach the native consumer twice.
step({'number': wirely.Some(5), 'unit': wirely.Some(wirely.Unit()), 'nested': wirely.Some(None)})
step({})
engine.assert_is_idle()
step({'number': wirely.Some(0), 'nested': wirely.Some(wirely.Some(7))})
step({})
engine.assert_is_idle()


async def run_test():
    pass
