inp_inst = engine.get_native_state('inp')


inp_inst.set_value(False)
print('Run -> False')

print('  Step 1')
engine.step()
engine.assert_value_is('inp', False)
engine.assert_value_is('delay1', False)
engine.assert_value_is('delay2', False)

engine.assert_is_idle()

print('  Step 2')
engine.step()
engine.assert_value_is('inp', False)
engine.assert_value_is('delay1', False)
engine.assert_value_is('delay2', False)

print('  Step 3')
engine.step()
engine.assert_value_is('inp', False)
engine.assert_value_is('delay1', False)
engine.assert_value_is('delay2', False)


inp_inst.set_value(True)
print('Run -> True')

print('  Step 1')
engine.step()
engine.assert_value_is('inp', True)
engine.assert_value_is('delay1', False)
engine.assert_value_is('delay2', False)

print('  Step 2')
engine.step()
engine.assert_value_is('inp', True)
engine.assert_value_is('delay1', True)
engine.assert_value_is('delay2', False)

print('  Step 3')
engine.step()
engine.assert_value_is('inp', True)
engine.assert_value_is('delay1', True)
engine.assert_value_is('delay2', True)

engine.assert_is_idle()


inp_inst.set_value(False)
print('Run -> False')

print('  Step 1')
engine.step()
engine.assert_value_is('inp', False)
engine.assert_value_is('delay1', True)
engine.assert_value_is('delay2', True)

print('  Step 2')
engine.step()
engine.assert_value_is('inp', False)
engine.assert_value_is('delay1', False)
engine.assert_value_is('delay2', True)

print('  Step 3')
engine.step()
engine.assert_value_is('inp', False)
engine.assert_value_is('delay1', False)
engine.assert_value_is('delay2', False)

engine.assert_is_idle()
