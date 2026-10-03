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
    assert engine.program.instances[name].parameters['Value'] == expected


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


# File loading normalizes carriage returns; test these bytes directly.
for literal, message in [
    ('"first\rsecond"', 'Bare carriage return'),
    ('r"first\rsecond"', 'Bare carriage return'),
    ('r#"first\rsecond"#', 'Bare carriage return'),
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
