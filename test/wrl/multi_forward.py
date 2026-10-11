import itertools

inp = [engine.get_native_state(f'inp{index}') for index in range(3)]

for values in itertools.product((False, True), repeat=3):
    for source, value in zip(inp, values):
        source.set_value(value)
    engine.step()
    engine.assert_value_is('inner', values[0] and values[1])
    engine.assert_value_is('outer', all(values))
    engine.assert_value_is('empty', True)
    engine.assert_value_is('empty_outer', values[2])
    engine.assert_is_idle()
