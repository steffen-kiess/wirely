import contextlib
import io
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from wirely.compiler import compile_strs
from wirely.compiler import ast
from wirely.compiler.attributes import (
    ATTRIBUTE_REGISTRY, AttributeDefinition, Metadata, MetadataLiteral, ParsedFile,
)
from wirely.compiler.parser import parse_str
from wirely.test import negative_test_cases


engine.step()
engine.assert_value_is('sink', True)
graph = engine.program.graph
assert graph.blocks['source'].position == (120, -40.5)
assert graph.blocks['sink'].position is None
assert graph.connections[0].source is graph.blocks['source'].block_outputs_byname['']
assert graph.connections[0].target is graph.blocks['sink'].block_inputs_byname[''].members[0]
try:
    graph.blocks['source'].position = (0, 0)
except AttributeError:
    pass
else:
    raise AssertionError('position must be immutable')
try:
    graph.blocks['source'].attributes[0].value = (0, 0)
except FrozenInstanceError:
    pass
else:
    raise AssertionError('attribute metadata must be immutable')


def parse(source):
    return parse_str(source, filename='<attributes>')


def compile_source(source, *, add_std=False):
    result = compile_strs([('<attributes>', source)], add_std=add_std)
    assert result is not None, source
    return result


parsed = parse('''
#![file_tag]
#[outer] namespace One.Two {
  #![inner]
  #[outer] node type T {
    #![inner]
    #[tag] parameter P: ValueType;
    #[tag] optional parameter Q: uint8;
    #[tag] parameter R: uint8 = 1;
    #[tag] implicit input: value boolean;
    #[tag] output Out: resource Handle;
    #[tag] state saved: uint8 = 0;
    #[tag] temporary tmp: boolean;
    #[tag] native procedure run depends on saved.old produces saved;
    #[tag] native initializer;
    #[tag] node child: T;
    #[tag] connect #[source] child -> Out;
  }
  #[outer] struct type S {
    #![inner]
    #[field] item: uint8,
  }
  #[outer] enum type E: uint8 {
    #![inner]
    #[member] First = 0,
  }
  #[tag] resource type Handle;
}
''')
assert isinstance(parsed, list) and isinstance(parsed, ParsedFile)
assert parsed.inner_attributes[0].name == 'file_tag'
one = parsed[0]
two = one.declarations[0]
assert not one.outer_attributes and not one.inner_attributes
assert two.outer_attributes[0].name == 'outer'
assert two.inner_attributes[0].name == 'inner'
node_type, struct_type, enum_type, resource_type = two.declarations
for owner in (node_type, struct_type, enum_type):
    assert owner.outer_attributes[0].name == 'outer'
    assert owner.inner_attributes[0].name == 'inner'
for declaration in node_type.declarations:
    assert declaration.outer_attributes[0].name == 'tag'
    assert not declaration.inner_attributes
assert struct_type.fields[0].outer_attributes[0].name == 'field'
assert enum_type.members[0].outer_attributes[0].name == 'member'
assert not struct_type.fields[0].inner_attributes
assert not enum_type.members[0].inner_attributes
assert resource_type.outer_attributes[0].name == 'tag'
assert node_type.declarations[-1].sources[0].outer_attributes[0].name == 'source'
assert two.outer_attributes[0].source_position.start_line == 3
assert two.inner_attributes[0].source_position.start_line == 4
assert two.outer_attributes[0].source_position.filename == '<attributes>'

attribute = parse('''// comment
#[tool.label(flag, key = "value", nested(path.flag, -2, +3.5, true,),)]
node n: T;
''')[0].outer_attributes[0]
assert attribute.name == 'tool.label'
assert attribute.source_position.start_line == 2
assert attribute.source_position.start_pos == len('// comment\n')
flag, key, nested = attribute.metadata.value
assert flag.kind == 'flag' and flag.name == 'flag'
assert key.value.value == 'value'
assert nested.value[0].path == ('path', 'flag')
assert [literal.value for literal in nested.value[1:]] == [-2, 3.5, True]
assert isinstance(nested.value[1], MetadataLiteral)
assert isinstance(nested, Metadata)
assert parse('#![only]').inner_attributes[0].name == 'only'
# An attribute-only preamble must become its own negative test case.
cases = list(negative_test_cases('#![only]\n// TEST\n#[unknown] node n:T; // E: Unknown\n'))
assert len(cases) == 2 and '#![only]' in cases[0].source

