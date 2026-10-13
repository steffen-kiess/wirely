import wirely

sources = {name: engine.get_native_state(name) for name in ('press', 'number', 'first', 'second')}


def check(events, **expected):
    """Send events in one step, then check the state."""
    for name, value in events.items():
        sources[name].send_event(wirely.Some(value))
    engine.step()
    # Events end in this step; state aliases read the state stored by the previous one.
    engine.step()
    for name, value in expected.items():
        engine.assert_value_is(name, value)
    engine.assert_is_idle()


check({}, toggled=False, last=None, count=0, both=None, total=0, any_steps=0)
check({'press': wirely.Unit()}, toggled=True)
check({'press': wirely.Unit()}, toggled=False)
check({'number': 5}, last=wirely.Some(5))
check({'first': 2}, count=1, both=None, total=2, any_steps=1)
check({'first': 3, 'second': 4}, count=2, both=wirely.Some(7), total=9, any_steps=2)
check({'second': 1}, count=3, both=wirely.Some(7), total=10, any_steps=3)
check({}, toggled=False, last=wirely.Some(5), count=3, total=10, any_steps=3)


async def run_test():
    pass
