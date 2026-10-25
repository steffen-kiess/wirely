import wirely
from wirely.runtime.engine import Engine


def check(runtime, value, previous, *, event=False):
    runtime.get_native_state('value.create').set_value(value)
    if event:
        runtime.get_native_state('event.create').send_event(wirely.Some(wirely.Unit()))
    runtime.step()
    changed = value != previous
    payload = wirely.Some(value) if changed else None
    for name in ('Written', 'Read', 'Copied'):
        runtime.assert_value_is('watch.' + name, payload)
    runtime.assert_value_is('watch.Previous', wirely.Some(previous) if changed else None)
    runtime.assert_value_is('watch.Mixed', wirely.Some(wirely.Unit()) if changed or event else None)
    runtime.assert_value_is('watch.Option', wirely.Some(wirely.Some(value) if value else None)
                            if changed else None)


def settle(runtime):
    runtime.step()
    for name in ('Written', 'Read', 'Copied', 'Previous', 'Mixed', 'Option'):
        runtime.assert_value_is('watch.' + name, None)
    runtime.assert_is_idle()


check(engine, 0, 0)
settle(engine)
check(engine, 5, 0)
settle(engine)
check(engine, 0, 5)
settle(engine)
check(engine, 0, 0, event=True)
settle(engine)
# Consecutive changes compare against the immediately preceding step.
check(engine, 1, 0)
check(engine, 2, 1)
settle(engine)
# A fresh engine compares its first step against declared initial state.
restarted = Engine(engine.program)
check(restarted, 7, 0)
settle(restarted)


async def run_test():
    pass