all_targets = frozenset({
    'file', 'namespace', 'node_type', 'struct', 'struct_field', 'enum',
    'enum_member', 'resource_type', 'node', 'connector', 'parameter',
    'procedure', 'initializer', 'state', 'temporary', 'connection',
})
marker = AttributeDefinition(all_targets, lambda attribute: attribute.metadata, repeatable=True)
unique = AttributeDefinition(all_targets, lambda attribute: None)
with patch.dict(ATTRIBUTE_REGISTRY, {'mark': marker, 'tool.mark': marker, 'unique': unique}):
    compiled = compile_source('''
#![mark]
#[mark] namespace A.B {
  #![mark]
  #[mark] struct type S { #![mark] #[mark] f: uint8, }
  #[mark] enum type E: uint8 { #![mark] #[mark] First = 0, }
  #[mark] resource type R;
  #[mark] native node type T {
    #![mark]
    #[mark] parameter P: ValueType;
    #[mark] optional parameter Q: uint8;
    #[mark] parameter V: uint8 = 0;
    #[mark] implicit input: value boolean;
    #[mark] output Out: value boolean;
    #[mark] state s: uint8 = 0;
    #[mark] temporary t: boolean;
    #[mark] native procedure run depends on implicit input as "input" produces Out;
    #[mark] native initializer;
  }
}
#[tool.mark] namespace A.B { #![mark] #[mark] node type Other {} }
#[mark] node n: A.B.Other;
node plain: A.B.Other;
''')
    scope = compiled.libraries['A'].libraries['B']
    assert len(scope.attributes) == 4
    assert [attribute.style for attribute in scope.attributes] == ['outer', 'inner', 'outer', 'inner']
    assert len(scope.outer_attributes) == len(scope.inner_attributes) == 2
    assert len(compiled.file_metadata) == 1
    assert len(compiled.file_metadata[0].attributes) == 1
    owner = scope.block_type_interfaces['T']
    assert len(owner.attributes) == 2
    for declaration in owner.declarations:
        assert len(declaration.attributes) == 1
    assert owner.block_inputs['implicit input'].attributes == owner.declarations[3].attributes
    assert owner.block_outputs['Out'].attributes == owner.declarations[4].attributes
    assert owner.block_state_variables['s'].attributes == owner.declarations[5].attributes
    assert owner.block_temporary_variables['t'].attributes == owner.declarations[6].attributes
    assert len(scope.struct_types['S'].attributes) == 2
    assert len(scope.struct_types['S'].fields[0].attributes) == 1
    assert len(scope.enum_types['E'].declaration.attributes) == 2
    assert compiled.blocks['n'].attributes[0].name == 'mark'
    assert compiled.blocks['n'].position is None
    assert not compiled.blocks['plain'].attributes
    assert scope.block_type_interfaces['Other'].attributes

    files = compile_strs([('one', '#![unique]'), ('two', '#![unique] node type T {}')])
    assert files is not None
    assert [file.filename for file in files.file_metadata] == ['one', 'two']
    assert all(len(file.attributes) == 1 for file in files.file_metadata)
    assert not files.attributes and not files.block_type_interfaces['T'].attributes

    edges = compile_source('''
node a: Std.Constant(Type = boolean, Value = true);
node b: Std.Constant(Type = boolean, Value = false);
node sink: Std.And;
#[mark(shared)] connect #[mark(first)] a, #[mark(second)] b -> sink;
node type Stateful {
  state saved: boolean = false;
  implicit output: value boolean;
  #[mark(r#state)] connect #[mark(source)] saved.old -> implicit output;
}
node type Fresh {
  state saved: boolean = false;
  implicit output: value boolean;
  native procedure write produces saved;
  #[mark(fresh)] connect saved.new -> implicit output;
}
resource type Handle;
node type Provider { implicit output: resource Handle; }
node type Consumer { implicit input: list resource Handle; }
node p: Provider;
node q: Provider;
node consumer: Consumer;
#[mark(r#resource)] connect #[mark(p)] p, #[mark(q)] q -> consumer;
''', add_std=True)
    assert [edge.kind for edge in edges.connections] == ['data', 'data', 'resource', 'resource']
    for index, edge in enumerate(edges.connections):
        assert edge.target.connected_source is edge.source
        assert len(edge.attributes) == 2
        first, second = (attribute.value.value[0].name for attribute in edge.attributes)
        assert first == ('shared' if index < 2 else 'resource')
        assert second == ['first', 'second', 'p', 'q'][index]
        owner = edges.blocks['sink' if index < 2 else 'consumer']
        assert edge.target is owner.block_inputs_byname[''].members[index % 2]
    for name, phase in [('Stateful', 'old'), ('Fresh', 'new')]:
        state_edge = edges.block_type_interfaces[name].connections[0]
        assert state_edge.kind == 'state' and state_edge.source.phase == phase
        assert state_edge.target is edges.block_type_interfaces[name].block_outputs['implicit output']
        assert state_edge.attributes[0].value.value[0].name == ('state' if phase == 'old' else 'fresh')
    assert len(edges.block_type_interfaces['Stateful'].connections[0].attributes) == 2

    for source in [
        '#[unique] namespace A {} namespace A { #![unique] }',
        'namespace A.B { #![unique] } namespace A { #[unique] namespace B {} }',
        'node a:Std.And; #[unique] connect #[unique] a -> a;',
        '#[unique] node type T { #![unique] }',
    ]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert compile_strs([('duplicate', source)]) is None
        assert 'Duplicate attribute unique' in output.getvalue(), output.getvalue()
        assert 'Internal compiler error' not in output.getvalue()

