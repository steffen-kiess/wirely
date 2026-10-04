from unittest import TestCase
from unittest.mock import patch

from lark import Lark
from lark.exceptions import UnexpectedInput
from lark.parsers.lalr_analysis import LALR_Analyzer

import wirely
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.parser import parse_str, parser


weak_keywords = [
    'constant', 'default', 'event', 'input', 'multi', 'on', 'output',
    'resource', 'stage', 'state', 'type', 'value',
]
reserved_keywords = [
    'as', 'assert', 'connect', 'depends', 'enum', 'initializer', 'namespace',
    'function', 'native', 'node', 'optional', 'parameter', 'procedure', 'produces',
    'singleton', 'struct', 'temporary',
]


def assert_conflict_free(candidate):
    # Debug mode reports shift/reduce conflicts on every supported Lark version.
    # Reduce/reduce conflicts raise errors directly.
    with patch('lark.parsers.lalr_analysis.logger.warning') as warning:
        LALR_Analyzer(candidate.parser.parser_conf, debug=True).compute_lalr()
        warning.assert_not_called()


assert_conflict_free(parser)
conflicting = Lark('start: prefix "x" | "x"\nprefix:', parser='lalr')
with TestCase().assertRaises(AssertionError):
    assert_conflict_free(conflicting)

engine.step()
engine.assert_value_is('sink', True)
engine.assert_value_is('produces', True)
graph = engine.program.graph
assert len(graph.connections) == len(weak_keywords) + 1
for index, keyword in enumerate(weak_keywords):
    engine.assert_value_is(keyword, True)
    edge = graph.connections[index]
    assert edge.source is graph.blocks[keyword].block_outputs_byname['']
    assert edge.target is graph.blocks['sink'].block_inputs_byname[''].members[index]


def parse(source):
    return parse_str(source, filename='<weak-keywords>')[0]


for keyword in weak_keywords:
    for name in [keyword, f'r#{keyword}']:
        for source in [
            f'node {name}: T {{}}',
            f'input {name}: value boolean;',
            f'output {name}: event unit;',
            f'parameter {name}: boolean;',
            f'constant {name}: boolean = true;',
            f'state {name}: boolean = false;',
            f'temporary {name}: boolean;',
            f'native procedure {name};',
        ]:
            assert parse(source).name.id_str == keyword
        argument = parse(f'node n: T {{ {name}: true }}').instantiation.parameters[0]
        assert argument.name.id_str == keyword
        connection = parse(f'connect source.{name} -> {name};')
        assert str(connection.sources[0]) == f'source.{keyword}'
        assert str(connection.target) == keyword
        procedure = parse(
            f'native procedure run depends on {name}, produces {name};'
        )
        assert procedure.depends.dependencies[0].key == keyword
        assert procedure.produces.outputs[0].key == keyword
        attribute = parse(f'#[{name}] node n: T {{}}').outer_attributes[0]
        assert attribute.name == keyword
        struct = parse(f'struct type S {{ {name}: boolean, }}')
        assert struct.fields[0].name.id_str == keyword
        enum = parse(f'enum type E: uint8 {{ {name} = 0, }}')
        assert enum.members[0].name.id_str == keyword
        parse(f'node type {name} {{}}')
        parse(f'namespace {name} {{}}')

    for name in [f'{keyword}_name', f'{keyword}name', f'name_{keyword}']:
        assert parse(f'node {name}: T {{}}').name.id_str == name

    for name in [keyword, f'r#{keyword}']:
        compiled = compile_strs([('<weak-parameter>', f'''
node type Forward {{
  parameter {name}: boolean;
  node constant: Std.Constant {{ Type: boolean, Value: {name} }}
}}
node forwarded: Forward {{ {name}: true }}
''')], add_std=True)
        assert compiled is not None
        runtime = wirely.Engine(program_compiler.compile(compiled))
        assert runtime.program.instances['forwarded'].parameters[keyword] is True
        runtime.step()
        runtime.assert_value_is('forwarded.constant', True)

procedure = parse(
    'native procedure stage stage input depends on on, stage, produces output;'
)
assert procedure.name.id_str == 'stage'
assert [binding.key for binding in procedure.depends.dependencies] == ['on', 'stage']
assert [binding.key for binding in procedure.produces.outputs] == ['output']
connector = parse('input input: multi event unit;')
assert connector.name.id_str == 'input'
parse('output resource: resource Handle;')
parse('native initializer depends on resource, on;')

for direction in ['input', 'output']:
    connector = parse(f'default {direction}: value boolean;')
    assert connector.name is None
    assert connector.name_str == f'default {direction}'
    connection = parse(f'connect default {direction} -> default;')
    assert str(connection.sources[0]) == f'default {direction}'
    assert str(connection.target) == 'default'
    connection = parse(f'connect default -> default {direction};')
    assert str(connection.sources[0]) == 'default'
    assert str(connection.target) == f'default {direction}'

assert parse('node implicit: T {}').name.id_str == 'implicit'
assert parse('node list: T {}').name.id_str == 'list'

for specification in ['value boolean', 'event unit', 'resource Handle']:
    connector = parse(f'input multi: multi {specification};')
    assert connector.name.id_str == 'multi'
    assert connector.list_spec.is_list
    connector = parse(f'default input: multi {specification};')
    assert connector.name is None
    assert connector.list_spec.is_list

for keyword in reserved_keywords:
    try:
        parse(f'node {keyword}: T {{}}')
    except UnexpectedInput:
        pass
    else:
        raise AssertionError(f'Reserved keyword accepted as a name: {keyword}')
    assert parse(f'node r#{keyword}: T {{}}').name.id_str == keyword
    assert parse(f'node {keyword}name: T {{}}').name.id_str == f'{keyword}name'
