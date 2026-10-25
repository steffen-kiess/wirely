import os
from unittest import TestCase

from wirely.compiler.sources import ParseCache, ParsedSource, standard_library_sources
from wirely.compiler.analysis import analyze
from wirely.compiler.editing import apply_edits
from wirely.compiler.outline import Connection, Node, outline
from wirely.editor.program import Program


checks = TestCase()


class RecordingCache(ParseCache):
    def __init__(self, *, max_entries=32):
        super().__init__(max_entries=max_entries)
        self.requests = []

    def parse_source(self, filename: str | os.PathLike, text: str) -> ParsedSource:
        self.requests.append((os.fsdecode(filename), text, (filename, text) in self))
        return super().parse_source(filename, text)


library = '''node type T {
    parameter p: int32 = 1;
    default input: value boolean;
    default output: value boolean;
}
'''
main = 'node first: T {}\nnode second: T {}\nconnect first -> second;\n'
sources = [('lib.wrl', library), ('main.wrl', main)]
cache = RecordingCache()
program = Program(sources, cache=cache)
checks.assertEqual(cache.requests, [('lib.wrl', library, False), ('main.wrl', main, False)])
cache.requests.clear()
program.refresh(sources)
checks.assertEqual(cache.requests, [('lib.wrl', library, True), ('main.wrl', main, True)])

# Only edited files miss, and reverting to retained text reuses its parse.
changed = main + '// edited\n'
cache.requests.clear()
program.refresh([('lib.wrl', library), ('main.wrl', changed)])
checks.assertEqual(cache.requests, [('lib.wrl', library, True), ('main.wrl', changed, False)])
cache.requests.clear()
program.refresh(sources)
checks.assertTrue(all(hit for _, _, hit in cache.requests))

# Invalid edits never replace a retained good parse or its recovery diagnostics.
for _ in range(2):
    cache.requests.clear()
    program.refresh([('lib.wrl', library), ('main.wrl', 'node')])
    checks.assertEqual(set(program.parse_errors), {'main.wrl'})
    checks.assertEqual(cache.requests, [('lib.wrl', library, True), ('main.wrl', 'node', False)])
    checks.assertNotIn(('main.wrl', 'node'), cache)
program.refresh(sources)
checks.assertFalse(program.parse_errors)
checks.assertEqual(len(program.outline.find(Node(('first',)))), 1)

# All edit commands use the program cache, including nested and combined edits.
commands = [
    lambda: program.move({Node(('first',)): (1, 2), Node(('second',)): (3, 4)}),
    lambda: program.reset_positions([Node(('first',)), Node(('second',))]),
    lambda: program.connect((), 'second', 'first'),
    lambda: program.reconnect(Connection((), 'first', 'second'), target='first'),
    lambda: program.delete([Connection((), 'first', 'second'), Node(('second',))]),
    lambda: program.add_node((), 'T'),
    lambda: program.set_parameter(Node(('first',)), 'p', '2'),
    lambda: program.set_parameter(Node(('first',)), 'p', ''),
    lambda: program.set_node_type(Node(('first',)), 'T'),
    lambda: program.rename(Node(('first',)), 'renamed'),
]
for command in commands:
    cache.requests.clear()
    command()
    checks.assertTrue(cache.requests)
    checks.assertTrue(all(hit for filename, text, hit in cache.requests if (filename, text) in sources))
    checks.assertIn(('lib.wrl', library), cache)
checks.assertEqual(program.fresh_name((), 'first'), 'first2')

# Rename verification has already cached the resulting sources for refresh.
edits = program.rename(Node(('first',)), 'renamed')
renamed = apply_edits(sources, edits)
cache.requests.clear()
program.refresh(renamed)
checks.assertTrue(all(hit for _, _, hit in cache.requests))
checks.assertEqual(len(program.outline.find(Node(('renamed',)))), 1)
checks.assertFalse(program.outline.find(Node(('first',))))

# Standalone outline and analysis callers can share the same cache too.
cache.requests.clear()
outline(renamed, add_std=True, cache=cache)
analyze(renamed, add_std=True, cache=cache)
checks.assertEqual(len(cache.requests), 4)
checks.assertTrue(all(hit for _, _, hit in cache.requests))
checks.assertEqual({filename for filename, _, _ in cache.requests}, {'lib.wrl', 'main.wrl'})

# Editor caches are independent; eviction never removes standard-library entries.
other_cache = RecordingCache(max_entries=1)
stdlib = standard_library_sources()
other = Program(sources, cache=other_cache)
checks.assertNotIn(('lib.wrl', library), other_cache)
checks.assertIn(('main.wrl', main), other_cache)
other.refresh(sources)
checks.assertTrue(all(not hit for _, _, hit in other_cache.requests))
checks.assertTrue(all(before is after for before, after in zip(stdlib, standard_library_sources())))
checks.assertIn(('lib.wrl', library), cache)


async def run_test():
    pass
