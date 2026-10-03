import contextlib
import io

import wirely.compiler
import wirely.compiler.parser


engine.step()
for name, expected in {
    'escapes': '\0\t\n\r"\'\\',
    'null_digits': '\0' + '12',
    'hex': '\0\x7f\x80\xff',
    'unicode': 'A\U0001f600',
    'raw': 'Say "hello"\\n',
    'multiline': 'first\nsecond',
    'continuation': 'firstsecond',
}.items():
    engine.assert_value_is(name, expected)


def parse_literal(literal):
    declarations = wirely.compiler.parser.parse_str(
        f'parameter Value : String = {literal};', filename='<string-test>'
    )
    return declarations[0].default_value.value


for literal, expected in [
    ('""', ''),
    ('"Café 😀"', 'Café 😀'),
    (r'"\0\t\n\r\"\'\\"', '\0\t\n\r"\'\\'),
    (r'"\012"', '\0' + '12'),
    (r'"\x00\x7F\x80\xFF"', '\0\x7f\x80\xff'),
    (r'"\x123"', '\x12' + '3'),
    (r'"\u{0}\u{7f}\u{80}\u{D7FF}\u{E000}\u{10FFFF}"',
     '\0\x7f\x80\ud7ff\ue000\U0010ffff'),
    (r'"\u{1F_600}\u{000041}\u{4__1_}"', '\U0001f600AA'),
    ('"first\nsecond"', 'first\nsecond'),
    ('"first\r\nsecond"', 'first\nsecond'),
    ('"first\\\n  \t\n  second"', 'firstsecond'),
    ('"first\\\r\n  second"', 'firstsecond'),
    ('"first\\\n\u00a0second"', 'first\u00a0second'),
    (r'r"\n\u{41}\xFF"', r'\n\u{41}\xFF'),
    (r'r#"Say "hello"\n"#', 'Say "hello"\\n'),
    ('r"first\r\nsecond"', 'first\nsecond'),
    ('r#"first\r\nsecond"#', 'first\nsecond'),
    ('r""', ''),
    ('r#""#', ''),
]:
    actual = parse_literal(literal)
    assert actual == expected, (literal, actual, expected)


for literal, message in [
    (r'"\x"', 'Hex escape requires two hexadecimal digits'),
    (r'"\xF"', 'Hex escape requires two hexadecimal digits'),
    (r'"\xGG"', 'Hex escape requires two hexadecimal digits'),
    (r'"\u0041"', 'Unicode escape requires braces'),
    (r'"\u{"', 'Unterminated Unicode escape'),
    (r'"\u{}"', 'Invalid Unicode escape digits'),
    (r'"\u{_41}"', 'Invalid Unicode escape digits'),
    (r'"\u{GG}"', 'Invalid Unicode escape digits'),
    (r'"\u{0000041}"', 'at most six hexadecimal digits'),
    (r'"\u{D800}"', 'not a Unicode scalar value'),
    (r'"\u{DFFF}"', 'not a Unicode scalar value'),
    (r'"\u{110000}"', 'not a Unicode scalar value'),
    (r'"\U00000041"', 'Unknown string escape'),
    (r'"\N{LATIN CAPITAL LETTER A}"', 'Unknown string escape'),
    (r'"\101"', 'Unknown string escape'),
    *[(f'"\\{escape}"', 'Unknown string escape')
      for escape in ['a', 'b', 'f', 'v', 'q', '7']],
    ('"first\rsecond"', 'Bare carriage return'),
    ('r"first\rsecond"', 'Bare carriage return'),
    ('r#"first\rsecond"#', 'Bare carriage return'),
    ('r##"text"##', None),
    ('"unterminated', None),
    ('"trailing\\', None),
]:
    source = f'node type Settings {{\n  parameter Value : String = {literal};\n}}'
    output = io.StringIO()
    errors = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        result = wirely.compiler.compile_strs([('<string-test>', source)])
    diagnostic = output.getvalue()
    assert result is None, literal
    assert 'Error in <string-test>:2' in diagnostic, (literal, diagnostic)
    assert message is None or message in diagnostic, (literal, diagnostic)
    assert 'Internal compiler error' not in diagnostic, diagnostic
    assert 'Traceback' not in diagnostic, diagnostic
    assert not errors.getvalue(), errors.getvalue()
