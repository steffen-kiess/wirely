from unittest import TestCase

from wirely.compiler import compile_strs, parser
from wirely.compiler.editing import (
    TextEdit, add_attribute, add_declaration, apply_edits, connect, disconnect, format_constant,
    format_identifier, format_metadata, format_node, format_parameter, format_port, format_state,
    format_binding, fresh_name, reconnect, remove_attribute, remove_declaration,
    remove_parameter_binding, rename, replace_declaration, set_attribute, set_node_type,
    set_parameter_binding,
)
from wirely.compiler.outline import (
    Connection, Constant, Node, NodeType, Parameter, Port, Procedure, State, Binding, outline,
)


checks = TestCase()
position = format_metadata('Diagram.Position', X=1, Y=-2.5)
checks.assertEqual(position, 'Diagram.Position(X = 1, Y = -2.5)')
checks.assertEqual(format_metadata('Internal'), 'Internal')
checks.assertEqual(format_metadata('flags', on=True, off=False, size=1e20),
                   'flags(on = true, off = false, size = 1e+20)')
with checks.assertRaises(ValueError):
    format_metadata('Diagram.Position', X=float('nan'), Y=0)


def edit(function, text, *arguments, **options):
    """Apply an edit to the single file main.wrl and check that the result parses."""
    sources = [('main.wrl', text)]
    (_, result), = apply_edits(sources, function(sources, *arguments, **options))
    parser.parse_str(result, filename='<result>')
    return result


def check(function, source, *arguments):
    *arguments, expected = arguments
    checks.assertEqual(edit(function, source, *arguments), expected)


# Multi-line bodies keep comments and follow the body's indentation.
check(add_attribute, '''node a: Std.Constant { // comment
    Type: boolean, // type
    Value: true,
}
''', Node(('a',)), position, '''node a: Std.Constant { // comment
    #![Diagram.Position(X = 1, Y = -2.5)]
    Type: boolean, // type
    Value: true,
}
''')
check(add_attribute, '''node a: Std.Constant {
  #![Internal] // after
  Type: boolean,
}
''', Node(('a',)), position, '''node a: Std.Constant {
  #![Internal] // after
  #![Diagram.Position(X = 1, Y = -2.5)]
  Type: boolean,
}
''')
check(add_attribute, '''node a: Std.Constant { Type: boolean,
  Value: true,
}
''', Node(('a',)), 'Internal', '''node a: Std.Constant { #![Internal] Type: boolean,
  Value: true,
}
''')
check(add_attribute, 'node a: T {\n}\n', Node(('a',)), 'Internal',
      'node a: T {\n  #![Internal]\n}\n')

# One-line bodies are expanded using the file's indentation unit.
check(add_attribute, '''namespace A.B {
    node type T {
        node a: Std.Constant { Type: boolean, Value: true }
        node b: U /* type */ {}
    }
}
''', Node(('A', 'B', 'T', 'a')), position, '''namespace A.B {
    node type T {
        node a: Std.Constant {
            #![Diagram.Position(X = 1, Y = -2.5)]
            Type: boolean, Value: true
        }
        node b: U /* type */ {}
    }
}
''')
check(add_attribute, 'node type T {\n\tnode b: U /* type */ {}\n}\n', Node(('T', 'b')), position,
      'node type T {\n\tnode b: U /* type */ {\n\t\t#![Diagram.Position(X = 1, Y = -2.5)]\n\t}\n}\n')
check(add_attribute, 'node a: T { #![Internal] P: 1 }\n', Node(('a',)), position,
      'node a: T {\n  #![Internal]\n  #![Diagram.Position(X = 1, Y = -2.5)]\n  P: 1\n}\n')

# Setting replaces attributes in place and removes duplicates.
check(set_attribute, '''#[Diagram.Position(X = 0, Y = 0)] // outer
node a: T {
  #![Diagram.Position(X = 5, Y = 5)] // inner
}
''', Node(('a',)), position, '''#[Diagram.Position(X = 1, Y = -2.5)] // outer
node a: T {
  // inner
}
''')
check(set_attribute, 'node a: T { #![Diagram.Position(X = 0, Y = 0)] }\n', Node(('a',)), position,
      'node a: T { #![Diagram.Position(X = 1, Y = -2.5)] }\n')
check(set_attribute, 'node a: T {}\n', Node(('a',)), position,
      'node a: T {\n  #![Diagram.Position(X = 1, Y = -2.5)]\n}\n')

