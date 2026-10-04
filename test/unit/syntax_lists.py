import datetime

import wirely
from wirely.compiler.parser import parse_str
from wirely.compiler.types import parse_value


engine.step()
engine.assert_value_is('sink', False)
settings = engine.program.instances['settings'].parameters
assert settings['Values'] == (wirely.Some(1), None, wirely.Some(2))
assert settings['Empty'] == ()
graph = engine.program.graph
assert len(graph.connections) == 2
for index, name in enumerate(['first', 'second']):
    edge = graph.connections[index]
    assert edge.source is graph.blocks[name].block_outputs_byname['']
    assert edge.target is graph.blocks['sink'].block_inputs_byname[''].members[index]


def parse(source):
    return parse_str(source, filename='<syntax-lists>')[0]


for arguments, expected in [
    ('', []), ('A: 1', [('A', 1)]), ('A: 1,', [('A', 1)]),
    ('A: 1, B: 2', [('A', 1), ('B', 2)]),
    ('A: 1, B: 2, /* final argument */', [('A', 1), ('B', 2)]),
]:
    declaration = parse(f'node n: T {{ {arguments} }}')
    assert [(argument.name.id_str, argument.value.value)
            for argument in declaration.instantiation.parameters] == expected

option = wirely.types.TypeOption(wirely.types.uint8)
list_type = wirely.types.TypeList(option)
for expression, expected in [
    ('[]', ()), ('[Some(1)]', (wirely.Some(1),)),
    ('[Some(1,),]', (wirely.Some(1),)),
    ('[Some(1), None]', (wirely.Some(1), None)),
    ('[Some(1 /* value */,), None, /* last item */]', (wirely.Some(1), None)),
]:
    value = parse(f'parameter P: List[Option[uint8]] = {expression};').default_value
    assert parse_value(list_type, value) == expected

for expression in ['TimeOfDay.AddWrap("23:30", "01:00")',
                   'TimeOfDay.AddWrap("23:30", "01:00",)']:
    value = parse(f'parameter P: TimeOfDay = {expression};').default_value
    assert graph.get_const_value(value, wirely.types.TimeOfDay).evaluate() == datetime.time(0, 30)
assert parse('parameter P: unit = f();').default_value.parameters == []


def bindings(values):
    return [(str(binding.reference), binding.key) for binding in values]


for comma in ['', ',']:
    procedure = parse(f'''native procedure run
  depends on default input as "input", previous.old as "old"{comma}
  produces Result, default output as "output"{comma};''')
    assert bindings(procedure.depends.dependencies) == [
        ('default input', 'input'), ('previous.old', 'old'),
    ]
    assert bindings(procedure.produces.outputs) == [
        ('Result', 'Result'), ('default output', 'output'),
    ]
    initializer = parse(f'native initializer depends on First, Second as "service"{comma};')
    assert bindings(initializer.depends.dependencies) == [
        ('First', 'First'), ('Second', 'service'),
    ]
    connection = parse(f'connect #[tag] first, #[other] second{comma} -> sink;')
    assert [(str(source), [attribute.name for attribute in source.outer_attributes])
            for source in connection.sources] == [('first', ['tag']), ('second', ['other'])]
    assert str(connection.target) == 'sink'
    single = parse(f'connect first{comma} -> sink;')
    assert [str(source) for source in single.sources] == ['first']
    single_procedure = parse(f'native procedure run depends on Input{comma} produces Result{comma};')
    assert bindings(single_procedure.depends.dependencies) == [('Input', 'Input')]
    assert bindings(single_procedure.produces.outputs) == [('Result', 'Result')]

for clauses in ['', 'depends on', 'produces', 'depends on produces']:
    procedure = parse(f'native procedure run {clauses};')
    assert procedure.depends.dependencies == ()
    assert procedure.produces.outputs == ()
for clause in ['', 'depends on']:
    assert parse(f'native initializer {clause};').depends.dependencies == ()

for comma in ['', ',']:
    metadata = parse(f'#[tag(nested(first, second{comma}){comma})] node n: T {{}}')
    nested = metadata.outer_attributes[0].metadata.value[0]
    assert nested.name == 'nested'
    assert [argument.name for argument in nested.value] == ['first', 'second']


for arguments in ['', 'value: uint8', 'value: uint8,']:
    declaration = parse(f'native function r#function({arguments}): uint8;')
    assert declaration.name.id_str == 'function'
    assert len(declaration.arguments) == (0 if not arguments else 1)
