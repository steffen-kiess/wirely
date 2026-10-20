import io
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import TestCase

from wirely.compiler import (
    ParseCache, SourceError, compile_files, compile_strs, parse_source, standard_library_cache,
    standard_library_sources, standard_library_texts,
)
from wirely.compiler import model, program
from wirely.compiler.diagnostics import error_diagnostics


checks = TestCase()


# Errors retain their own ordered hints, which callers cannot modify in place.
element = parse_source('<diagnostics>', 'node type T {}').tree[0]
first_hint = model.CompilerHint(element, 'First hint')
second_hint = model.CompilerHint(element, 'Second hint')
hints = [first_hint]
error = model.ProgramError(element, 'Main error', hints=hints)
hints.append(second_hint)
checks.assertEqual([diagnostic.message for diagnostic in error_diagnostics(error)],
                   ['Main error', 'First hint'])
with checks.assertRaises(TypeError):
    error.hints[0] = second_hint
error.hints = (*error.hints, second_hint)
checks.assertEqual([diagnostic.message for diagnostic in error_diagnostics(error)],
                   ['Main error', 'First hint', 'Second hint'])
checks.assertEqual([diagnostic.message for diagnostic in error_diagnostics(model.ProgramError(element, 'Other error'))],
                   ['Other error'])


# Global graph failures are reported by both source compilation APIs.
for source, message in (
        ('native procedure run;', 'Native procedures must belong to a node type'),
        ('binding unused: value boolean;', "Binding `unused` has no source")):
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
    checks.assertEqual((diagnostics[0].message, diagnostics[0].filename), ('Duplicate node type `T`', later))
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

# Hits promote entries; membership queries do not. Versions count separately.
cache = ParseCache(max_entries=2)
a = ('<lru>', 'node type A {}')
b = ('<lru>', 'node type B {}')
c = ('<lru>', 'node type C {}')
first_a = cache.parse_source(*a)
first_b = cache.parse_source(*b)
checks.assertIs(cache.parse_source(*a), first_a)
cache.parse_source(*c)
checks.assertIn(a, cache)
checks.assertNotIn(b, cache)
checks.assertIn(c, cache)
checks.assertIsNot(cache.parse_source(*b), first_b)
checks.assertNotIn(a, cache)
checks.assertIn(c, cache)
cache.parse_source(*a)
checks.assertNotIn(c, cache)
checks.assertIn(b, cache)
checks.assertIn(a, cache)
cache.clear()
checks.assertNotIn(a, cache)
checks.assertNotIn(b, cache)
checks.assertIsNot(cache.parse_source(*a), first_a)

# A failed parse does not evict a successful source.
cache = ParseCache(max_entries=1)
kept = cache.parse_source(*a)
with checks.assertRaises(SourceError):
    cache.parse_source('<invalid>', 'node')
checks.assertIs(cache.parse_source(*a), kept)

# A zero limit retains nothing; ordinary caches default to 256 entries.
cache = ParseCache(max_entries=0)
checks.assertIsNot(cache.parse_source(*a), cache.parse_source(*a))
checks.assertNotIn(a, cache)
with checks.assertRaises(ValueError):
    ParseCache(max_entries=-1)
cache = ParseCache()
unbounded = ParseCache(max_entries=None)
first = ('<capacity>', '// version 0\n')
for index in range(257):
    entry = ('<capacity>', f'// version {index}\n')
    cache.parse_source(*entry)
    unbounded.parse_source(*entry)
    standard_library_cache.parse_source(*entry)
checks.assertNotIn(first, cache)
checks.assertIn(entry, cache)
checks.assertTrue(all(('<capacity>', f'// version {index}\n') in unbounded for index in range(257)))
checks.assertTrue(all(('<capacity>', f'// version {index}\n') in standard_library_cache for index in range(257)))

# File parsing shares the same keys and retention policy as parsing text.
with tempfile.TemporaryDirectory(prefix='wirely-parse-cache-') as directory:
    path = Path(directory) / 'source.wrl'
    path.write_text(a[1], encoding='utf-8')
    cache = ParseCache(max_entries=1)
    old = cache.parse_file(path)
    checks.assertIs(cache.parse_source(str(path), a[1]), old)
    path.write_text(b[1], encoding='utf-8')
    new = cache.parse_file(path)
    checks.assertIsNot(new, old)
    checks.assertNotIn((path, a[1]), cache)
    checks.assertIn((path, b[1]), cache)

