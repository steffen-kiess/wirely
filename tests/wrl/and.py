inp_count = 3
inp = [engine.get_native_state(('inp{}'.format(i)) + '.create') for i in range(inp_count)]

def set(*args):
    if len(args) != inp_count:
        raise Exception('len(args) != inp_count')
    for i, value in enumerate(args):
        inp[i].set_value(value)

def check(*args):
    set(*args)

    engine.step()

    value = True
    for i, v in enumerate(args):
        engine.assert_value_is('and{}'.format(i), value)
        value = value and v

    engine.assert_is_idle()

check(False, False, False)
check(False, False, True)
check(False, True, False)
check(False, True, True)
check(True, False, False)
check(True, False, True)
check(True, True, False)
check(True, True, True)


async def run_test():
    pass
