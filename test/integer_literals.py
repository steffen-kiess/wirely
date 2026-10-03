import contextlib
import io

import wirely.compiler
import wirely.compiler.parser


engine.step()
for name, expected in {
    'decimal_minus': -42,
    'decimal_plus': 42,
    'hex_minus': -42,
    'hex_plus': 42,
    'octal_minus': -42,
    'octal_plus': 42,
    'spaced_minus': -42,
    'spaced_plus': 42,
    'zero_minus': 0,
    'zero_plus': 0,
    'hex_plain': 42,
    'octal_plain': 42,
}.items():
    engine.assert_value_is(name, expected)

for bits in [8, 16, 32, 64]:
    engine.assert_value_is(f'int{bits}_min', -(2 ** (bits - 1)))
    engine.assert_value_is(f'int{bits}_max', 2 ** (bits - 1) - 1)
    engine.assert_value_is(f'uint{bits}_min', 0)
    engine.assert_value_is(f'uint{bits}_max', 2 ** bits - 1)


def parse_literal(literal):
    declarations = wirely.compiler.parser.parse_str(
        f'parameter Value : int64 = {literal};', filename='<integer-test>'
    )
    return declarations[0].default_value.value


for literal, expected in [
    ('- 0x2a', -42),
    ('+ 0o52', 42),
    ('-\n42', -42),
    ('+\t42', 42),
    ('- /* sign */ 42', -42),
    ('-0X2A', -42),
    ('+0O52', 42),
    ('0', 0),
    ('042', 42),
    ('-042', -42),
    ('-0x8000000000000000', -(2 ** 63)),
    ('+0xFFFFFFFFFFFFFFFF', 2 ** 64 - 1),
]:
    assert parse_literal(literal) == expected, literal

# Floats retain their existing parser behavior.
assert parse_literal('1.5') == 1.5
assert parse_literal('1e-3') == 0.001


def reject_literal(literal, value_type, message=None):
    source = (
        'node type Settings {\n'
        f'  parameter Value : {value_type};\n'
        '}\n'
        f'node settings : Settings(Value = {literal});'
    )
    output = io.StringIO()
    errors = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        result = wirely.compiler.compile_strs([('<integer-test>', source)])
    diagnostic = output.getvalue()
    assert result is None, literal
    assert 'Error in <integer-test>:4' in diagnostic, (literal, diagnostic)
    assert message is None or message in diagnostic, (literal, diagnostic)
    assert 'Internal compiler error' not in diagnostic, diagnostic
    assert 'Traceback' not in diagnostic, diagnostic
    assert not errors.getvalue(), errors.getvalue()


for literal in ['--42', '++42', '-+42', '+-42', '-Value', '+Value', '-(42)']:
    reject_literal(literal, 'int8')
for literal in ['0x', '-0x', '+0o']:
    reject_literal(literal, 'int8', 'Invalid integer literal')

for bits in [8, 16, 32, 64]:
    reject_literal(f'-{2 ** (bits - 1) + 1}', f'int{bits}', 'Int value out of range')
    reject_literal(f'+{2 ** (bits - 1)}', f'int{bits}', 'Int value out of range')
    reject_literal('-1', f'uint{bits}', 'Int value out of range')
    reject_literal(f'+{2 ** bits}', f'uint{bits}', 'Int value out of range')
