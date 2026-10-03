import datetime

import wirely
import wirely.compiler.parser
from wirely.compiler.types import parse_value


engine.step()
for name, expected in {
    'absent': None,
    'boolean_some': wirely.Some(True),
    'number_some': wirely.Some(-42),
    'unit_some': wirely.Some(wirely.Unit()),
    'nested_none': wirely.Some(None),
    'nested_unit': wirely.Some(wirely.Some(wirely.Unit())),
    'list_some': wirely.Some((1, 2)),
    'list_options': (wirely.Some(1), None, wirely.Some(-42)),
    'time_some': wirely.Some(datetime.time(0, 30)),
    'delay': wirely.Some(False),
    'settings.flag.': wirely.Some(True),
    'settings.nested.': wirely.Some(wirely.Some(True)),
    'settings.items.': (wirely.Some(5), None),
    'settings.numbers.': wirely.Some((5, -42)),
    'settings.at.': wirely.Some(datetime.time(0, 30)),
    'settings.times.': (wirely.Some(datetime.time(0, 30)), None),
    'forwarded.constant.': wirely.Some(datetime.time(0, 30)),
}.items():
    if name in {'list_some', 'list_options', 'settings.items.', 'settings.numbers.', 'settings.times.'}:
        assert engine.program.instances[name.rstrip('.')].parameters['Value'] == expected
    else:
        engine.assert_value_is(name, expected)
engine.step()
engine.assert_value_is('delay', wirely.Some(True))


def parse_expression(expression):
    declarations = wirely.compiler.parser.parse_str(
        f'parameter Value: Option[unit] = {expression};', filename='<some-test>'
    )
    return declarations[0].default_value


for expression in ['Some(())', 'Some ( () )', 'Some(/* value */ ())']:
    assert parse_value(wirely.types.TypeOption(wirely.types.unit), parse_expression(expression)) == wirely.Some(wirely.Unit())
option_type = wirely.types.TypeOption(wirely.types.TypeOption(wirely.types.unit))
assert parse_value(option_type, parse_expression('Some(Some(()))')) == wirely.Some(wirely.Some(wirely.Unit()))
assert parse_value(option_type, parse_expression('Some(None)')) == wirely.Some(None)
list_type = wirely.types.TypeList(wirely.types.TypeOption(wirely.types.unit))
assert parse_value(list_type, parse_expression('[None, Some(())]')) == (None, wirely.Some(wirely.Unit()))
