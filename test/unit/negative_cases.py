import contextlib
import io
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import wirely.compiler
from wirely.compiler import program as program_compiler
from wirely.test import compile_negative_test


checks = TestCase()


def run_fixture(source):
    with tempfile.TemporaryDirectory(prefix='wirely-negative-cases-') as directory:
        filename = Path(directory) / 'cases.wrl'
        filename.write_text(source, encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            compile_negative_test(str(filename))


def run_fixture_with_std(source):
    run_fixture('// LIBRARIES: std\n' + source)


# Unmarked fixtures, initial annotated cases, and comment-only preambles.
legacy = 'node bad: Std.Constant { Type: uint8, Value: -1 } // E: out of range\n'
run_fixture_with_std(legacy)
run_fixture_with_std(legacy + '''
// TEST
node bad: Std.Constant { Type: uint8, Value: 256 } // E: out of range
''')
run_fixture_with_std('''// A description before the first case.
/* A block comment /* with nesting */ in the preamble. */

  // TEST: duplicate members
// Reusing names below also exercises isolation between cases.
enum type Mode: uint8 {
  On = 1, // H: Previous member was here
  On = 2, // E: Duplicate enum member On
}

// TEST: unknown member
enum type Mode: uint8 { On = 1 }
node bad: Std.Constant { Type: Mode, Value: Mode.Missing }
// ^E: Unknown member 'Missing' of Mode

// TEST: declaration isolation
node bad: Std.Constant { Type: Mode, Value: Mode.On } // E: Cannot find type Mode
''')
# Labels are descriptive; repeated labels and indentation are accepted.
run_fixture_with_std('''\t// TEST: bounds\x20\x20
node bad: Std.Constant { Type: uint8, Value: -1 } // E: out of range
  // TEST: bounds\t
node bad: Std.Constant { Type: uint8, Value: 256 } // E: out of range
''')
# Inline strings and comments containing TEST are not case boundaries.
run_fixture_with_std('''node type Holder { parameter Text: String = "// TEST"; }
// TESTING is an ordinary comment.
node bad: Std.Constant { Type: uint8, Value: -1 } // E: out of range
''')


# Syntax diagnostics use the same annotations and physical line numbers.
run_fixture_with_std('enum type Bad: int8 { One = 1; } // E: Unexpected token\n')
run_fixture_with_std('''// TEST: unexpected token
enum type Bad: int8 {
  One = 1; // E: Unexpected token
}
// TEST: unexpected character
node bad: Std.Constant { Type: String, Value: "unterminated }
// ^E: No terminal matches
// TEST: unexpected end of input
enum type Bad: int8 { One = 1 // E: Syntax error
// TEST: semantic error after syntax errors
node bad: Std.Constant { Type: uint8, Value: -1 } // E: out of range
''')

with checks.assertRaises(AssertionError) as error:
    run_fixture_with_std('''// TEST: wrong syntax expectation
enum type Bad: int8 { One = 1; } // E: No terminal matches
// TEST: another mismatch
node bad: Std.Constant { Type: uint8, Value: -1 } // E: wrong expectation
''')
message = str(error.exception)
assert 'wrong syntax expectation' in message and 'another mismatch' in message
assert 'Unexpected token' in message

# A location header alone is not a complete syntax diagnostic.
for suffix in ['', '\n']:
    def incomplete_syntax(sources, *, suffix=suffix, **kwargs):
        print(f'Error in {sources[-1][0]}:1' + suffix)
        return None

    with patch('wirely.compiler.compile_strs', side_effect=incomplete_syntax):
        with checks.assertRaises(AssertionError) as error:
            run_fixture_with_std(legacy)
    assert 'malformed syntax diagnostic' in str(error.exception)


invalid = [
    ('', 'no expected diagnostics'),
    ('// TEST\n', 'no expected diagnostics'),
    ('// TEST\n// TEST\n' + legacy, 'no expected diagnostics'),
    (legacy + '// TEST\n', 'no expected diagnostics'),
    ('// TEST:   \n' + legacy, 'empty test label'),
    ('// TEST\nnode valid: Std.Constant { Type: uint8, Value: 1 }',
     'no expected diagnostics'),
    ('// TEST\nnode valid: Std.Constant { Type: uint8, Value: 1 } // E: out of range',
     'compiled=True'),
    ('// TEST\n' + legacy.replace('out of range', 'wrong expectation'),
     'diagnostic mismatch'),
    ('// TEST\n' + legacy.replace('// E: out of range', '// E:'),
     'Malformed annotation on line 3'),
    ('// TEST\n// ^E: out of range\n' + legacy,
     'Malformed annotation on line 3'),
    (legacy + '// TEST\n// ^^E: out of range\n' + legacy,
     'Malformed annotation on line 4'),
    ('// TEST\nenum type Mode: uint8 {\n  On = 1,\n'
     '  On = 2, // E: Duplicate enum member On\n}', 'unexpected='),
]
for source, expected in invalid:
    with checks.assertRaises(AssertionError) as error:
        run_fixture_with_std(source)
    assert expected in str(error.exception), str(error.exception)

# Validate every case and report both failures with their names and locations.
with checks.assertRaises(AssertionError) as error:
    run_fixture_with_std('''// TEST: first
node bad: Std.Constant { Type: uint8, Value: -1 } // E: first mismatch
// TEST: second
node bad: Std.Constant { Type: uint8, Value: 256 } // E: second mismatch
''')
message = str(error.exception)
assert 'first (line 3)' in message and 'second (line 5)' in message
assert 'first mismatch' in message and 'second mismatch' in message

# An internally failed case still allows later cases to run.
original_compile = wirely.compiler.compile_strs


def fail_first(sources, **kwargs):
    if '// E: simulated' in sources[-1][1]:
        print('Internal compiler error: simulated failure')
        return None
    return original_compile(sources, **kwargs)


with patch('wirely.compiler.compile_strs', side_effect=fail_first) as compiler:
    with checks.assertRaises(AssertionError) as error:
        run_fixture_with_std('''// TEST: internal failure
node bad: Missing {} // E: simulated
// TEST: later case
node bad: Missing {} // E: wrong expectation
''')
    assert compiler.call_count == 2
message = str(error.exception)
assert 'compiler failed internally' in message and 'later case' in message

for stream, text in [('stderr', 'unexpected stderr'),
                     ('stdout', 'Traceback (most recent call last)'),
                     ('stdout', 'Error without a location')]:
    def emit_failure(sources, *, stream=stream, text=text, **kwargs):
        print(text, file=getattr(sys, stream))
        return None

    with patch('wirely.compiler.compile_strs', side_effect=emit_failure):
        with checks.assertRaises(AssertionError) as error:
            run_fixture_with_std(legacy)
    assert ('malformed diagnostic' if text.startswith('Error')
            else 'compiler failed internally') in str(error.exception)

# Default library loading remains opt-in for source compilation.
with contextlib.redirect_stdout(io.StringIO()) as output:
    assert wirely.compiler.compile_strs([
        ('<no-libraries>', 'node value: Std.Constant { Type: uint8, Value: 1 }'),
    ]) is None
assert 'Cannot find block type Std.Constant' in output.getvalue()
compiled = wirely.compiler.compile_strs([
    ('<with-libraries>', 'node value: Std.Constant { Type: uint8, Value: 1 }'),
], add_std=True)
assert compiled is not None
assert program_compiler.compile(compiled) is not None


# Library availability is explicit, and built-in types need no library.
run_fixture('node bad: Std.Constant { Type: uint8, Value: 1 } // E: Cannot find block type Std.Constant\n')
run_fixture('enum type Mode: uint8 { Bad = -1 } // E: out of range\n')
run_fixture('''// LIBRARIES: matter
// TEST: std is absent
node bad: Std.Constant { Type: uint8, Value: 1 } // E: Cannot find block type Std.Constant
// TEST: matter is available
node type Holder {
  parameter Mode: ThermostatSystemModeEnum = ThermostatSystemModeEnum.Missing; // E: Unknown member
}
node holder: Holder {}
''')
run_fixture('''// LIBRARIES: std
// TEST: matter is absent
node bad: Std.Constant { Type: ThermostatSystemModeEnum, Value: ThermostatSystemModeEnum.Off } // E: Cannot find type ThermostatSystemModeEnum
// TEST: multiple selected libraries
// LIBRARIES: std, matter
node bad: Std.Constant { Type: ThermostatSystemModeEnum, Value: ThermostatSystemModeEnum.Missing } // E: Unknown member
''')

# File defaults include an initial unmarked case; overrides replace the list.
run_fixture('// LIBRARIES: std\n' + legacy + '''
// TEST: empty override
// LIBRARIES:
node bad: Std.Constant { Type: uint8, Value: -1 } // E: Cannot find block type Std.Constant
// TEST: override does not carry forward
node bad: Std.Constant { Type: uint8, Value: -1 } // E: out of range
''')
run_fixture('// LIBRARIES:\nnode bad: Missing {} // E: Cannot find block type Missing\n')
run_fixture('// TEST: selected case\n\t// LIBRARIES : std\t\n' + legacy)
run_fixture('''// TEST: a directive in a string is ordinary content
node type Holder { parameter Text: String = "// LIBRARIES: missing"; }
// LIBRARIES_EXTRA is an ordinary comment.
node bad: Missing {} // E: Cannot find block type Missing
''')

for metadata, message in [
    ('// LIBRARIES std', 'Malformed library metadata'),
    ('// LIBRARIES: missing', 'Unknown library'),
    ('// LIBRARIES: ../std', 'Malformed library name'),
    ('// LIBRARIES: STD', 'Malformed library name'),
    ('// LIBRARIES: std,', 'Malformed library name'),
    ('// LIBRARIES: std,,matter', 'Malformed library name'),
    ('// LIBRARIES: std, std', 'Duplicate library name'),
    ('// LIBRARIES: std\n// LIBRARIES:', 'Multiple library directives'),
    ('// LIBRARIES:\n// LIBRARIES: std', 'Multiple library directives'),
]:
    for prefix in ['', '// TEST: malformed metadata\n']:
        with checks.assertRaises(AssertionError) as error:
            run_fixture(prefix + metadata + '\n' + legacy)
        assert message in str(error.exception), str(error.exception)

# Only the requested sources are supplied, and physical locations survive headers.
source_selections = []


def observe_sources(sources, **kwargs):
    source_selections.append(([Path(filename).stem for filename, _ in sources[:-1]], kwargs['add_std']))
    return original_compile(sources, **kwargs)


with patch('wirely.compiler.compile_strs', side_effect=observe_sources):
    run_fixture('''// LIBRARIES: std

// TEST: inherited library
node bad: Std.Constant { Type: uint8, Value: -1 }
// ^E: out of range
// TEST: no libraries
// LIBRARIES:
node bad: Std.Constant { Type: uint8, Value: -1 } // E: Cannot find block type Std.Constant
// TEST: selected libraries
// LIBRARIES: std, matter
node bad: Std.Constant { Type: ThermostatSystemModeEnum, Value: ThermostatSystemModeEnum.Missing }
// ^E: Unknown member
''')
assert source_selections == [(['std'], False), ([], False), (['std', 'matter'], False)]