# Removal drops lines that only held the attribute.
check(remove_attribute, '''node a: T {
  #![Internal]
  P: 1,
}
''', Node(('a',)), 'Internal', 'node a: T {\n  P: 1,\n}\n')
check(remove_attribute, 'node a: T { #![Internal] }\n', Node(('a',)), 'Internal',
      'node a: T { }\n')
check(remove_attribute, 'connect b -> c; #[Internal]\nnode a: T {}\n', Node(('a',)), 'Internal',
      'connect b -> c;\nnode a: T {}\n')
check(remove_attribute, 'node a: T {}\n', Node(('a',)), 'Internal', 'node a: T {}\n')

# Node types and repeated namespaces.
check(add_attribute, '''namespace N {
  node a: T {}
}
namespace N {
  node type T {
    node b: U {}
  }
}
''', NodeType(('N', 'T')), 'Internal', '''namespace N {
  node a: T {}
}
namespace N {
  node type T {
    #![Internal]
    node b: U {}
  }
}
''')

# Connections use outer attributes for single sources and per-source ones otherwise.
check(add_attribute, 'node type T {\n  connect a.out -> b;\n}\n',
      Connection(('T',), 'a.out', 'b'), 'Internal',
      'node type T {\n  #[Internal]\n  connect a.out -> b;\n}\n')
check(set_attribute, 'connect #[x = 1] a, b -> default output;\n',
      Connection((), 'a', 'default output'), 'x = 2',
      'connect #[x = 2] a, b -> default output;\n')
check(add_attribute, 'connect a, b -> c;\n', Connection((), 'b', 'c'), 'x = 2',
      'connect a, #[x = 2] b -> c;\n')
check(remove_attribute, '#[x]\nconnect #[x] a -> c;\n', Connection((), 'a', 'c'), 'x',
      'connect a -> c;\n')
with checks.assertRaises(ValueError):
    edit(set_attribute, '#[x]\nconnect a, b -> c;\n', Connection((), 'a', 'c'), 'x')
check(add_attribute, 'connect a, a -> c;\n', Connection((), 'a', 'c', 1), 'x',
      'connect a, #[x] a -> c;\n')
with checks.assertRaises(ValueError):
    edit(add_attribute, 'connect a, a -> c;\n', Connection((), 'a', 'c'), 'x')

# Files with semantic errors are edited, but targets and metadata are checked.
check(add_attribute, 'node a: Missing.Type { Unknown: 1 + true }\nconnect a -> nowhere;\n',
      Node(('a',)), position,
      'node a: Missing.Type {\n  #![Diagram.Position(X = 1, Y = -2.5)]\n  Unknown: 1 + true\n}\n'
      'connect a -> nowhere;\n')
for target in (Node(('b',)), NodeType(('a',)), Connection((), 'a', 'b')):
    with checks.assertRaises(ValueError):
        edit(add_attribute, 'node a: T {}\n', target, 'Internal')
with checks.assertRaises(ValueError):
    edit(add_attribute, 'node a: T {}\nnode a: T {}\n', Node(('a',)), 'Internal')
for metadata in ('', 'x = y', 'x] node b: T {} #![y', 'x // ]'):
    with checks.assertRaises(ValueError):
        edit(add_attribute, 'node a: T {}\n', Node(('a',)), metadata)

# Edits apply from the end; insertions at one offset keep their order.
checks.assertEqual(apply_edits([('a', 'abc'), ('b', 'x')], [
    TextEdit('a', 1, 1, '1'), TextEdit('a', 1, 1, '2'), TextEdit('a', 2, 3, ''), TextEdit('b', 0, 1, 'y'),
]), [('a', 'a12b'), ('b', 'y')])
for edits in ([TextEdit('a', 0, 2, ''), TextEdit('a', 1, 3, '')], [TextEdit('c', 0, 0, '')]):
    with checks.assertRaises(ValueError):
        apply_edits([('a', 'abc')], edits)

# Formatters escape keywords and check the resulting declaration.
checks.assertEqual((format_identifier('state'), format_identifier('node')), ('state', 'r#node'))
for name in ('a b', 'r#x', ''):
    with checks.assertRaises(ValueError):
        format_identifier(name)
checks.assertEqual(format_node('n', 'Std.Constant', [('Type', 'int'), ('Value', '1')]),
                   'node n: Std.Constant { Type: int, Value: 1 }')