# Concurrent insertions keep a small cache within its entry limit.
cache = ParseCache(max_entries=2)
barrier = threading.Barrier(8)


def parse_bounded(index):
    barrier.wait()
    return cache.parse_source(f'<bounded-{index}>', 'node type Shared {}')


with ThreadPoolExecutor(8) as executor:
    results = list(executor.map(parse_bounded, range(8)))
checks.assertEqual(sum((f'<bounded-{index}>', 'node type Shared {}') in cache for index in range(8)), 2)
checks.assertEqual([source.filename for source in results], [f'<bounded-{index}>' for index in range(8)])


# Compilations cache the standard library, also when they fail, but not other sources.
standard_library_cache.clear()
checks.assertIsNone(compile_strs([('<user>', 'node broken: T {}')], add_std=True, shuffle_seed=None,
                                 on_diagnostic=lambda diagnostic: None))
checks.assertTrue(all(source in standard_library_cache for source in standard_library_texts()))
checks.assertNotIn(('<user>', 'node broken: T {}'), standard_library_cache)
library = standard_library_sources()
checks.assertTrue(all(cached is again for cached, again in zip(library, standard_library_sources())))

# Shared cached sources compile concurrently, including shuffled and layout builds.
cache = ParseCache()
parsed = cache.parse_source('<shuffled>', '''
#![Allow(Unused)]
namespace N {
    struct type S { a: int32, b: int32 }
    open enum type E: uint8 { A = 1, B = 2 }
    closed enum type F { A, B }
    node type T { parameter p: int32 = 3; }
}
node n: N.T {}
binding result: value boolean;
procedure run produces result { result = true; }
''')
barrier = threading.Barrier(8)


def compile_shared(seed):
    barrier.wait()
    timings = []
    diagnostics = []
    graph = compile_strs([parsed], add_std=True, shuffle_seed=seed,
                         shuffle_layout=True, timings=timings, on_diagnostic=diagnostics.append)
    checks.assertIsNotNone(graph, diagnostics)
    checks.assertEqual(dict(timings)['parse'], 0)
    checks.assertEqual(dict(timings)['build syntax tree'], 0)
    output = io.StringIO()
    program.compile(graph).dump(output)
    return output.getvalue()


with ThreadPoolExecutor(8) as executor:
    outputs = list(executor.map(compile_shared, (None, 1, 2, 3, None, 1, 2, 3)))
checks.assertTrue(all(output == outputs[0] for output in outputs))
checks.assertIs(cache.parse_source(parsed.filename, parsed.text), parsed)

# Filenames are snapshots, independent of later changes to caller-owned paths.
class MutablePath:
    def __init__(self, value):
        self.value = value

    def __fspath__(self):
        return self.value


filename = MutablePath('first.wrl')
parsed = cache.parse_source(filename, 'node type T {}')
checks.assertEqual(parsed.filename, 'first.wrl')
filename.value = 'second.wrl'
checks.assertIs(cache.parse_source(Path('first.wrl'), parsed.text), parsed)
diagnostics = []
checks.assertIsNone(compile_strs([parsed, parsed], shuffle_seed=None, on_diagnostic=diagnostics.append))
checks.assertEqual(diagnostics[0].filename, 'first.wrl')


# A parser cache file is written once and then loaded; cached parsers parse alike.
from wirely.compiler import parser as parser_module

with tempfile.TemporaryDirectory(prefix='wirely-parser-cache-') as directory:
    path = Path(directory) / 'parser.cache'
    written = parser_module.create_parser(str(path))
    checks.assertTrue(path.exists())
    # Loading must not rebuild the tables, which would write the file again.
    os.utime(path, ns=(0, 0))
    loaded = parser_module.create_parser(str(path))
    checks.assertEqual(path.stat().st_mtime_ns, 0)
    for _, text in standard_library_texts():
        expected = parser_module.parser.parse(text)
        checks.assertEqual(written.parse(text), expected)
        checks.assertEqual(loaded.parse(text), expected)


async def run_test():
    pass
