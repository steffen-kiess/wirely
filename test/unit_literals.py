from dataclasses import FrozenInstanceError
from unittest import TestCase

import wirely
import wirely.parser


checks = TestCase()
unit = wirely.Unit()
assert unit == wirely.Unit()
checks.assertNotEqual(unit, None)
assert repr(unit) == 'Unit()'
assert len({unit, wirely.Unit()}) == 1
assert not hasattr(unit, '__dict__')
with checks.assertRaises((FrozenInstanceError, TypeError)):
    unit.value = True
with checks.assertRaises(TypeError):
    wirely.Unit(True)

assert wirely.types.unit.verify_value(unit) == wirely.Unit()
assert wirely.types.unit.equals(unit, wirely.Unit())
for invalid in [None, (), False, True, wirely.Some(unit)]:
    with checks.assertRaises(Exception):
        wirely.types.unit.verify_value(invalid)
    with checks.assertRaises(Exception):
        wirely.types.unit.equals(unit, invalid)

optional_unit = wirely.types.TypeOption(wirely.types.unit)
assert optional_unit.verify_value(None) is None
assert optional_unit.verify_value(wirely.Some(unit)) == wirely.Some(wirely.Unit())
with checks.assertRaises(Exception):
    optional_unit.verify_value(wirely.Some(None))

engine.step()
for name in ['source', 'spaced', 'commented', 'delay']:
    engine.assert_value_is(name, wirely.Unit())
engine.assert_value_is('units', [wirely.Unit(), wirely.Unit()])
engine.assert_value_is('present', wirely.Some(wirely.Unit()))
engine.assert_value_is('absent', None)
engine.step()
engine.assert_value_is('delay', wirely.Unit())


def parse_expression(expression):
    declarations = wirely.parser.parse_str(
        f'parameter Value : unit = {expression};', filename='<unit-test>'
    )
    return declarations[0].default_value


for expression in ['()', '( )', '(\n\t)', '(/* outer /* inner */ */)',
                   '(\n// unit\n)']:
    literal = parse_expression(expression)
    assert isinstance(literal, wirely.UnitLiteral), expression
    assert wirely.types.unit.parse_value(literal) == wirely.Unit()

for expression in ['Function()', 'Function( )', 'Function(/* empty */)']:
    call = parse_expression(expression)
    assert isinstance(call, wirely.FunctionCall), expression
    assert call.parameters == [], expression

call = parse_expression('Function(())')
assert isinstance(call, wirely.FunctionCall)
assert len(call.parameters) == 1
assert isinstance(call.parameters[0], wirely.UnitLiteral)