checks.assertEqual(format_node('node', 'T'), 'node r#node: T {}')
checks.assertEqual(format_parameter('P', 'int', optional=True), 'optional parameter P: int;')
checks.assertEqual(format_port('input', 'in', 'event', 'int', is_list=True), 'input in: multi event int;')
checks.assertEqual(format_port('output', None, 'resource', 'R'), 'default output: resource R;')
for function, arguments in ((format_state, ('s', 'int', '')), (format_port, ('output', 'o', 'value', 'int', True)),
                            (format_parameter, ('P', 'int', '1', True)), (format_port, ('in', 'a', 'value', 'int'))):
    with checks.assertRaises(ValueError):
        function(*arguments)

body = '''node type T {
  parameter P: type;
  input a: value P;
  node x: Std.Constant { Type: P, Value: 1 }
  connect x -> y.in;
}
node n: T { P: int }
connect s1, s2 -> m.multi; // comment
connect s3 -> m.multi;
'''
checks.assertEqual(fresh_name([('main.wrl', body)], ('T',), 'x'), 'x2')
checks.assertEqual(fresh_name([('main.wrl', body)], ('T',), 'z'), 'z')


def check_insert(scope, declaration, expected, source=body, **options):
    checks.assertEqual(edit(add_declaration, source, scope, declaration, **options), expected)


# Declarations follow their own kind, then earlier kinds, then start the body.
# Blank lines set them apart from their neighbours.
check_insert(('T',), format_state('s', 'int', '0'), body.replace(
    '  node x', '\n  state s: int = 0;\n\n  node x'))
check_insert(('T',), format_constant('C', 'int', '1'), body.replace(
    '  input a', '\n  constant C: int = 1;\n\n  input a'))
# Nodes take attributes as inner attributes, other declarations as outer ones.
check_insert((), format_node('k', 'T'), body.replace(
    '\nconnect s1', '\n\nnode k: T {\n  #![Diagram.Position(X = 1, Y = 2)]\n}\n\nconnect s1'),
    attributes=[format_metadata('Diagram.Position', X=1, Y=2)])
check_insert(('T',), format_node('k', 'U', [('P', '1')]),
             'node type T {\n\tnode x: U {}\n\n\tnode k: U {\n\t\t#![Diagram.Position(X = 1, Y = 2)]\n\t\tP: 1\n\t}\n}\n',
             source='node type T {\n\tnode x: U {}\n}\n', attributes=[format_metadata('Diagram.Position', X=1, Y=2)])
check_insert((), format_parameter('Q', 'int'), 'parameter Q: int;\n\n' + body)
check_insert(('T',), format_binding('t', 'value', 'int'), 'node type T {\n  binding t: value int;\n\n  node x: U {}\n}\n',
             source='node type T { node x: U {} }\n')
check_insert(('T',), format_binding('t', 'value', 'int'), 'node type T {\n  #![Internal]\n  binding t: value int;\n}\n',
             source='node type T { #![Internal] }\n')
check_insert(('T',), format_state('c', 'int', '0'),
             'node type T {\n  state a: int = 0; state b: int = 0;\n\n  state c: int = 0;\n}\n',
             source='node type T {\n  state a: int = 0; state b: int = 0;\n}\n')
check_insert((), format_parameter('p', 'int'), '// header\n\nparameter p: int;\n\nnode a: T {}\n',
             source='// header\n\nnode a: T {}\n')
check_insert((), format_parameter('p', 'int'), '// header\nparameter p: int;\n', source='// header')
check_insert(('N',), format_node('b', 'T'), 'namespace N {}\nnamespace N {\n  node b: T {}\n}\n',
             source='namespace N {}\nnamespace N {}\n')

# New connections go directly below their targets.
sources = [('main.wrl', 'node type T {\n}\n')]
for function, arguments, options in (
        (add_declaration, (format_port('input', 'In', 'value', 'int8'),), {'X': -298, 'Y': -85}),
        (add_declaration, (format_port('output', 'Out', 'value', 'int8'),), {'X': 107, 'Y': -58}),
        (add_declaration, (format_state('last', 'int8', '4'),), {'X': -85, 'Y': -162}),
        (connect, ('last.old', 'Out'), None),
        (connect, ('In', 'last'), None)):
    attributes = [format_metadata('Diagram.Position', **options)] if options else []
    sources = apply_edits(sources, function(sources, ('T',), *arguments, attributes=attributes))
