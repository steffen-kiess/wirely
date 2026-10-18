import os
import subprocess
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import wirely.compiler
from wirely.compiler import model
from wirely.compiler.ast import SourceElement
from wirely.compiler.coordinator import CompilerCoordinator
from wirely.compiler.parser import parse_str
from wirely.compiler.shuffle import shuffle_declarations


checks = TestCase()

source = '''
node type T {
    input a: value int32;
    input b: value int32;
    input c: multi value int32;
    output d: value int32;
    parameter p: int32;
    parameter q: int32;
    procedure run depends on a, b, c produces d {
        let x = Std.Min(a, b);
        d = x;
    }
}
node n1: T { p: 1, q: 2 }
node n2: T { p: 3, q: 4 }
connect n1.d -> n2.c;
connect n2.d -> n1.c;
connect n1.d, n2.d -> n1.a;
connect n1.d -> n2.c;
struct type S { a: int32, b: int32, c: int32 }
open enum type E: uint8 { A = 1, B = 2, C = 3 }
closed enum type F { A, B, C }
native struct type NS { a: int32, b: int32, c: int32 }
native closed enum type NF { A, B, C }
'''


def parse():
    return parse_str(source, filename='<shuffle>')


def names(items):
    return [str(getattr(item, 'name', None) or getattr(item, 'reference', item))
            for item in items]


def enum_members(declarations, *, is_open, is_native=False):
    return names(next(d for d in declarations if isinstance(d, model.DeclarationEnumType)
                      and d.is_open == is_open and d.is_native == is_native).members)


def struct_fields(declarations, *, is_native=False):
    return names(next(d for d in declarations
                      if isinstance(d, model.DeclarationStructType) and d.is_native == is_native).fields)


def order(declarations):
    node_type = next(d for d in declarations if isinstance(d, model.DeclarationNodeType))
    procedure = next(d for d in node_type.declarations if isinstance(d, model.DeclarationProcedure))
    nodes = [d for d in declarations if isinstance(d, model.DeclarationInstance)]
    return (names(declarations), names(node_type.declarations),
            names(procedure.depends.dependencies),
            [names(node.instantiation.parameters) for node in nodes],
            enum_members(declarations, is_open=True))


def layout_order(declarations):
    return struct_fields(declarations), enum_members(declarations, is_open=False)


def native_order(declarations):
    return struct_fields(declarations, is_native=True), enum_members(declarations, is_open=False, is_native=True)


# The same seed gives the same order; some seed changes each shuffled list.
original = order(parse())
results = []
for seed in range(20):
    declarations = parse()
    declarations = shuffle_declarations(declarations, seed)
    again = parse()
    again = shuffle_declarations(again, seed)
    checks.assertEqual(order(declarations), order(again))
    results.append(order(declarations))
for index in range(len(original)):
    checks.assertTrue(any(result[index] != original[index] for result in results))

# Connections keep their relative order and sources; positional lists keep
# their order.
for seed in range(20):
    declarations = parse()
    declarations = shuffle_declarations(declarations, seed)
    connections = [(names(d.sources), str(d.target)) for d in declarations
                   if isinstance(d, model.DeclarationConnection)]
    checks.assertEqual(connections, [
        (['n1.d'], 'n2.c'), (['n2.d'], 'n1.c'),
        (['n1.d', 'n2.d'], 'n1.a'), (['n1.d'], 'n2.c'),
    ])
    node_type = next(d for d in declarations if isinstance(d, model.DeclarationNodeType))
    procedure = next(d for d in node_type.declarations if isinstance(d, model.DeclarationProcedure))
    statements = procedure.body.statements
    checks.assertEqual(statements[0].name.id_str, 'x')
    checks.assertEqual(str(statements[1].expression.target), 'd')
    checks.assertEqual(names(statements[0].initializer.parameters), ['a', 'b'])

