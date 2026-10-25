import wirely

sources = {name: engine.get_native_state(name + '.create') for name in ('press', 'number', 'first', 'second')}


def check(events, **expected):
    """Send events in one step, then check the state."""
    for name, value in events.items():
        sources[name].send_event(wirely.Some(value))
    engine.step()
    number = events.get('number')
    emitted = None if number is None else wirely.Some(number)
    engine.assert_value_is('forward.payload', emitted)
    engine.assert_value_is('forward.constant', None if number is None else wirely.Some(42))
    engine.assert_value_is('forward.filtered', emitted if number != 0 else None)
    engine.assert_value_is('forward.nested', None if number is None else
                           wirely.Some(None if number == 0 else wirely.Some(number)))
    engine.assert_value_is('forward.unit', wirely.Some(wirely.Unit()) if 'press' in events else None)
    engine.assert_value_is('forward.absent', None)
    engine.assert_value_is('forward.persistent', wirely.Some(wirely.Unit()))
    merged = events.get('second', events.get('first'))
    engine.assert_value_is('forward.merged', None if merged is None else wirely.Some(merged))
    # Events end in this step; state aliases read the state stored by the previous one.
    engine.step()
    for name in ('payload', 'constant', 'filtered', 'nested', 'unit', 'absent', 'merged'):
        engine.assert_value_is('forward.' + name, None)
    for name, value in expected.items():
        engine.assert_value_is(name, value)
    engine.assert_is_idle()


check({}, toggled=False, last=None, count=0, both=None, total=0, any_steps=0, latest=0)
check({'press': wirely.Unit()}, toggled=True)
check({'press': wirely.Unit()}, toggled=False)
check({'number': 0}, last=wirely.Some(0))
check({'number': 5}, last=wirely.Some(5))
check({'first': 2}, count=1, both=None, total=2, any_steps=1, latest=2)
check({'first': 3, 'second': 4}, count=2, both=wirely.Some(7), total=9, any_steps=2, latest=4)
check({'second': 1}, count=3, both=wirely.Some(7), total=10, any_steps=3, latest=1)
check({}, toggled=False, last=wirely.Some(5), count=3, total=10, any_steps=3, latest=1)


async def run_test():
    pass