checks.assertEqual(sources[0][1], '''node type T {
  #[Diagram.Position(X = -298, Y = -85)]
  input In: value int8;

  #[Diagram.Position(X = 107, Y = -58)]
  output Out: value int8;
  connect last.old -> Out;

  #[Diagram.Position(X = -85, Y = -162)]
  state last: int8 = 4;
  connect In -> last;
}
''')
for declaration in ('connect a -> b;', 'node a: T {} node b: T {}', 'node a'):
    with checks.assertRaises(ValueError):
        edit(add_declaration, body, (), declaration)
with checks.assertRaises(ValueError):
    edit(add_declaration, body, ('Missing',), format_binding('t', 'value', 'int'))


def multi_sources(text, target='m.multi'):
    return [edge.source.text for edge in outline([('main.wrl', text)]).scope.edges
            if edge.target.text == target]


# New connections follow the target's connections or precede its index-th source.
result = edit(connect, body, (), 'q', 'm.multi')
checks.assertEqual(result, body + 'connect q -> m.multi;\n')
checks.assertEqual(multi_sources(result), ['s1', 's2', 's3', 'q'])
for index, expected in ((0, 'connect q, s1, s2'), (1, 'connect s1, q, s2'),
                        (2, 'connect s1, s2 -> m.multi; // comment\nconnect q -> m.multi;\nconnect s3')):
    result = edit(connect, body, (), 'q', 'm.multi', index=index)
    checks.assertIn(expected, result)
    sources = ['s1', 's2', 's3']
    sources.insert(index, 'q')
    checks.assertEqual(multi_sources(result), sources)
checks.assertEqual(edit(connect, body, ('T',), 'a', 'z', attributes=['Internal']), body.replace(
    '  connect x -> y.in;\n', '  connect x -> y.in;\n  #[Internal]\n  connect a -> z;\n'))
checks.assertEqual(edit(connect, 'node type T {\n  input a: value int;\n}\n', ('T',), 'a', 'x'),
                   'node type T {\n  input a: value int;\n\n  connect a -> x;\n}\n')
checks.assertEqual(edit(connect, 'node type T {}\n', ('T',), 'a', 'x'),
                   'node type T {\n  connect a -> x;\n}\n')
for source, target in (('a, b', 'c'), ('#[x] a', 'c'), ('a', 'c;')):
    with checks.assertRaises(ValueError):
        edit(connect, body, (), source, target)

# Global connections go to the file holding the target's connections.
files = [('a.wrl', 'node a: T {}\nconnect a -> b.in;\n'), ('b.wrl', 'node b: T {}\n')]
checks.assertEqual(connect(files, (), 'c', 'b.in'), [TextEdit('a.wrl', 31, 31, '\nconnect c -> b.in;')])
checks.assertEqual(connect(files, (), 'c', 'd'), [TextEdit('b.wrl', 12, 12, '\n\nconnect c -> d;')])
checks.assertEqual(connect(files, (), 'c', 'b'), [TextEdit('b.wrl', 12, 12, '\nconnect c -> b;')])

# Without connections to the target, a connection joins the block directly
# below the target's declaration; declarations added later go below it.
nodes = 'node a: T {}\nnode b: T {}\n\nnode c: T {\n  P: 1,\n}\nconnect x -> c.in;\n'
checks.assertEqual(edit(connect, nodes, (), 'x', 'a'),
                   nodes.replace('node a: T {}\n', 'node a: T {}\nconnect x -> a;\n\n'))
checks.assertEqual(edit(connect, nodes, (), 'y', 'c.other'),
                   nodes + 'connect y -> c.other;\n')
checks.assertEqual(edit(connect, nodes, (), 'y', 'c'), nodes + 'connect y -> c;\n')
checks.assertEqual(edit(connect, nodes, (), 'x', 'b.in'),
                   nodes.replace('node b: T {}\n', 'node b: T {}\nconnect x -> b.in;\n'))
checks.assertEqual(edit(add_declaration, nodes, (), format_node('d', 'T')), nodes + '\nnode d: T {}\n')
# A separated connection is not part of the block.
checks.assertEqual(edit(add_declaration, 'node a: T {}\n\nconnect x -> a;\n', (), format_node('d', 'T')),
                   'node a: T {}\n\nnode d: T {}\n\nconnect x -> a;\n')

# Moving a connection to another target moves it to that target's block.
moves = 'node a: T {}\nconnect x -> a;\nnode b: T {}\nconnect y, z -> b;\n'
checks.assertEqual(edit(reconnect, moves, Connection((), 'x', 'a'), target='b.in'),
                   'node a: T {}\nnode b: T {}\nconnect y, z -> b;\nconnect x -> b.in;\n')
