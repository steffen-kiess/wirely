import os
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
        compile_negative_test(str(filename), verbose=False)


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
// SH: Previous member was here
// SE: Duplicate enum member On
open enum type Mode: uint8 {
  On = 1, // H: Previous member was here
  On = 2, // E: Duplicate enum member On
}

// TEST: unknown member
open enum type Mode: uint8 { On = 1 }
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
run_fixture_with_std('open enum type Bad: int8 { One = 1; } // E: Unexpected token\n')
run_fixture_with_std('''// TEST: unexpected token
open enum type Bad: int8 {
  One = 1; // E: Unexpected token
}
// TEST: unexpected character
node bad: Std.Constant { Type: String, Value: "unterminated }
// ^E: No terminal matches
// TEST: unexpected end of input
open enum type Bad: int8 { One = 1 // E: Syntax error
// TEST: semantic error after syntax errors
node bad: Std.Constant { Type: uint8, Value: -1 } // E: out of range
''')

with checks.assertRaises(AssertionError) as error:
    run_fixture_with_std('''// TEST: wrong syntax expectation
open enum type Bad: int8 { One = 1; } // E: No terminal matches
// TEST: another mismatch
node bad: Std.Constant { Type: uint8, Value: -1 } // E: wrong expectation
''')
message = str(error.exception)
assert 'wrong syntax expectation' in message and 'another mismatch' in message
assert 'Unexpected token' in message

