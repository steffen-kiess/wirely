from lark.exceptions import UnexpectedCharacters

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
    'decimal_leading_zero': 10,
    'decimal_leading_zero_eight': 80,
}.items():
    engine.assert_value_is(name, expected)

for bits in [8, 16, 32, 64]:
    engine.assert_value_is(f'int{bits}_min', -(2 ** (bits - 1)))
    engine.assert_value_is(f'int{bits}_max', 2 ** (bits - 1) - 1)
    engine.assert_value_is(f'uint{bits}_min', 0)
    engine.assert_value_is(f'uint{bits}_max', 2 ** bits - 1)


def parse_literal(literal):
    declarations = wirely.compiler.parser.parse_str(
        f'parameter Value: int64 = {literal};', filename='<integer-test>'
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
    ('010', 10),
    ('080', 80),
    ('0009', 9),
    ('+010', 10),
    ('-080', -80),
    ('-0009', -9),
    ('-0x8000000000000000', -(2 ** 63)),
    ('+0xFFFFFFFFFFFFFFFF', 2 ** 64 - 1),
]:
    assert parse_literal(literal) == expected, literal

for literal in ['1l', '1L', '0x10l', '0x10L', '0o10l', '0o10L',
                '-1L', '+0x10L', '-0o10L', '123abc', '123_abc']:
    for source in [
        f'parameter Value: int64 = {literal};',
        f'#[position(x = {literal}, y = 0)] node n: T {{}}',
    ]:
        try:
            list(wirely.compiler.parser.parser.lex(source))
        except UnexpectedCharacters:
            pass
        else:
            raise AssertionError(f'Lexer accepted integer suffix: {source}')

# Floats retain their existing parser behavior.
assert parse_literal('1.5') == 1.5
assert parse_literal('1e-3') == 0.001
