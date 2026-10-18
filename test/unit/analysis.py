import os
from types import MappingProxyType
from unittest import TestCase

from wirely import test as runner
from wirely.compiler import model, standard_library_texts
from wirely.compiler import outline as outline_module
from wirely.compiler.analysis import ConnectionCheck, ResolvedParameter, ResolvedPort, analyze
from wirely.compiler.attributes import validate_files
from wirely.compiler.coordinator import CompilerCoordinator
from wirely.compiler.diagnostics import WarningSink, error_diagnostics
from wirely.compiler.native_functions import validate_collisions
from wirely.compiler.outline import (
    Connection, Constant, Node, NodeType, Parameter, Port, Procedure, ProcedurePort, State, Binding,
)


checks = TestCase()
boolean, uint8, int32 = (model.types.type_by_name[name] for name in ('boolean', 'uint8', 'int32'))

# Unfinished programs resolve: connections and sources are not checked.
source = '''node type Box {
  parameter Type: ValueType;
  constant Zero: int32 = 0;
  input value: value Type;
  default output: value Type;
  state last: Type = Zero;
  binding current: value Type;
}
node constant: Std.Constant { Type: boolean, Value: true }
node box: Box { Type: uint8 }
node printer: Std.DebugValuePrinter { Type: uint8 }
'''
result = analyze([('main.wrl', source)], add_std=True)
checks.assertEqual(result.diagnostics, ())
checks.assertEqual(result.ports(Node(('constant',))),
                   (ResolvedPort(None, 'output', 'value', False, boolean, None),))
checks.assertEqual([port.type for port in result.ports(Node(('box',)))], [uint8, uint8])
checks.assertEqual(result.parameters(Node(('constant',))), (
    ResolvedParameter('Type', model.types.ValueType, False, True),
    ResolvedParameter('Value', boolean, False, True)))
declared = result.ports(NodeType(('Box',)))
checks.assertEqual([(port.name, port.direction) for port in declared], [('value', 'input'), (None, 'output')])
checks.assertIsInstance(declared[0].type, model.TypeParameter)
checks.assertEqual(result.value_type(Constant(('Box', 'Zero'))), int32)
checks.assertIsInstance(result.value_type(State(('Box', 'last'))), model.TypeParameter)
checks.assertIsInstance(result.value_type(Binding(('Box', 'current'))), model.TypeParameter)
checks.assertIsInstance(result.value_type(Port(('Box', 'value'), 'input')), model.TypeParameter)
checks.assertEqual(result.value_type(Parameter(('Box', 'Type'))), model.types.ValueType)

# Errors are reported at their elements, and other elements keep their types.
source = '''node type Box {
  parameter Type: ValueType;
  default output: value Type;
}
node good: Box { Type: uint8 }
node unknown: Nope {}
node bad: Std.Constant { Type: boolean, Value: 3, Extra: 1 }
node missing: Box {}
node type A { node b: B {} }
node type B { node a: A {} }
'''
result = analyze([('main.wrl', source)], add_std=True)
errors = [(item.element, source[item.diagnostic.start:item.diagnostic.end], item.diagnostic.message)
          for item in result.diagnostics if item.diagnostic.kind == 'error']
checks.assertEqual(errors, [
    (Node(('unknown',)), 'Nope', 'Cannot find node type Nope'),
    (Node(('bad',)), 'Extra', 'Unknown parameter for Constant: Extra'),
    (Node(('bad',)), '3', 'Invalid boolean value 3'),
    (Node(('B', 'a')), 'node a: A {}', "Recursive node type instantiation of 'A'"),
    (Node(('missing',)), 'node missing: Box {}', "Missing value for parameter 'Type' of node 'missing'"),
])
checks.assertEqual([port.type for port in result.ports(Node(('good',)))], [uint8])
checks.assertIsNone(result.ports(Node(('unknown',))))
checks.assertEqual([port.type for port in result.ports(Node(('bad',)))], [boolean])
checks.assertEqual([parameter.bound for parameter in result.parameters(Node(('bad',)))], [True, False])
checks.assertEqual([port.type for port in result.ports(Node(('missing',)))], [None])