invalid = [
    ('', 'no expected diagnostics'),
    ('// TEST\n', 'no expected diagnostics'),
    ('// TEST\n// TEST\n' + legacy, 'no expected diagnostics'),
    ('// TEST:   \n' + legacy, 'empty test label'),
    ('// TEST\nnode valid: Std.Constant { Type: uint8, Value: 1 } // E: out of range',
     'compiled=True'),
    ('// TEST\n' + legacy.replace('out of range', 'wrong expectation'),
     'diagnostic mismatch'),
    ('// TEST\n' + legacy.replace('// E: out of range', '// E:'),
     'Malformed annotation on line 3'),
    ('// TEST\n// ^E: out of range\n' + legacy,
     'Malformed annotation on line 3'),
    ('// TEST\nopen enum type Mode: uint8 {\n  On = 1,\n'
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
        kwargs['on_diagnostic'](wirely.compiler.Diagnostic('internal_error', 'simulated failure'))
        return None
    return original_compile(sources, **kwargs)


with patch('wirely.compiler.compile_strs', side_effect=fail_first):
    with checks.assertRaises(AssertionError) as error:
        run_fixture_with_std('''// TEST: internal failure
node bad: Missing {} // E: simulated
// TEST: later case
node bad: Missing {} // E: wrong expectation
''')
message = str(error.exception)
assert 'compiler failed internally' in message and 'later case' in message

# Default library loading remains opt-in for source compilation.
diagnostics = []
assert wirely.compiler.compile_strs([
    ('<no-libraries>', 'node value: Std.Constant { Type: uint8, Value: 1 }'),
], on_diagnostic=diagnostics.append) is None
assert len(diagnostics) == 1
assert diagnostics[0].kind == 'error'
assert diagnostics[0].filename == '<no-libraries>' and diagnostics[0].line == 1
assert 'Cannot find node type Std.Constant' in diagnostics[0].message
compiled = wirely.compiler.compile_strs([
    ('<with-libraries>', 'node value: Std.Constant { Type: uint8, Value: 1 }'),
], add_std=True)
assert compiled is not None
assert program_compiler.compile(compiled) is not None


# Library availability is explicit, and built-in types need no library.
run_fixture('node bad: Std.Constant { Type: uint8, Value: 1 } // E: Cannot find node type Std.Constant\n')
run_fixture('open enum type Mode: uint8 { Bad = -1 } // E: out of range\n')
run_fixture('''// LIBRARIES: matter
// TEST: std is absent
node bad: Std.Constant { Type: uint8, Value: 1 } // E: Cannot find node type Std.Constant
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
node bad: Std.Constant { Type: uint8, Value: -1 } // E: Cannot find node type Std.Constant
// TEST: override does not carry forward
node bad: Std.Constant { Type: uint8, Value: -1 } // E: out of range
''')
run_fixture('// LIBRARIES:\nnode bad: Missing {} // E: Cannot find node type Missing\n')
run_fixture('''// TEST: a directive in a string is ordinary content
node type Holder { parameter Text: String = "// LIBRARIES: missing"; }
// LIBRARIES_EXTRA is an ordinary comment.
node bad: Missing {} // E: Cannot find node type Missing
''')

for metadata, message in [
    ('// LIBRARIES std', 'Malformed library metadata'),
    ('// LIBRARIES: missing', 'Unknown library'),
    ('// LIBRARIES: ../std', 'Malformed library name'),
    ('// LIBRARIES: std,', 'Malformed library name'),
    ('// LIBRARIES: std, std', 'Duplicate library name'),
    ('// LIBRARIES: std\n// LIBRARIES:', 'Multiple library directives'),
]:
    with checks.assertRaises(AssertionError) as error:
        run_fixture(metadata + '\n' + legacy)
    assert message in str(error.exception), str(error.exception)

# A malformed case override is rejected too.
with checks.assertRaises(AssertionError):
    run_fixture('// LIBRARIES: std\n' + legacy + '\n// TEST: override\n// LIBRARIES: missing\n' + legacy)


# Alternate annotations match only within their case when a seed is set.
def run_diagnostics(source, diagnostics):
    def compile_case(sources, *, on_diagnostic, **kwargs):
        filename = sources[-1][0]
        for kind, line, message, other_file in diagnostics:
            on_diagnostic(wirely.compiler.Diagnostic(
                kind, message, filename=other_file or filename, line=line,
            ))
        return None

    with patch('wirely.compiler.compile_strs', side_effect=compile_case):
        run_fixture(source)


alternate = '''// TEST: alternate
first // E: original
second
// SE: shuffled
'''
original = [('error', 2, 'original', None)]
relocated = [('error', 3, 'shuffled', None)]
for seed in ('', '0', '1', '2', '3', '4', '5'):
    with patch.dict(os.environ, {'WIRELY_COMPILER_SHUFFLE_SEED': seed}):
        run_diagnostics(alternate, relocated if seed else original)
        with checks.assertRaises(AssertionError):
            run_diagnostics(alternate, original if seed else relocated)

with patch.dict(os.environ, {'WIRELY_COMPILER_SHUFFLE_SEED': '1'}):
    # Cases without alternate annotations retain exact location matching.
    with checks.assertRaises(AssertionError):
        run_diagnostics('first // E: original\nsecond\n', [('error', 2, 'original', None)])
    # An opted-in case does not relax a later case.
    with checks.assertRaises(AssertionError):
        run_diagnostics(alternate + '''// TEST: strict
first // E: original
second
''', relocated + [('error', 7, 'original', None)])

    invalid_diagnostics = [
        [],
        [('hint', 3, 'shuffled', None)],
        [('warning', 3, 'shuffled', None)],
        [('error', 3, 'wrong message', None)],
        relocated * 2,
        relocated + [('hint', 3, 'extra hint', None)],
        [('error', 1, 'shuffled', None)],
        [('error', 5, 'shuffled', None)],
        [('error', None, 'shuffled', None)],
        [('error', 3, 'shuffled', '/tmp/other-negative-case.wrl')],
        [('internal_error', 3, 'shuffled', None)],
    ]
    for diagnostics in invalid_diagnostics:
        with checks.assertRaises(AssertionError):
            run_diagnostics(alternate, diagnostics)

    # Overlapping fragments still require distinct matching diagnostics.
    overlapping = '''first // E: original
// SE: conflict
// SE: conflict named
// SH: context
// SW: warning
'''
    diagnostics = [('error', 1, 'conflict named', None),
                   ('error', 1, 'conflict other', None),
                   ('hint', 1, 'context', None),
                   ('warning', 1, 'warning', None)]
    run_diagnostics(overlapping, diagnostics)
    with checks.assertRaises(AssertionError):
        run_diagnostics(overlapping, diagnostics[:1] + diagnostics[2:])
    with checks.assertRaisesRegex(AssertionError, 'compiled=True'):
        run_fixture('node type Valid {} // E: wrong\n// SE: wrong\n')
    # A hint alone cannot establish rejection, even with matching expectations.
    with checks.assertRaises(AssertionError):
        run_diagnostics('first // E: original\n// SH: context\n',
                        [('hint', 1, 'context', None)])

for seed in ('', '1'):
    with patch.dict(os.environ, {'WIRELY_COMPILER_SHUFFLE_SEED': seed}):
        for annotation in ('// SE:', '// SH:', '// SW:', '// ^SE: wrong', '// SE wrong'):
            with checks.assertRaisesRegex(AssertionError, 'Malformed annotation'):
                run_diagnostics('first // E: original\n' + annotation + '\n', original)
        with checks.assertRaisesRegex(AssertionError, 'no expected diagnostics'):
            run_diagnostics('first\n// SE: shuffled\n', relocated)
        with checks.assertRaisesRegex(AssertionError, 'no expected diagnostics'):
            run_diagnostics('// SE: shuffled\n// TEST: next\nfirst // E: original\n', original)


async def run_test():
    pass
