inp_count = 3
inp = [engine.get_native_state('inp{}'.format(i)) for i in range(inp_count)]
number_inp = engine.get_native_state('number_inp')


def check(option_value, has_value, bool_value, number):
    for i, value in enumerate((option_value, has_value, bool_value)):
        inp[i].set_value(value)
    number_inp.set_value(number)

    engine.step()
    engine.assert_value_is('val0', None)
    engine.assert_value_is('is_some0', False)
    engine.assert_value_is('val1', (option_value,))
    engine.assert_value_is('is_some1', True)
    engine.assert_value_is('val2', (bool_value,) if has_value else None)
    engine.assert_value_is('is_some2', has_value)
    engine.assert_value_is('val3', (number,) if has_value else None)
    engine.assert_value_is('is_some3', has_value)
    engine.assert_is_idle()


check(False, False, False, 42)
check(True, False, True, 123)
check(False, True, False, 0)
check(True, True, True, 65535)