# Declaration errors and syntax errors only affect their own declarations or files.
result = analyze([('a.wrl', 'node type T { default output: value Missing; }\nnode t: T {}\n'
                            'node type T {}\nnode u: Std.Constant { Type: uint8, Value: 1 }\n'),
                  ('b.wrl', 'node broken')], add_std=True)
messages = [(item.diagnostic.kind, item.diagnostic.filename, item.element, item.diagnostic.message)
            for item in result.diagnostics if item.diagnostic.kind != 'hint']
checks.assertEqual(messages[0][:3], ('syntax_error', 'b.wrl', None))
checks.assertEqual(messages[1:], [
    ('error', 'a.wrl', NodeType(('T',)), 'Duplicate node type T'),
    ('error', 'a.wrl', Port(('T', 'default output'), 'output'), 'Cannot find type Missing'),
])
checks.assertEqual([port.type for port in result.ports(Node(('u',)))], [uint8])


# Connection checks follow the compiler's rules and messages.
source = '''namespace Lib {
  struct type Point { x: int32, }
  #[Internal] struct type Secret { x: int32, }
  #[Internal] node type Hidden {}
  node type Gate {
    input enable: event boolean;
    default output: value boolean;
  }
}
open enum type Mode: uint8 { Off = 0, }
node type Box {
  parameter Type: ValueType;
  constant Zero: int32 = 0;
  input value: value Type;
  input many: multi value boolean;
  default output: value Type;
  state last: Type = Zero;
  binding current: value Type;
  node type Inner {}
  procedure run depends on value as input, typed: value int32 produces current { current = input; }
  connect Zero -> run.typed;
  connect current -> default output;
}
node flag: Std.Constant { Type: boolean, Value: true }
node number: Std.Constant { Type: uint8, Value: 1 }
node box: Box { Type: uint8 }
node printer: Std.DebugValuePrinter { Type: uint8 }
node both: Std.And {}
node gate: Lib.Gate {}
node broken: Missing {}
connect number -> box.value;
'''
result = analyze([('main.wrl', source)], add_std=True)
allowed = ConnectionCheck(True, None)
for scope, source_text, target_text, expected in (
        ((), 'number', 'printer', allowed),
        ((), 'flag', 'both', allowed),
        ((), 'flag', 'box.many', allowed),
        (('Box',), 'last.new', 'last', allowed),
        ((), 'flag', 'printer', ConnectionCheck(False, 'source has type boolean but target has type uint8')),
        ((), 'flag', 'gate.enable', ConnectionCheck(
            False, 'source has kind ConnectionKind.Value but target has kind ConnectionKind.Event')),
        ((), 'flag', 'box.missing', ConnectionCheck(False, 'Unable to find target input port box.missing')),
        ((), 'printer', 'flag', ConnectionCheck(False, "Source node 'printer' does not have a default output")),
        ((), 'number', 'box.value', ConnectionCheck(
            False, "Input 'value' of node 'box' already has a source; only multi inputs accept several")),
        (('Box',), 'last.new', 'default output', ConnectionCheck(False, 'Default output already has a source')),
        (('Box',), 'Zero', 'run.typed', ConnectionCheck(False, "Port 'typed' of procedure 'run' already has a source")),
        (('Box',), 'value', 'run.input', ConnectionCheck(False, "Port 'input' of procedure 'run' is not a typed port")),
        ((), 'broken', 'printer', ConnectionCheck(False, "Unable to find source node 'broken'"))):
    checks.assertEqual(result.can_connect(scope, source_text, target_text), expected, (source_text, target_text))
checks.assertEqual(result.can_connect((), 'number', 'box.value', replacing=Connection((), 'number', 'box.value')),
                   allowed)
with checks.assertRaises(ValueError):
    result.can_connect((), 'a, b', 'c')
checks.assertEqual(result.connection_candidates((), source='flag'), ('box.many', 'both'))
checks.assertEqual(result.connection_candidates((), target='printer'), ('number', 'box'))
checks.assertEqual(result.connection_candidates(('Box',), target='last'),
                   ('value', 'last.old', 'last.new', 'current'))