checks.assertEqual(edit(reconnect, moves, Connection((), 'y', 'b'), target='a.in'),
                   'node a: T {}\nconnect x -> a;\nconnect y -> a.in;\n\nnode b: T {}\nconnect z -> b;\n')
checks.assertEqual(edit(reconnect, moves, Connection((), 'x', 'a'), source='w'),
                   moves.replace('connect x', 'connect w'))

# Disconnecting removes a source with its comma, or the whole statement.
checks.assertIn('connect s2 -> m.multi; // comment\n', edit(disconnect, body, Connection((), 's1', 'm.multi')))
checks.assertIn('connect s1 -> m.multi; // comment\n', edit(disconnect, body, Connection((), 's2', 'm.multi')))
checks.assertEqual(edit(disconnect, body, Connection((), 's3', 'm.multi')),
                   body.replace('connect s3 -> m.multi;\n', ''))
checks.assertEqual(edit(disconnect, '#[x]\nconnect a, #[y] b, c -> d;\n', Connection((), 'b', 'd')),
                   '#[x]\nconnect a, c -> d;\n')
checks.assertEqual(edit(disconnect, 'connect a, b, c -> d;\n', Connection((), 'c', 'd')),
                   'connect a, b -> d;\n')

# Reconnecting replaces references or moves a source out of a shared statement.
checks.assertEqual(edit(reconnect, body, Connection((), 's3', 'm.multi'), source='q'),
                   body.replace('connect s3', 'connect q'))
checks.assertEqual(edit(reconnect, body, Connection((), 's3', 'm.multi'), source='q', target='z'),
                   body.replace('connect s3 -> m.multi', 'connect q -> z'))
checks.assertEqual(edit(reconnect, '#[x]\nconnect a, #[y] b -> c;\nconnect d -> e;\n',
                        Connection((), 'b', 'c'), target='e'),
                   '#[x]\nconnect a -> c;\nconnect d -> e;\n#[x]\n#[y]\nconnect b -> e;\n')
checks.assertEqual(edit(reconnect, body, Connection((), 's1', 'm.multi'), target='m.multi'), body)

# Removal cascades to connections in all files, including through instance ports.
files = [
    ('a.wrl', 'node type T {\n  input a: value int;\n  binding t: value int;\n  connect a -> t;\n}\n'
              'node n: T {} connect n -> q;\n'),
    ('b.wrl', 'connect w, v -> n.a;\nconnect n.a -> r;\n'),
]
checks.assertEqual(apply_edits(files, remove_declaration(files, Port(('T', 'a'), 'input'))), [
    ('a.wrl', 'node type T {\n  binding t: value int;\n}\nnode n: T {} connect n -> q;\n'),
    ('b.wrl', 'connect n.a -> r;\n'),
])
checks.assertEqual(apply_edits(files, remove_declaration(files, Node(('n',)))), [
    ('a.wrl', 'node type T {\n  input a: value int;\n  binding t: value int;\n  connect a -> t;\n}\n'),
    ('b.wrl', ''),
])
checks.assertEqual(apply_edits(files, remove_declaration(files, Node(('n',)), cascade=False))[1], files[1])
checks.assertEqual(edit(remove_declaration, '#[Diagram.Position(X = 0, Y = 0)]\nstate s: int = 0;\nconnect s.old -> s;\n',
                        State(('s',))), '')
checks.assertEqual(edit(remove_declaration, '''node type T {
  binding t: value int;
  procedure run produces t { t = 1; }
  procedure other depends on x: value int produces y: value int { y = x; }
  connect t -> other.x;
  connect other.y -> u;
}
''', Procedure(('T', 'other'))), 'node type T {\n  binding t: value int;\n  procedure run produces t { t = 1; }\n}\n')
with checks.assertRaises(ValueError):
    edit(remove_declaration, body, Node(('missing',)))

# Removal leaves no doubled blank line, and none after { or before }.
spaced = '''node type T {
  input a: value int;

  input b: value int;

  binding t: value int;

  connect b -> t;
}
'''
for target, expected in (
        (Port(('T', 'a'), 'input'), spaced.replace('  input a: value int;\n\n', '')),
        (Port(('T', 'b'), 'input'), spaced.replace('  input b: value int;\n\n', '').replace(
            '\n  connect b -> t;\n', '')),
        (Binding(('T', 't')), spaced.replace('  binding t: value int;\n\n', '').replace(
            '\n\n  connect b -> t;', ''))):
    checks.assertEqual(edit(remove_declaration, spaced, target), expected)