# Struct fields and closed enum members are shuffled only with layout, and
# never for native structs and enums.
original_layout = layout_order(parse())
original_native = native_order(parse())
layout_results = []
for seed in range(20):
    declarations = parse()
    declarations = shuffle_declarations(declarations, seed)
    checks.assertEqual(layout_order(declarations), original_layout)
    checks.assertEqual(native_order(declarations), original_native)
    declarations = parse()
    declarations = shuffle_declarations(declarations, seed, layout=True)
    checks.assertEqual(native_order(declarations), original_native)
    again = parse()
    again = shuffle_declarations(again, seed, layout=True)
    checks.assertEqual(layout_order(declarations), layout_order(again))
    layout_results.append(layout_order(declarations))
for index in range(len(original_layout)):
    checks.assertTrue(any(result[index] != original_layout[index] for result in layout_results))

# Source sorting is stable without shuffling and reproducibly random with it.
declarations = parse()
coordinator = CompilerCoordinator(SimpleNamespace())
original_keys = [(str(d.source_position.filename or ''), d.source_position.meta.start_pos)
                 for d in declarations]
checks.assertEqual([coordinator.source_order(d) for d in declarations], original_keys)
original_order = names(sorted(declarations, key=coordinator.source_order))
source_orders = []
for seed in range(20):
    coordinator = CompilerCoordinator(SimpleNamespace(), shuffle_seed=seed)
    keys = [coordinator.source_order(d) for d in declarations]
    checks.assertEqual([coordinator.source_order(d) for d in reversed(declarations)], keys[::-1])
    again = CompilerCoordinator(SimpleNamespace(), shuffle_seed=seed)
    checks.assertEqual([again.source_order(d) for d in parse()], keys)
    source_orders.append(names(sorted(declarations, key=coordinator.source_order)))
checks.assertNotEqual(source_orders[0], original_order)
checks.assertGreater(len({tuple(items) for items in source_orders}), 1)

# Synthetic source positions retain their fallback and original diagnostic data.
position = model.SourcePosition(filename=None, meta=None, input_data='')
element = SourceElement(source_position=position)
checks.assertEqual(CompilerCoordinator(SimpleNamespace()).source_order(element), ('', -1))
coordinator = CompilerCoordinator(SimpleNamespace(), shuffle_seed=0)
checks.assertEqual(coordinator.source_order(element)[1:], ('', -1))
checks.assertIs(element.source_position, position)
checks.assertIsNone(position.filename)
checks.assertIsNone(position.meta)

# Keys are reproducible in separate processes with different Python hash seeds.
script = '''
from wirely.compiler.shuffle import shuffled_source_order
for position in range(10):
    print(shuffled_source_order(('file\\nwith Unicode ä.wrl', position), 0)[0].hex())
'''
process_keys = [subprocess.check_output(
    [sys.executable, '-c', script],
    env={**os.environ, 'PYTHONHASHSEED': hash_seed}, text=True,
) for hash_seed in ('1', '2')]
checks.assertEqual(*process_keys)

# Compilation reads the options from keyword arguments.
sources = wirely.compiler.standard_library_sources(['std'])
for seed in range(5):
    for layout in (False, True):
        result = wirely.compiler.compile_strs(sources, shuffle_seed=seed, shuffle_layout=layout)
        checks.assertIsNotNone(result)

# By default the seed comes from the environment; None disables shuffling.
members = ['A', 'B', 'C', 'D', 'E', 'F']
enum_source = [('<members>', f'open enum type M: uint8 {{ {", ".join(f"{m} = {n}" for n, m in enumerate(members))} }}')]
shuffled = []
for seed in range(5):
    with patch.dict(os.environ, {'WIRELY_COMPILER_SHUFFLE_SEED': str(seed)}):
        unshuffled = wirely.compiler.compile_strs(enum_source, shuffle_seed=None)
        checks.assertEqual(list(unshuffled.enum_types['M'].members), members)
        shuffled.append(list(wirely.compiler.compile_strs(enum_source).enum_types['M'].members))
checks.assertTrue(any(order != members for order in shuffled))


async def run_test():
    pass
