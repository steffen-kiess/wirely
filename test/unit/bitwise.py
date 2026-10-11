# Verify compiled procedure behavior through changing engine inputs.
engine.get_native_state('boolean_source').set_value(False)
engine.get_native_state('other_source').set_value(False)
for bits in (8, 16, 32, 64):
    for prefix in ('int', 'uint'):
        engine.get_native_state(prefix + str(bits) + '_source').set_value(0)

for bits in (8, 16, 32, 64):
    for signed in (True, False):
        name = ('int' if signed else 'uint') + str(bits)
        source = engine.get_native_state(name + '_source')
        mask = (1 << bits) - 1

        def normalized(value):
            value &= mask
            return value - (1 << bits) if signed and value >= (1 << (bits - 1)) else value

        values = (0, 1, 8, (1 << (bits - int(signed))) - 1)
        if signed:
            values += (-1, -(1 << (bits - 1)), -9)
        for value in values:
            source.set_value(value)
            engine.step()
            expected = {
                'Left': normalized(value << 3), 'Right': value >> 2,
                'Complement': normalized(~value), 'And': value & 5,
                'Or': value | 5, 'Xor': value ^ 5,
                'Preserved': 1, 'BitSet': bool(value & 8),
            }
            for port, result in expected.items():
                engine.assert_value_is(name + '_ops.' + port, result)

first = engine.get_native_state('boolean_source')
second = engine.get_native_state('other_source')
for left in (False, True):
    for right in (False, True):
        first.set_value(left)
        second.set_value(right)
        engine.step()
        expected = {
            'And': left and right, 'Or': left or right, 'Xor': left != right,
            'Complement': not left, 'AndRan': True, 'OrRan': True,
            'XorRan': True, 'Mask': 8,
        }
        for port, result in expected.items():
            engine.assert_value_is('booleans.' + port, result)
engine.assert_is_idle()