# Declarations in node type bodies take outer attributes.
declarations = '''node type T {
  #[Internal]
  state s: int = 0; // state
  input a: value int;
  procedure p produces s { s = 1; }
}
'''
check(set_attribute, declarations, State(('T', 's')), position, declarations.replace(
    '  state s', '  #[Diagram.Position(X = 1, Y = -2.5)]\n  state s'))
check(add_attribute, declarations, Port(('T', 'a'), 'input'), position, declarations.replace(
    '  input a', '  #[Diagram.Position(X = 1, Y = -2.5)]\n  input a'))
check(set_attribute, declarations, Procedure(('T', 'p')), position, declarations.replace(
    '  procedure p', '  #[Diagram.Position(X = 1, Y = -2.5)]\n  procedure p'))
check(remove_attribute, declarations, State(('T', 's')), 'Internal',
      declarations.replace('  #[Internal]\n', ''))

# Parameter bindings follow the body's style.
node = Node(('n',))
for source, expected in (
        ('node n: T {}\n', 'node n: T { B: 2 }\n'),
        ('node n: T { A: 1 }\n', 'node n: T { A: 1, B: 2 }\n'),
        ('node n: T { A: 1, }\n', 'node n: T { A: 1, B: 2, }\n'),
        ('node n: T {\n  A: 1,\n}\n', 'node n: T {\n  A: 1,\n  B: 2,\n}\n'),
        ('node n: T {\n  A: 1 // a\n}\n', 'node n: T {\n  A: 1, // a\n  B: 2\n}\n'),
        ('node n: T {\n}\n', 'node n: T {\n  B: 2,\n}\n'),
        ('node n: T { #![Internal] }\n', 'node n: T {\n  #![Internal]\n  B: 2,\n}\n')):
    check(set_parameter_binding, source, node, 'B', '2', expected)
checks.assertEqual(edit(set_parameter_binding, 'node n: T { A: 1, B: 2, A: 3 }\n', node, 'A', '5', optional=True),
                   'node n: T { A?: 5, B: 2 }\n')
for value in ('2 +', '2 }'):
    with checks.assertRaises(ValueError):
        edit(set_parameter_binding, 'node n: T {}\n', node, 'B', value)
for name, expected in (('A', 'node n: T { B: 2, C: 3 }\n'), ('B', 'node n: T { A: 1, C: 3 }\n'),
                       ('C', 'node n: T { A: 1, B: 2 }\n'), ('Z', 'node n: T { A: 1, B: 2, C: 3 }\n')):
    check(remove_parameter_binding, 'node n: T { A: 1, B: 2, C: 3 }\n', node, name, expected)
check(remove_parameter_binding, 'node n: T { A: 1, A: 2 }\n', node, 'A', 'node n: T { }\n')
check(remove_parameter_binding, 'node n: T {\n  A: 1,\n  B: 2\n}\n', node, 'B', 'node n: T {\n  A: 1,\n}\n')
check(remove_parameter_binding, 'node n: T {\n  A: 1,\n  B: 2,\n  C: 3,\n}\n', node, 'B',
      'node n: T {\n  A: 1,\n  C: 3,\n}\n')

check(set_node_type, 'node n: Old.T { A: 1 }\n', node, 'New.U', 'node n: New.U { A: 1 }\n')
with checks.assertRaises(ValueError):
    edit(set_node_type, 'node n: T {}\n', node, '1')

# Replaced declarations keep their attributes and comments, kind, and name.
check(replace_declaration, declarations, State(('T', 's')), format_state('s', 'uint8', '1'),
      declarations.replace('state s: int = 0;', 'state s: uint8 = 1;'))
check(replace_declaration, declarations, Port(('T', 'a'), 'input'), format_port('input', 'a', 'event', 'int', True),
      declarations.replace('input a: value int;', 'input a: multi event int;'))
check(replace_declaration, 'binding t: value int;\n', Binding(('t',)), format_binding('t', 'value', 'uint8'),
      'binding t: value uint8;\n')
check(replace_declaration, 'constant C: int = 1;\n', Constant(('C',)), format_constant('C', 'int', '2'),
      'constant C: int = 2;\n')
check(replace_declaration, 'parameter P: int;\n', Parameter(('P',)), format_parameter('P', 'int', optional=True),
      'optional parameter P: int;\n')
