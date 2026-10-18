from unittest import TestCase

from wirely.compiler.outline import (
    Connection, Constant, Endpoint, Node, NodeType, Parameter, Port, Procedure, ProcedurePort,
    State, Binding, outline,
)


checks = TestCase()

source = '''
node type T {
  parameter P: ValueType;
  constant C: int = 1;
  input a: multi value P;
  input e: event int;
  input r: resource Std.R;
  default output: value P;
  state s: int = 0;
  binding t: value int;

  procedure run
    depends on a as x, s.old as old, typed: value int
    produces default output as out, s.new as new
  {
    out = x;
    new = old;
  }

  connect e, C -> run.typed;
  connect run.out -> t;
}

#[Diagram.Position(X = 1, Y = 2.5)]
node n: T { P: int }
node m: Missing.T { #![Diagram.Position(X = 1)] Q?: 2 }
connect n -> m.in;
connect n -> m.in;
connect elsewhere -> n.a;
'''
result = outline([('main.wrl', source)], add_std=True)

# Node type interfaces are copied to their instances.
node_type, = result.find(NodeType(('T',)))
checks.assertEqual([(port.name, port.kind, port.is_list, port.type_text) for port in node_type.ports], [
    ('a', 'value', True, 'P'), ('e', 'event', False, 'int'), ('r', 'resource', False, 'Std.R'),
    (None, 'value', False, 'P'),
])
checks.assertEqual(result.find(Port(('T', 'default output'), 'output')), (node_type.ports[3],))
n, = result.find(Node(('n',)))
checks.assertEqual((n.node_type, n.ports, n.position), (NodeType(('T',)), node_type.ports, (1, 2.5)))
constant, = [item for namespace in result.scope.namespaces if namespace.path == ('Std',)
             for item in namespace.node_types if item.name == 'Constant']
checks.assertEqual([(port.name, port.type_text) for port in constant.ports], [(None, 'Type')])

# Declarations keep their source text.
parameter, = result.find(Parameter(('T', 'P')))
checks.assertEqual((parameter.type_text, parameter.default_text, parameter.is_optional),
                   ('ValueType', None, False))
checks.assertEqual(result.find(Constant(('T', 'C')))[0].value_text, '1')
state, = result.find(State(('T', 's')))
checks.assertEqual((state.type_text, state.initial_text), ('int', '0'))
checks.assertEqual(result.find(Binding(('T', 't')))[0].type_text, 'int')
procedure, = result.find(Procedure(('T', 'run')))
checks.assertEqual([(port.key, port.reference_text, port.kind) for port in procedure.inputs],
                   [('x', 'a', None), ('old', 's.old', None), ('typed', None, 'value')])
checks.assertEqual(result.find(ProcedurePort(Procedure(('T', 'run')), 'new', 'output')),
                   (procedure.outputs[1],))

# Explicit and reference edges resolve within their scope.
run = Procedure(('T', 'run'))
checks.assertEqual([(edge.kind, edge.source, edge.target) for edge in node_type.scope.edges], [
    ('reference', Endpoint('a', Port(('T', 'a'), 'input'), None), Endpoint('run.x', run, 'x')),
    ('reference', Endpoint('s.old', State(('T', 's')), 'old'), Endpoint('run.old', run, 'old')),
    ('reference', Endpoint('run.out', run, 'out'),
     Endpoint('default output', Port(('T', 'default output'), 'output'), None)),
    ('reference', Endpoint('run.new', run, 'new'), Endpoint('s.new', State(('T', 's')), 'new')),
    ('connect', Endpoint('e', Port(('T', 'e'), 'input'), None), Endpoint('run.typed', run, 'typed')),
    ('connect', Endpoint('C', Constant(('T', 'C')), None), Endpoint('run.typed', run, 'typed')),
    ('connect', Endpoint('run.out', run, 'out'), Endpoint('t', Binding(('T', 't')), None)),
])

# Reference edges carry the span of the reference on its side.
depends, produces = node_type.scope.edges[0], node_type.scope.edges[2]
checks.assertEqual((source[slice(*depends.source_span)], depends.target_span), ('a', None))
checks.assertEqual((produces.source_span, source[slice(*produces.target_span)]), (None, 'default output'))

# Unknown types and names stay unresolved; identical edges are numbered.
m, = result.find(Node(('m',)))
checks.assertEqual((m.node_type, m.ports, m.position), (None, None, None))
checks.assertEqual([(binding.name, binding.value_text, binding.is_optional) for binding in m.parameters],
                   [('Q', '2', True)])
checks.assertEqual([(edge.id, edge.source.element, edge.target.element) for edge in result.scope.edges], [
    (Connection((), 'n', 'm.in', 0), Node(('n',)), Node(('m',))),
    (Connection((), 'n', 'm.in', 1), Node(('n',)), Node(('m',))),
    (Connection((), 'elsewhere', 'n.a'), None, Node(('n',))),
])
checks.assertEqual(len(result.find(Connection((), 'n', 'm.in'))), 2)
checks.assertEqual(result.find(Connection((), 'n', 'm.in', 1)), (result.scope.edges[1],))
checks.assertEqual(result.find(Connection((), 'n', 'm.in', 2)), ())

# Files share the global scope and namespaces; parse errors only skip their file.
result = outline([
    ('a.wrl', 'node a: N.T {}\nconnect a -> b;\nnamespace N { node type T {} }\n'),
    ('broken.wrl', 'node x'),
    ('invalid.wrl', 'node type U { output o: multi value int; }'),
    ('b.wrl', 'node b: N.T {}\nnode b: N.T {}\nnamespace N { node type V {} }\n'),
])
checks.assertEqual([(file.filename, file.error and file.error.kind) for file in result.files], [
    ('a.wrl', None), ('broken.wrl', 'syntax_error'), ('invalid.wrl', 'error'), ('b.wrl', None),
])
checks.assertEqual(result.scope.edges[0].target.element, Node(('b',)))
checks.assertEqual([node.filename for node in result.find(Node(('b',)))], ['b.wrl', 'b.wrl'])
checks.assertEqual(result.find(Node(('a',)))[0].node_type, NodeType(('N', 'T')))
namespace, = result.scope.namespaces
checks.assertEqual([item.name for item in namespace.node_types], ['T', 'V'])
checks.assertEqual(result.find(NodeType(('U',))), ())


# Bindings share one collection and ID type, retaining their connection kind.
bindings = outline([('bindings.wrl', '''binding number: value uint8;
binding service: resource Handle;
binding event: event Option[uint8];
connect source -> number;
connect provider -> service;
''')])
checks.assertEqual([(record.id, record.kind, record.type_text) for record in bindings.scope.bindings], [
    (Binding(('number',)), 'value', 'uint8'),
    (Binding(('service',)), 'resource', 'Handle'),
    (Binding(('event',)), 'event', 'Option[uint8]'),
])
checks.assertEqual(bindings.scope.edges[1].target.element, Binding(('service',)))


async def run_test():
    pass
