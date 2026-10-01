import contextlib
import datetime
import io
from pathlib import Path

import wirely
import wirely.compiler
import wirely.parser


engine.step()
for name, expected in {
    'absent': None,
    'boolean_some': (True,),
    'number_some': (-42,),
    'unit_some': (None,),
    'nested_none': (None,),
    'nested_unit': ((None,),),
    'list_some': ([1, 2],),
    'list_options': [(1,), None, (-42,)],
    'time_some': (datetime.time(0, 30),),
    'delay': (False,),
    'settings.flag.': (True,),
    'settings.nested.': ((True,),),
    'settings.items.': [(5,), None],
    'settings.numbers.': ([5, -42],),
    'settings.at.': (datetime.time(0, 30),),
    'settings.times.': [(datetime.time(0, 30),), None],
    'forwarded.constant.': (datetime.time(0, 30),),
}.items():
    engine.assert_value_is(name, expected)
engine.step()
engine.assert_value_is('delay', (True,))


def parse_expression(expression):
    declarations = wirely.parser.parse_str(
        f'parameter Value : Option[unit] = {expression};', filename='<some-test>'
    )
    return declarations[0].default_value


for expression in ['Some(())', 'Some ( () )', 'Some(/* value */ ())']:
    assert wirely.types.TypeOption(wirely.types.unit).parse_value(parse_expression(expression)) == (None,)
option_type = wirely.types.TypeOption(wirely.types.TypeOption(wirely.types.unit))
assert option_type.parse_value(parse_expression('Some(Some(()))')) == ((None,),)
assert option_type.parse_value(parse_expression('Some(None)')) == (None,)
list_type = wirely.types.TypeList(wirely.types.TypeOption(wirely.types.unit))
assert list_type.parse_value(parse_expression('[None, Some(())]')) == [None, (None,)]

for expression in ['Some()', 'Some((), ())']:
    try:
        wirely.types.TypeOption(wirely.types.unit).parse_value(parse_expression(expression))
    except wirely.ProgramError as error:
        assert 'Some expects exactly one argument' in str(error)
    else:
        raise AssertionError(expression)

# A parameter reference is checked against the expected inner type.
source = (
    'node type Settings {\n'
    '  parameter Number : int8;\n'
    '  node constant : Std.Constant(Type = Option[boolean], Value = Some(Number));\n'
    '}\n'
    'node settings : Settings(Number = 1);'
)
output = io.StringIO()
errors = io.StringIO()
with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
    std_source = Path('wirely/lib/std.wrl').read_text(encoding='utf-8')
    result = wirely.compiler.compile_strs([
        ('wirely/lib/std.wrl', std_source), ('<some-test>', source),
    ])
assert result is None
assert 'Error in <some-test>:3' in output.getvalue()
assert 'Expected value of type boolean, got int8' in output.getvalue()
assert 'Internal compiler error' not in output.getvalue()
assert not errors.getvalue()