checks.assertEqual(result.value_type(ProcedurePort(Procedure(('Box', 'run')), 'input', 'input')),
                   result.value_type(Port(('Box', 'value'), 'input')))
checks.assertEqual(result.value_type(ProcedurePort(Procedure(('Box', 'run')), 'typed', 'input')), int32)

# The palette lists instantiable node types as they are written in each scope.
entries = {entry.type_text: entry for entry in result.node_types()}
constant = entries['Std.Constant']
checks.assertEqual([(parameter.name, parameter.type_text, parameter.optional) for parameter in constant.parameters],
                   [('Type', 'ValueType', False), ('Value', 'Type', False)])
checks.assertEqual([(port.name, port.direction, port.kind) for port in constant.ports], [(None, 'output', 'value')])
checks.assertEqual(entries['Lib.Gate'].id, NodeType(('Lib', 'Gate')))
checks.assertIn('Box', entries)
checks.assertNotIn('Lib.Hidden', entries)
checks.assertNotIn('Inner', entries)
checks.assertIn('Lib.Hidden', [entry.type_text for entry in result.node_types(include_internal=True)])
checks.assertIn('Inner', [entry.type_text for entry in result.node_types(('Box',))])

# Type names include built-in, declared, and parameter types visible in a scope.
names = result.type_names(('Box',))
for name in ('boolean', 'uint8', 'Option', 'List', 'Lib.Point', 'Mode', 'Std.EdgeType', 'Type'):
    checks.assertIn(name, names)
for name in ('ValueType', 'Lib.Secret'):
    checks.assertNotIn(name, names)
checks.assertNotIn('Type', result.type_names())


def strict_interface_error(sources):
    """Return the first error of the interface stages when stopping at errors."""
    files, parsed_files = outline_module.parse(sources)
    if any(file.error is not None for file in files):
        return None
    declarations = [declaration for _, parsed in parsed_files for declaration in parsed]
    position = model.SourcePosition(filename='<global>', meta=None, input_data='')
    global_node_type = model.NodeType(model.DeclarationNodeType([], None, declarations, source_position=position), None)
    global_node_type.services = MappingProxyType({})
    warnings = WarningSink(lambda diagnostic: None)
    coordinator = CompilerCoordinator(global_node_type, warnings=warnings)
    try:
        coordinator.metadata = validate_files(parsed_files, warnings=warnings)
        validate_collisions(declarations)
        coordinator.compile_interfaces()
    except model.ProgramError as error:
        return next(error_diagnostics(error))
    return None


# Every negative fixture is analyzed without internal errors, including the error stopping compilation.
for name in runner.negative_tests:
    filename = 'test/' + name + '.wrl'
    with open(filename, encoding='utf-8') as file:
        text = file.read()
    for case in runner.negative_test_cases(text):
        sources = standard_library_texts(case.libraries) + [(os.path.abspath(filename), case.source)]
        diagnostics = [item.diagnostic for item in analyze(sources).diagnostics]
        label = f'{filename}: {case.label}'
        checks.assertFalse([item for item in diagnostics if item.kind == 'internal_error'], label)
        strict = strict_interface_error(sources)
        if strict is not None:
            checks.assertIn((strict.filename, strict.line, strict.message),
                            [(item.filename, item.line, item.message) for item in diagnostics], label)


# Event binding types remain payload types; connection checks retain event kind.
event_bindings = analyze([('events.wrl', """binding event: event uint8;
binding forwarded: event uint8;
binding value: value uint8;
""")])
checks.assertEqual(event_bindings.value_type(Binding(('event',))), uint8)
checks.assertEqual(event_bindings.can_connect((), 'event', 'forwarded'), allowed)
checks.assertEqual(event_bindings.can_connect((), 'value', 'forwarded'), ConnectionCheck(
    False, 'source has kind ConnectionKind.Value but target has kind ConnectionKind.Event'))
checks.assertIn('forwarded', event_bindings.connection_candidates((), source='event'))
checks.assertNotIn('value', event_bindings.connection_candidates((), source='event'))


async def run_test():
    pass
