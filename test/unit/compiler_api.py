import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import TestCase

from wirely.compiler import (
    ParseCache, SourceError, compile_files, compile_strs, parse_source, standard_library_cache,
    standard_library_sources, standard_library_texts,
)
from wirely.compiler import program


checks = TestCase()


# Global graph failures are reported by both source compilation APIs.
for source, message in (
        ('native procedure run;', 'Native procedures must belong to a node type'),
        ('binding unused: value boolean;', "Binding 'unused' has no source")):
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
source = '#[Allow(Unused)] binding result: value boolean; procedure run produces result { result = true; }'
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


# Parsed sources and source text can be mixed and keep their order.
first = parse_source('<first>', 'node type T {}')
for sources, later in (([first, ('<second>', 'node type T {}')], '<second>'),
                       ([('<second>', 'node type T {}'), first], '<first>')):
    diagnostics = []
    checks.assertIsNone(compile_strs(sources, on_diagnostic=diagnostics.append, shuffle_seed=None))
    checks.assertEqual((diagnostics[0].message, diagnostics[0].filename), ("Duplicate node type T", later))
with tempfile.TemporaryDirectory(prefix='wirely-compiler-api-') as directory:
    filename = Path(directory) / 'child.wrl'
    filename.write_text('node child: Empty {}', encoding='utf-8')
    checks.assertIsNotNone(compile_files([parse_source('<empty>', 'node type Empty {}'), filename], add_std=False))

# Sources that do not parse report what a compilation would.
for text in ('node a: T {}\nnode 1', 'constant c: string = "\\q";'):
    with checks.assertRaises(SourceError) as raised:
        parse_source('<root>', text)
    diagnostics = []
    checks.assertIsNone(compile_strs([('<root>', text)], on_diagnostic=diagnostics.append))
    checks.assertEqual(raised.exception.diagnostics(), diagnostics)

# A cache returns the first parse of a source and does not keep failures.
cache = ParseCache()
parsed = cache.parse_source('<cached>', 'node type C {}')
checks.assertIs(cache.parse_source('<cached>', 'node type C {}'), parsed)
checks.assertIsNot(cache.parse_source('<cached>', 'node type D {}'), parsed)
with checks.assertRaises(SourceError):
    cache.parse_source('<broken>', 'node broken:')
checks.assertNotIn(('<broken>', 'node broken:'), cache)
cache = ParseCache()
barrier = threading.Barrier(8)


def parse_shared():
    barrier.wait()
    return cache.parse_source('<shared>', 'node type Shared {}')


with ThreadPoolExecutor(8) as executor:
    results = [future.result() for future in [executor.submit(parse_shared) for _ in range(8)]]
checks.assertEqual(len({id(result) for result in results}), 1)

# Compilations cache the standard library, also when they fail, but not other sources.
standard_library_cache.clear()
checks.assertIsNone(compile_strs([('<user>', 'node broken: T {}')], add_std=True, shuffle_seed=None,
                                 on_diagnostic=lambda diagnostic: None))
checks.assertTrue(all(source in standard_library_cache for source in standard_library_texts()))
checks.assertNotIn(('<user>', 'node broken: T {}'), standard_library_cache)
library = standard_library_sources()
checks.assertTrue(all(cached is again for cached, again in zip(library, standard_library_sources())))

# Shuffling, which reorders nested declarations, leaves parsed sources unchanged.
parsed = parse_source('<shuffled>', 'namespace N { node type A {} node type B {} node type C {} node type D {} }')
declarations = list(parsed.tree[0].declarations)
for seed in range(5):
    checks.assertIsNotNone(compile_strs([parsed], shuffle_seed=seed))
checks.assertEqual(list(parsed.tree[0].declarations), declarations)


async def run_test():
    pass
