import os
from types import MappingProxyType
from unittest import TestCase

from wirely import test as runner
from wirely.compiler import _standard_library_sources, model
from wirely.compiler import outline as outline_module
from wirely.compiler.analysis import ResolvedParameter, ResolvedPort, analyze
from wirely.compiler.attributes import validate_files
from wirely.compiler.coordinator import CompilerCoordinator
from wirely.compiler.diagnostics import error_diagnostics
from wirely.compiler.native_functions import validate_collisions
from wirely.compiler.outline import Constant, Node, NodeType, Parameter, Port, State, Temporary


checks = TestCase()
boolean, uint8, int32 = (model.types.type_by_name[name] for name in ('boolean', 'uint8', 'int32'))

# Unfinished programs resolve: connections and sources are not checked.
source = '''node type Box {
  parameter Type: ValueType;
  constant Zero: int32 = 0;
  input value: value Type;
  default output: value Type;
  state last: Type = Zero;
  temporary current: Type;
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
checks.assertIsInstance(result.value_type(Temporary(('Box', 'current'))), model.TypeParameter)
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


def strict_interface_error(sources):
    """Return the first error of the interface stages when stopping at errors."""
    files, parsed_files = outline_module.parse(sources)
    if any(file.error is not None for file in files):
        return None
    declarations = [declaration for _, parsed in parsed_files for declaration in parsed]
    position = model.SourcePosition(filename='<global>', meta=None, input_data='')
    global_node_type = model.NodeType([], None, declarations, source_position=position)
    global_node_type.services = MappingProxyType({})
    coordinator = CompilerCoordinator(global_node_type)
    try:
        global_node_type.file_metadata = validate_files(parsed_files)
        validate_collisions(declarations)
        coordinator.compile_interfaces()
    except model.ProgramError as error:
        return next(error_diagnostics(error))
    return None


# Every negative fixture is analyzed without internal errors, including the error stopping compilation.
for filename in runner.negative_tests:
    with open(filename, encoding='utf-8') as file:
        text = file.read()
    for case in runner.negative_test_cases(text):
        sources = _standard_library_sources(case.libraries) + [(os.path.abspath(filename), case.source)]
        diagnostics = [item.diagnostic for item in analyze(sources).diagnostics]
        label = f'{filename}: {case.label}'
        checks.assertFalse([item for item in diagnostics if item.kind == 'internal_error'], label)
        strict = strict_interface_error(sources)
        if strict is not None:
            checks.assertIn((strict.filename, strict.line, strict.message),
                            [(item.filename, item.line, item.message) for item in diagnostics], label)
