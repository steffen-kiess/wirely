from unittest import TestCase

from wirely.compiler.editing import apply_edits
from wirely.compiler.outline import Connection, Node, NodeType, Port, Procedure, State
from wirely.editor import graph_model
from wirely.editor.graph_model import Unresolved, layout_edges
from wirely.editor.layout import COLUMN_GAP, LayoutItem, layout
from wirely.editor.program import Program, node_name, renamed_path


checks = TestCase()

library = '''node type Counter {
  input Step: event int32;
  default output: value int32;
  state count: int32 = 0;

  procedure add
    depends on Step as step, count.old as old
    produces count as new
  {
    new = old + step;
  }
  connect count.old -> default output;
}
'''
main = '''node source: Std.DebugEventSource { Type: int32 }
node counter: Counter {}
node printer: Std.DebugValuePrinter { Type: int32 }
node broken: Missing {}
connect source -> counter.Step;
connect counter -> printer;
connect broken.Out -> nowhere.x;
'''
program = Program([('lib.wrl', library), ('main.wrl', main)])
checks.assertEqual(program.user_scopes(), [(), ('Counter',)])


def sockets(item):
    return [socket.reference for socket in item.inputs], [socket.reference for socket in item.outputs]


# A node type body: its inputs are sources, its outputs targets, state has
# old and new outputs, and procedure ports are keyed by their aliases.
body = graph_model.build(program, ('Counter',))
checks.assertEqual((body.kind, body.filenames), ('node_type', ('lib.wrl',)))
checks.assertEqual([(item.kind, item.title, sockets(item)) for item in body.items], [
    ('input', 'Step', ([], ['Step'])),
    ('output', 'default output', (['default output'], [])),
    ('state', 'count', (['count'], ['count.old', 'count.new'])),
    ('procedure', 'add', (['add.step', 'add.old'], ['add.new'])),
])
count = State(('Counter', 'count'))
add = Procedure(('Counter', 'add'))
checks.assertEqual([(edge.kind, edge.source, edge.target) for edge in body.edges], [
    ('reference', (Port(('Counter', 'Step'), 'input'), None), (add, 'step')),
    ('reference', (count, 'old'), (add, 'old')),
    ('reference', (add, 'new'), (count, None)),
    ('connect', (count, 'old'), (Port(('Counter', 'default output'), 'output'), None)),
])
# Reading state.old does not order the layout.
checks.assertEqual(layout_edges(body), [(Port(('Counter', 'Step'), 'input'), add), (add, count)])

# Nodes get resolved ports; ends without a resolved port or element are
# marked unresolved.
graph = graph_model.build(program, ())
checks.assertEqual(graph.item(Node(('counter',))).node_type, NodeType(('Counter',)))
checks.assertEqual(sockets(graph.item(Node(('counter',)))), (['counter.Step'], ['counter']))
checks.assertEqual(graph.item(Node(('counter',))).inputs[0].type_text, 'int32')
broken = graph.item(Node(('broken',)))
checks.assertEqual((sockets(broken), broken.outputs[0].resolved), (([], ['broken.Out']), False))
checks.assertTrue(broken.diagnostics)
nowhere = graph.item(Unresolved(('nowhere',)))
checks.assertEqual((nowhere.kind, sockets(nowhere)), ('unresolved', (['nowhere.x'], [])))

# A graph is frozen while a file it was shown from fails to parse, and
# missing once its scope is gone.
program.refresh([('lib.wrl', library), ('main.wrl', main + 'node')])
checks.assertEqual(list(program.parse_errors), ['main.wrl'])
checks.assertEqual(program.scope_state((), graph.filenames), 'frozen')
checks.assertEqual(program.scope_state(('Counter',), body.filenames), 'live')
program.refresh([('lib.wrl', ''), ('main.wrl', main)])
checks.assertEqual(program.scope_state(('Counter',), body.filenames), 'missing')
program.refresh([('lib.wrl', library), ('main.wrl', main)])


def edited(edits):
    return dict(apply_edits(program.sources, edits))


# Moving several elements preserves coordinates, in one edit per file.
edits = program.move({Node(('source',)): (10.4, -20.6), Node(('printer',)): (300, 0),
                      count: (5, 5)})