for target, declaration in ((Port(('T', 'a'), 'input'), 'output a: value int;'),
                            (State(('T', 's')), 'state t: int = 0;'),
                            (State(('T', 's')), 'binding s: value int;'),
                            (State(('T', 's')), '#[Internal] state s: int = 0;'),
                            (State(('T', 's')), 'state s: int ='),
                            (Procedure(('T', 'p')), 'procedure p produces s { s = 2; }')):
    with checks.assertRaises((ValueError, TypeError)):
        edit(replace_declaration, declarations, target, declaration)
with checks.assertRaises(ValueError):
    edit(replace_declaration, 'constant C: int = 1;\n', Constant(('C',)), 'parameter C: int = 1;')

# Renaming updates references; unaliased procedure ports keep the old name as alias.
program = '''node type T {
  parameter P: type;
  constant C: P = { let C = 1; C };
  input a: value P;
  default output: value P;
  state s: P = C;
  binding t: value P;
  node n: Std.Constant { Type: P, Value: C }
  procedure run depends on a, s.old as old, n as value produces t, s { t = a; s = old; }
  native procedure check depends on t uses C, P;
  connect s.new -> default output;
  connect run.t -> t;
  assert optional(P) == P;
}
node x: T { P: uint8 }
connect x.a -> x.a;
'''


def check_rename(target, name, *replacements, source=program):
    expected = source
    for old, new in replacements:
        checks.assertIn(old, expected)
        expected = expected.replace(old, new)
    checks.assertEqual(edit(rename, source, target, name), expected)


check_rename(Node(('T', 'n')), 'm', ('node n:', 'node m:'), ('n as value', 'm as value'))
check_rename(State(('T', 's')), 'r', ('state s:', 'state r:'), ('s.old', 'r.old'), ('produces t, s', 'produces t, r as s'),
             ('connect s.new', 'connect r.new'))
check_rename(Binding(('T', 't')), 'u', ('binding t: value', 'binding u: value'), ('produces t,', 'produces u as t,'),
             ('depends on t uses', 'depends on u as t uses'), ('-> t;', '-> u;'))
check_rename(Port(('T', 'a'), 'input'), 'b', ('input a:', 'input b:'), ('depends on a,', 'depends on b as a,'),
             ('x.a -> x.a', 'x.b -> x.b'))
check_rename(Port(('T', 'Named'), 'output'), 'Other', ('output Named:', 'output Other:'),
             ('produces Named;', 'produces Other as Named;'),
             source='resource type R;\nnative node type T {\n  output Named: resource R;\n'
                    '  native initializer produces Named;\n}\n')
check_rename(Procedure(('T', 'run')), 'go', ('procedure run', 'procedure go'), ('run.t', 'go.t'))
check_rename(Procedure(('T', 'check')), 'verify', ('procedure check', 'procedure verify as "T:check"'))
check_rename(Constant(('T', 'C')), 'D', ('constant C', 'constant D'), ('= C;', '= D;'), ('Value: C', 'Value: D'),
             ('uses C', 'uses D as C'))
check_rename(Parameter(('T', 'P')), 'Q', ('P: type', 'Q: type'), ('C: P', 'C: Q'), ('value P', 'value Q'),
             ('s: P', 's: Q'), ('Type: P', 'Type: Q'),
             (', P;', ', Q as P;'), ('optional(P) == P', 'optional(Q) == Q'), ('{ P:', '{ Q:'))
check_rename(NodeType(('T',)), 'U', ('node type T', 'node type U'), ('x: T', 'x: U'))
checks.assertEqual(edit(rename, program, Node(('x',)), 'x'), program)

# References in other files and namespaces follow.
files = [('a.wrl', 'namespace N { node type T { input a: value int; parameter P: int; } }\nnode n: N.T { P: 1 }\n'),
         ('b.wrl', 'node m: N.T { P: 2 }\nconnect m.a -> n.a;\n')]
checks.assertEqual(apply_edits(files, rename(files, NodeType(('N', 'T')), 'U')), [
    ('a.wrl', 'namespace N { node type U { input a: value int; parameter P: int; } }\nnode n: N.U { P: 1 }\n'),
    ('b.wrl', 'node m: N.U { P: 2 }\nconnect m.a -> n.a;\n')])
checks.assertEqual(apply_edits(files, rename(files, Port(('N', 'T', 'a'), 'input'), 'b'))[1],
                   ('b.wrl', 'node m: N.T { P: 2 }\nconnect m.b -> n.b;\n'))
