import tempfile
from pathlib import Path
from unittest import TestCase

from wirely.compiler import compile_files, compile_strs
from wirely.compiler import program


checks = TestCase()


# Global graph failures are reported by both source compilation APIs.
for source, message in (
        ('native procedure run;', 'Native procedures must belong to a node type'),
        ('temporary unused: boolean;', "Temporary 'unused' has no source")):
    diagnostics = []
    checks.assertIsNone(compile_strs([('<root>', source)], on_diagnostic=diagnostics.append))
    checks.assertEqual(len(diagnostics), 1)
    checks.assertEqual(diagnostics[0].kind, 'error')
    checks.assertEqual(diagnostics[0].message, message)
    checks.assertEqual((diagnostics[0].start, diagnostics[0].end), (0, len(source)))
    with tempfile.TemporaryDirectory(prefix='wirely-compiler-api-') as directory:
        filename = Path(directory) / 'root.wrl'
        filename.write_text(source, encoding='utf-8')
        diagnostics = []
        checks.assertIsNone(compile_files([str(filename)], add_std=False,
                                         on_diagnostic=diagnostics.append))
        checks.assertEqual(diagnostics[0].message, message)
        checks.assertEqual(diagnostics[0].filename, str(filename))

# Syntax errors carry the offset of the unexpected input.
diagnostics = []
compile_strs([('<root>', 'node a: T {}\nnode 1')], on_diagnostic=diagnostics.append)
checks.assertEqual((diagnostics[0].kind, diagnostics[0].start), ('syntax_error', 18))

# Valid root graphs reach program compilation without repeating binding.
source = 'temporary result: boolean; procedure run produces result { result = true; }'
diagnostics = []
graph = compile_strs([('<root>', source)], on_diagnostic=diagnostics.append)
checks.assertIsNotNone(graph)
checks.assertIsNotNone(program.compile(graph, on_diagnostic=diagnostics.append))
checks.assertEqual(diagnostics, [])

# File compilation also accepts path objects when ordering declared types.
with tempfile.TemporaryDirectory(prefix='wirely-compiler-api-') as directory:
    filename = Path(directory) / 'valid.wrl'
    filename.write_text('node type Empty {} node child: Empty {}', encoding='utf-8')
    checks.assertIsNotNone(compile_files([filename], add_std=False))

# Syntax errors retain the failing file's context across multiple input files.
diagnostics = []
checks.assertIsNone(compile_strs([('<first>', ''), ('<syntax>', 'node broken:')],
                               on_diagnostic=diagnostics.append))
checks.assertEqual(diagnostics[0].kind, 'syntax_error')
checks.assertEqual(diagnostics[0].filename, '<syntax>')
checks.assertIn('node broken:', diagnostics[0].context)

# Callback failures escape both graph errors and named progress notifications.
class CallbackFailure(Exception):
    pass


def fail(diagnostic):
    raise CallbackFailure(diagnostic.message)


with checks.assertRaises(CallbackFailure):
    compile_strs([('<root>', 'native procedure run;')], on_diagnostic=fail)
with checks.assertRaises(CallbackFailure):
    compile_strs([('<root>', '')], on_diagnostic=fail, verbose_passes=True)
