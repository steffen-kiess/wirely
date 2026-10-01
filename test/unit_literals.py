import wirely
import wirely.parser


engine.step()
for name in ['source', 'spaced', 'commented', 'delay']:
    engine.assert_value_is(name, None)
engine.assert_value_is('units', [None, None])
engine.assert_value_is('present', wirely.Some(None))
engine.assert_value_is('absent', None)
engine.step()
engine.assert_value_is('delay', None)


def parse_expression(expression):
    declarations = wirely.parser.parse_str(
        f'parameter Value : unit = {expression};', filename='<unit-test>'
    )
    return declarations[0].default_value


for expression in ['()', '( )', '(\n\t)', '(/* outer /* inner */ */)',
                   '(\n// unit\n)']:
    literal = parse_expression(expression)
    assert isinstance(literal, wirely.UnitLiteral), expression
    assert wirely.types.unit.parse_value(literal) is None

for expression in ['Function()', 'Function( )', 'Function(/* empty */)']:
    call = parse_expression(expression)
    assert isinstance(call, wirely.FunctionCall), expression
    assert call.parameters == [], expression

call = parse_expression('Function(())')
assert isinstance(call, wirely.FunctionCall)
assert len(call.parameters) == 1
assert isinstance(call.parameters[0], wirely.UnitLiteral)
