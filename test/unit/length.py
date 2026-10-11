import wirely


source = engine.get_native_state('source')
measurement = engine.get_native_state('measurement')
measurement_type = wirely.ValueType(engine.get_variable('measurement').var_type)

for enabled, length in [(False, 7), (True, 9), (False, 0)]:
    source.set_value(enabled)
    measurement.set_value(measurement_type.make_struct({'Length': length}))
    engine.step()
    for name, expected in [('empty', 0), ('single', 1), ('repeated', 2),
                           ('forwarded.child', 3)]:
        engine.assert_value_is(name, expected)
        engine.assert_value_is(name + '.Alias', expected + 1)
        engine.assert_value_is(name + '.Explicit', expected)
        engine.assert_value_is(name + '.ListCount', 3)
    engine.assert_value_is('fields', length)
    engine.assert_is_idle()
