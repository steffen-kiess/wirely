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


async def run_test():
    pass