checks.assertEqual(apply_edits(files, rename(files, Parameter(('N', 'T', 'P')), 'Q')), [
    ('a.wrl', 'namespace N { node type T { input a: value int; parameter Q: int; } }\nnode n: N.T { Q: 1 }\n'),
    ('b.wrl', 'node m: N.T { Q: 2 }\nconnect m.a -> n.a;\n')])

# Renaming is refused where it would break or change references.
for source, target, name in (
        (program, Node(('T', 'n')), 's'),
        (program, Port(('T', 'default output'), 'output'), 'o'),
        (program, Node(('T', 'n')), 'node type'),
        (program, Parameter(('T', 'P')), 'boolean'),
        (program, Constant(('T', 'C')), 'old'),
        ('native node type T {}\n', NodeType(('T',)), 'U'),
        ('node type T {}\nnode type V { node type U {} node v: T {} }\n', NodeType(('T',)), 'U'),
        ('node type T { parameter P: int; constant C: int = { let Q = 1; P + Q }; }\n', Parameter(('T', 'P')), 'Q')):
    with checks.assertRaises(ValueError):
        edit(rename, source, target, name)

# A renamed program compiles as before.
files = [('a.wrl', '''node type Toggle {
  parameter Initial: boolean;
  default output: value boolean;
  state on: boolean = Initial;
  procedure flip depends on on.old as old produces on { on = !old; }
  connect on.new -> default output;
}
node toggle: Toggle { Initial: true }
'''), ('b.wrl', 'node print: Std.DebugValuePrinter { Type: boolean }\nconnect toggle -> print;\n')]
for target, name in ((State(('Toggle', 'on')), 'enabled'), (Parameter(('Toggle', 'Initial')), 'Start'),
                     (Node(('toggle',)), 'switch'), (NodeType(('Toggle',)), 'Switch')):
    files = apply_edits(files, rename(files, target, name))
    checks.assertIsNotNone(compile_strs(files, add_std=True))

# Assignments and nested blocks preserve local shadowing during renames.
block_source = '''constant Flag: boolean = true;
constant Saved: boolean = {
  let mut value = Flag;
  { value = Flag; }
  let Flag = Flag;
  {
    let mut Flag = Flag;
    Flag = false;
  }
  Flag
};
node type Block {
  parameter Type: type;
  parameter Initial: Type;
  default output: value Type;
  procedure run produces default output as output {
    let mut next: Type;
    if true { next = Initial; } else { next = Initial; }
    output = next;
  }
}
'''
check_rename(Constant(('Flag',)), 'Enabled',
             ('constant Flag:', 'constant Enabled:'),
             ('let mut value = Flag;', 'let mut value = Enabled;'),
             ('{ value = Flag; }', '{ value = Enabled; }'),
             ('let Flag = Flag;', 'let Flag = Enabled;'), source=block_source)
check_rename(Parameter(('Block', 'Type')), 'ElementType',
             ('parameter Type:', 'parameter ElementType:'),
             ('Initial: Type', 'Initial: ElementType'),
             ('output: value Type', 'output: value ElementType'),
             ('next: Type', 'next: ElementType'), source=block_source)


# All binding kinds use the same formatting and editing API.
checks.assertEqual(format_binding('binding', 'value', 'uint8'), 'binding binding: value uint8;')
checks.assertEqual(format_binding('service', 'resource', 'Handle'), 'binding service: resource Handle;')
checks.assertEqual(format_binding('event', 'event', 'uint8'), 'binding event: event uint8;')
with checks.assertRaises(ValueError):
    format_binding('event', 'multi event', 'uint8')
resource_source = '''native node type Device {
  #[Internal]
  binding service: resource Handle; // keep
  native initializer produces service;
  native procedure poll depends on service;
}
'''
check_rename(Binding(('Device', 'service')), 'handle',
             ('binding service:', 'binding handle:'),
             ('produces service;', 'produces handle as service;'),
             ('depends on service;', 'depends on handle as service;'), source=resource_source)
check(replace_declaration, resource_source, Binding(('Device', 'service')),
      format_binding('service', 'resource', 'Other'),
      resource_source.replace('resource Handle;', 'resource Other;'))


event_source = """binding signal: event uint8; // keep
procedure produce produces signal {}
procedure consume depends on signal {}
"""
check_rename(Binding(('signal',)), 'renamed',
             ('binding signal:', 'binding renamed:'),
             ('produces signal {}', 'produces renamed as signal {}'),
             ('depends on signal {}', 'depends on renamed as signal {}'), source=event_source)


async def run_test():
    pass