checks.assertEqual(sorted(edit.filename for edit in edits), ['lib.wrl', 'main.wrl'])
texts = edited(edits)
checks.assertIn('#![Diagram.Position(X = 10.4, Y = -20.6)]', texts['main.wrl'])
checks.assertIn('#![Diagram.Position(X = 300, Y = 0)]', texts['main.wrl'])
checks.assertIn('#[Diagram.Position(X = 5, Y = 5)]\n  state count', texts['lib.wrl'])
texts = edited(program.move({Node(('source',)): (-3.0, -0.0)}))
checks.assertIn('#![Diagram.Position(X = -3, Y = 0)]', texts['main.wrl'])

# Resetting positions removes them; elements without one are unchanged.
moved = Program([(filename, text) for filename, text in edited(edits).items()])
texts = dict(apply_edits(moved.sources, moved.reset_positions([Node(('source',)), count, Node(('counter',))])))
checks.assertNotIn('Diagram.Position', texts['lib.wrl'])
checks.assertNotIn('Diagram.Position(X = 10.4', texts['main.wrl'])
checks.assertIn('#![Diagram.Position(X = 300, Y = 0)]', texts['main.wrl'])

# Deleting a node and a connection to it removes each once.
texts = edited(program.delete([Connection((), 'counter', 'printer'), Node(('printer',))]))
checks.assertNotIn('printer', texts['main.wrl'])

# New nodes get a fresh snake_case name and their position.
checks.assertEqual((node_name('Std.DebugValuePrinter'), node_name('Memory.ToggleFlipFlopTrigger'),
                    node_name('Std.Constant[int32]')),
                   ('debug_value_printer', 'toggle_flip_flop_trigger', 'constant'))
texts = edited(program.add_node((), 'Std.DebugValuePrinter', (1, 2)))
checks.assertIn('node debug_value_printer: Std.DebugValuePrinter {\n  #![Diagram.Position(X = 1, Y = 2)]\n}',
                texts['main.wrl'])

checks.assertFalse(program.can_connect((), 'source', 'printer').allowed)
texts = edited(program.connect((), 'counter', 'counter.Step'))
checks.assertIn('connect counter -> counter.Step;', texts['main.wrl'])
texts = edited(program.set_parameter(Node(('printer',)), 'Type', ''))
checks.assertIn('node printer: Std.DebugValuePrinter { }', texts['main.wrl'])

# Renaming a node type renames paths inside it.
texts = edited(program.rename(NodeType(('Counter',)), 'Tally'))
checks.assertIn('node counter: Tally {}', texts['main.wrl'])
checks.assertEqual(renamed_path(('Counter', 'Inner'), NodeType(('Counter',)), 'Tally'), ('Tally', 'Inner'))
checks.assertEqual(renamed_path((), NodeType(('Counter',)), 'Tally'), ())

# Text positions map to the innermost element and back.
offset = library.index('old + step')
checks.assertEqual(program.element_at('lib.wrl', offset), (('Counter',), add))
checks.assertEqual(program.span_of(add)[0], 'lib.wrl')
offset = main.index('-> printer')
checks.assertEqual(program.element_at('main.wrl', offset), ((), Connection((), 'counter', 'printer')))

# The layout places items in columns from left to right, ignoring cycles, with
# body inputs first and outputs last; stored positions are kept, and only an
# unplaced item overlapping a placed one moves.
items = [LayoutItem(name, 6, 3, column='last' if name == 'out' else None) for name in ('a', 'b', 'c', 'out')]
edges = [('a', 'b'), ('b', 'c'), ('c', 'b')]
positions = layout(items, edges)
checks.assertEqual(positions, layout(items, edges))
checks.assertEqual([positions[name][0] for name in ('a', 'b', 'c', 'out')],
                   [0, 6 + COLUMN_GAP, 2 * (6 + COLUMN_GAP), 3 * (6 + COLUMN_GAP)])