# A shared non-repeatable attribute can be used by multiple distinct edges.
with patch.dict(ATTRIBUTE_REGISTRY, {'unique': unique}):
    compiled = compile_source('''
node a: Std.Constant(Type = boolean, Value = true);
node b: Std.Constant(Type = boolean, Value = false);
node sink: Std.And;
#[unique] connect a, b -> sink;
''', add_std=True)
    assert all(len(edge.attributes) == 1 for edge in compiled.connections)

# Position is validated before resolving an otherwise unknown node type.
output = io.StringIO()
with contextlib.redirect_stdout(output):
    assert compile_strs([('early', '#[position(x = true, y = 0)] node n: Missing;')]) is None
assert 'finite numeric literal' in output.getvalue()
for literal, expected in [
    ('-0x2a', -42), ('+0o52', 42), ('- 1.5', -1.5), ('+ 2.5', 2.5),
    ('1e2', 100.0), ('010', 10), ('080', 80), ('0009', 9),
    ('-080', -80), ('+010', 10),
]:
    compiled = compile_source(f'node type T {{}} #[position(x = {literal}, y = 0)] node n: T;')
    assert compiled.blocks['n'].position == (expected, 0)

# List slots retain order across statements, and source-only attributes do not
# conflict with the same non-repeatable attribute on a different edge.
with patch.dict(ATTRIBUTE_REGISTRY, {'unique': unique}):
    compiled = compile_source('''
node a: Std.Constant(Type = boolean, Value = true);
node sink: Std.And;
connect #[unique] a, #[unique] a -> sink;
connect a -> sink;
''', add_std=True)
    edges = compiled.connections
    assert [len(edge.attributes) for edge in edges] == [1, 1, 0]
    slots = compiled.blocks['sink'].block_inputs_byname[''].members
    assert [edge.target for edge in edges] == slots
    assert edges[0].attributes[0].source_position.start_pos != edges[1].attributes[0].source_position.start_pos

# Even if a registry extension permits position on types, instances use only
# their own attributes.
position = ATTRIBUTE_REGISTRY['position']
with patch.dict(ATTRIBUTE_REGISTRY, {
    'position': AttributeDefinition(frozenset({'node', 'node_type'}), position.validate),
}):
    compiled = compile_source('''
#[position(x = 1, y = 2)] node type T {}
node n: T;
''')
    assert compiled.block_type_interfaces['T'].attributes[0].value == (1, 2)
    assert compiled.blocks['n'].position is None

boolean_source = '#[mark(key =\n true)] node n:T;'
boolean = parse(boolean_source)[0].outer_attributes[0].metadata.value[0].value
assert boolean.source_position.start_line == 2
assert boolean.source_position.start_pos == boolean_source.index('true')

with patch.dict(ATTRIBUTE_REGISTRY, {'mark': marker, 'unique': unique}):
    compiled = compile_strs([
        ('first', '#[mark] namespace A.B { #![mark] }'),
        ('second', 'namespace A { #[mark] namespace B { #![mark] } }'),
    ])
    assert compiled is not None
    attributes = compiled.libraries['A'].libraries['B'].attributes
    assert [attribute.source_position.filename for attribute in attributes] == [
        'first', 'first', 'second', 'second',
    ]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert compile_strs([
            ('first', '#[unique] namespace A.B {}'),
            ('second', 'namespace A { namespace B { #![unique] } }'),
        ]) is None
    assert 'Error in second:1: Duplicate attribute unique' in output.getvalue()
