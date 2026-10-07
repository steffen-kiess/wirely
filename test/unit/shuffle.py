from unittest import TestCase

import wirely.compiler
from wirely.compiler import model as blocks
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
enum type E: uint8 { A = 1, B = 2, C = 3 }
'''


def parse():
    return parse_str(source, filename='<shuffle>')


def names(items):
    return [str(getattr(item, 'name', None) or getattr(item, 'reference', item))
            for item in items]


def order(declarations):
    block_type = next(d for d in declarations if isinstance(d, blocks.BlockType))
    procedure = next(d for d in block_type.declarations if isinstance(d, blocks.DeclarationProcedure))
    nodes = [d for d in declarations if isinstance(d, blocks.DeclarationBlockInstantiation)]
    return (names(declarations), names(block_type.declarations),
            names(procedure.depends.dependencies),
            [names(node.instantiation.parameters) for node in nodes])


def layout_order(declarations):
    struct = next(d for d in declarations if isinstance(d, blocks.DeclarationStructType))
    enum = next(d for d in declarations if isinstance(d, blocks.DeclarationEnumType))
    return names(struct.fields), names(enum.members)


# The same seed gives the same order; some seed changes each shuffled list.
original = order(parse())
results = []
for seed in range(20):
    declarations = parse()
    shuffle_declarations(declarations, seed)
    again = parse()
    shuffle_declarations(again, seed)
    checks.assertEqual(order(declarations), order(again))
    results.append(order(declarations))
for index in range(len(original)):
    checks.assertTrue(any(result[index] != original[index] for result in results))

# Connections keep their relative order and sources; positional lists keep
# their order.
for seed in range(20):
    declarations = parse()
    shuffle_declarations(declarations, seed)
    connections = [(names(d.sources), str(d.target)) for d in declarations
                   if isinstance(d, blocks.DeclarationConnection)]
    checks.assertEqual(connections, [
        (['n1.d'], 'n2.c'), (['n2.d'], 'n1.c'),
        (['n1.d', 'n2.d'], 'n1.a'), (['n1.d'], 'n2.c'),
    ])
    block_type = next(d for d in declarations if isinstance(d, blocks.BlockType))
    procedure = next(d for d in block_type.declarations if isinstance(d, blocks.DeclarationProcedure))
    statements = procedure.body.statements
    checks.assertEqual([statement.name.id_str for statement in statements], ['x', 'd'])
    checks.assertEqual(names(statements[0].initializer.parameters), ['a', 'b'])

# Struct fields and enum members are shuffled only with layout.
original_layout = layout_order(parse())
layout_results = []
for seed in range(20):
    declarations = parse()
    shuffle_declarations(declarations, seed)
    checks.assertEqual(layout_order(declarations), original_layout)
    declarations = parse()
    shuffle_declarations(declarations, seed, layout=True)
    again = parse()
    shuffle_declarations(again, seed, layout=True)
    checks.assertEqual(layout_order(declarations), layout_order(again))
    layout_results.append(layout_order(declarations))
for index in range(len(original_layout)):
    checks.assertTrue(any(result[index] != original_layout[index] for result in layout_results))

# Compilation reads the options from keyword arguments.
sources = wirely.compiler._standard_library_sources(['std'])
for seed in range(5):
    for layout in (False, True):
        result = wirely.compiler.compile_strs(sources, shuffle_seed=seed, shuffle_layout=layout)
        checks.assertIsNotNone(result)