items[0] = LayoutItem('a', 6, 3, position=(2 * (6 + COLUMN_GAP), 1))
placed = layout(items, edges)
checks.assertEqual(placed['a'], (2 * (6 + COLUMN_GAP), 1))
checks.assertEqual(placed['b'], positions['b'])
checks.assertGreater(placed['c'][1], 1 + 3)
# A placed item takes its reserved size in the columns, so resizing it does
# not move automatic items, but they still avoid its actual size.
for width in (6, 7.5, 20):
    items[0] = LayoutItem('a', width, 3, position=(0, 20), reserved=(6, 3))
    checks.assertEqual(layout(items, edges)['b'], positions['b'])
items[0] = LayoutItem('a', 20, 3, position=(0, 0), reserved=(6, 3))
checks.assertGreater(layout(items, edges)['b'][1], 3)


# Items rank in source order, unresolved ones last.
ordered = graph_model.build(Program([('main.wrl', '''state s: int32 = 0;
node c: Std.Constant { Type: int32, Value: 1 }
node p: Std.DebugValuePrinter { Type: int32 }
connect c -> missing;
''')]), ())
checks.assertEqual([(item.title, item.order) for item in ordered.items],
                   [('c', 1), ('p', 2), ('s', 0), ('missing', 3)])

# Diagram geometry: port rows, value rows, and sizes in grid units.
layout_source = '''node type Device {
  #![Diagram.DefaultWidth = 10]
  #[Diagram.ShowValue]
  parameter Address: String;
  #[Diagram.ShowValue]
  parameter Port: uint16 = 3671;
  #[Diagram.ShowValue]
  optional parameter Label: String;
  parameter Hidden: boolean = false;
  default input: value boolean;
  input A: value boolean;
  #[Diagram.Row = 3]
  input B: value boolean;
  input C: value boolean;
  #[Diagram.Row = 1]
  output Out: value boolean;
  output Next: value boolean;
  connect default input -> Out;
  connect A -> Next;
}
node first: Device { #![Diagram.Height = 4] Address: "1/2/3" }
#[Diagram.Width = 6.5]
node second: Device { #![Diagram.Height = 20] Label: "x" }
node plain: Std.And {}
state count: int32 = 0;
'''
items = {item.title: item for item in graph_model.build(Program([('main.wrl', layout_source)]), ()).items}
first, second = items['first'], items['second']
checks.assertEqual([(socket.key, socket.row) for socket in first.inputs],
                   [(None, None), ('A', 0), ('B', 3), ('C', 1)])
checks.assertEqual([(socket.key, socket.row) for socket in first.outputs], [('Out', 1), ('Next', 0)])
# Unbound values still take their rows.
checks.assertEqual(first.details, (('Address', '"1/2/3"', 'bound'), ('Port', '3671', 'default'),
                                   ('Label', None, 'absent')))
checks.assertEqual(second.details[0], ('Address', None, 'missing'))
# Std.And's input is unordered; other multi inputs keep their order.
checks.assertEqual([(socket.is_list, socket.unordered) for socket in items['plain'].inputs], [(True, True)])
checks.assertEqual([socket.unordered for socket in first.inputs], [False] * 4)
checks.assertEqual((graph_model.port_rows(first), graph_model.minimum_height(first)), (4, 9))
# A requested height below the minimum is raised to it.
checks.assertEqual([(item.width, graph_model.item_height(item)) for item in (first, second)],
                   [(10, 9), (6.5, 20)])
checks.assertEqual([(item.width, item.height, graph_model.item_height(item))
                    for item in (items['plain'], items['count'])], [(8, None, 2), (8, None, 4)])

# Fractional sizes round up, stored positions remain exact, and automatic
# positions avoid overlaps on the grid. Every coordinate remains a float.
placed = layout([LayoutItem('fixed', 6.2, 3.1, position=(-0.4, -0.6)),
                 LayoutItem('auto', 6.2, 3.1)], [])
checks.assertEqual(placed, {'fixed': (-0.4, -0.6), 'auto': (0.0, 6.0)})
checks.assertTrue(all(type(value) is float for position in placed.values() for value in position))
texts = edited(program.add_declaration((), 'state extra: boolean = false;', (-2.5, 3.5)))
checks.assertIn('#[Diagram.Position(X = -2.5, Y = 3.5)]', texts['main.wrl'])


async def run_test():
    pass
