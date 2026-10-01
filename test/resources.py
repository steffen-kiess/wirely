import lark

import wirely as blocks
import wirely.parser


program = engine.program
graph = program.graph
deep = graph.libraries['ResourceFixture'].libraries['Deep']
handle = deep.resource_types['Handle']
assert str(handle) == 'ResourceFixture.Deep.Handle'
assert handle is not graph.libraries['OtherResourceFixture'].resource_types['Handle']
assert graph.blocks['root_source'].block_outputs_byname[''].resource_type is graph.resource_types['RootHandle']


def assert_source(path, connector, expected_path, expected_output):
    actual_path, actual_output = blocks.resolve_variable(path, connector)
    assert actual_path == expected_path, (actual_path, expected_path)
    assert actual_output is expected_output
    assert actual_output.resource_type is handle


source = graph.blocks['source']
relay = graph.blocks['relay']
sink = graph.blocks['sink']
factory = graph.blocks['factory']
factory_source = factory.block_type.blocks['source']
second_relay = graph.blocks['second_relay']
second_sink = graph.blocks['second_sink']
source_output = source.block_outputs_byname['']
factory_output = factory_source.block_outputs_byname['']
assert_source((sink,), sink.block_inputs_byname['Service'], (source,), source_output)
assert_source((relay,), relay.block_outputs_byname[''], (source,), source_output)
assert_source((second_sink,), second_sink.block_inputs_byname['Service'],
              (factory, factory_source), factory_output)
for wrapper, expected_path, expected_output in [
    (relay, (source,), source_output),
    (second_relay, (factory, factory_source), factory_output),
]:
    client = wrapper.block_type.blocks['client']
    assert_source((wrapper, client), client.block_inputs_byname['Service'],
                  expected_path, expected_output)

many = graph.blocks['many']
services = many.block_inputs_byname['Services']
assert services.resource_type is handle
assert len(services.members) == 3
for connector, expected_path, expected_output in zip(
    services.members, [(source,), (factory, factory_source), (source,)],
    [source_output, factory_output, source_output],
):
    assert connector.resource_type is handle
    assert_source((many,), connector, expected_path, expected_output)

outer = graph.blocks['outer']
assert_source((outer,), outer.block_inputs_byname['Service'], (source,), source_output)
root_source = graph.blocks['root_source']
root_sink = graph.blocks['root_sink']
root_path, root_output = blocks.resolve_variable((root_sink,), root_sink.block_inputs_byname[''])
assert root_path == (root_source,)
assert root_output is root_source.block_outputs_byname['']

shared = graph.blocks['shared']
shared_sink = graph.blocks['shared_sink']
assert_source((shared_sink,), shared_sink.block_inputs_byname['Service'],
              (shared.block_type,), shared.block_outputs_byname[''])

assert not program.variable_map
for instance in program.instances.values():
    block = instance.block
    if block is None:
        block = instance.block_type.singleton_instance
    assert not block.block_temporaries
    for connector in block.block_inputs + block.block_outputs:
        assert isinstance(connector.conn_spec, blocks.ConnectorSpecificationResource)
        assert connector.resource_type is not None
        assert not hasattr(connector, 'orig_temporary')


for legacy_type in ['knx_bus', 'mqtt_server', 'two_n_device']:
    try:
        wirely.parser.parse_str(
            'node type Legacy {\n'
            f'  implicit output : {legacy_type};\n'
            '}',
            filename='<legacy-resource>',
        )
    except lark.UnexpectedInput:
        pass
    else:
        raise AssertionError(f'Legacy resource syntax was accepted: {legacy_type}')
