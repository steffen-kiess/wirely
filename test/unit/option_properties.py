import wirely
from wirely.runtime.procedures import Expression


source = engine.get_native_state('source')
nested_source = engine.get_native_state('nested_source')
for step, value in enumerate((None, wirely.Some(False), wirely.Some(True), None)):
    source.set_value(value)
    nested_source.set_value(None if value is None else wirely.Some(None))
    engine.step()
    for name in ('present', 'absent', 'nested'):
        present = value is not None
        for output in ('Present', 'BlockPresent', 'IfPresent'):
            engine.assert_value_is(f'{name}.{output}', present)
        for output in ('Absent', 'BlockAbsent', 'IfAbsent'):
            engine.assert_value_is(f'{name}.{output}', not present)
        copied = wirely.Some(None) if name == 'nested' and present else value
        engine.assert_value_is(f'{name}.Copy', copied)
        engine.assert_value_is(f'{name}.LocalAbsent', True)
        parameter_present = name != 'absent'
        for output in ('NestedPresent', 'ParameterPresent', 'ConstantPresent', 'LocalPresent'):
            engine.assert_value_is(f'{name}.{output}', parameter_present)
        for output in ('NestedAbsent', 'ParameterAbsent', 'ConstantAbsent'):
            engine.assert_value_is(f'{name}.{output}', not parameter_present)
        engine.assert_value_is(f'{name}.OptionalPresent', name == 'present')
        engine.assert_value_is(f'{name}.OptionalAbsent', name != 'present')
    engine.assert_value_is('generic', True)
    engine.assert_value_is('qualified', step == 0)
engine.assert_is_idle()

# Runtime properties read the receiver once and check only outer presence.


class Receiver:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def evaluate(self, inputs, locals):
        self.calls += 1
        return self.value


for value in (None, wirely.Some(False), wirely.Some(None)):
    for operation, expected in [('is_some', value is not None), ('is_none', value is None)]:
        receiver = Receiver(value)
        expression = Expression(operation, (receiver,))
        assert expression.evaluate({}, {}) is expected
        assert receiver.calls == 1
