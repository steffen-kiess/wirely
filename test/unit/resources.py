import asyncio
import contextlib
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import model as blocks
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type


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

assert not program.variables
for block in graph.blocks.values():
    assert not block.block_temporaries
    for connector in block.block_inputs + block.block_outputs:
        assert isinstance(connector.conn_spec, blocks.ConnectorSpecificationResource)
        assert connector.resource_type is not None
        assert not hasattr(connector, 'orig_temporary')

checks = TestCase()
registry = {}
constructed = []
procedure_calls = []
startup_inputs = []


@native_block_type(registry, has_native_state=True)
class Provider(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        inputs = context.inputs
        assert isinstance(inputs, Mapping)
        assert isinstance(inputs, MappingProxyType)
        assert not inputs
        self.value = 7
        constructed.append(self)


@native_block_type(registry, has_native_state=True)
class Shared(Provider):
    pass


@native_block_type(registry, has_native_state=True)
class Consumer(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        inputs = context.inputs
        assert isinstance(inputs, MappingProxyType)
        assert list(inputs) == ['Service', 'Services', 'Empty', 'implicit', 'child']
        assert inputs['Service'] in constructed
        assert inputs['Services'] == (inputs['Service'], inputs['implicit'], inputs['Service'])
        assert inputs['Empty'] == ()
        assert inputs['child'] is inputs['Service']
        with checks.assertRaises(TypeError):
            inputs['Service'] = None
        with checks.assertRaises(KeyError):
            inputs['missing']
        constructed.append(self)

    def start_tasks(self, task_group, async_exit_stack):
        startup_inputs.append(self.inputs)

    def read(self, *, context):
        assert dict(context.inputs) == dict(self.inputs)
        procedure_calls.append(('read', context.inputs['Service']))
        context.set_result('Count', context.inputs['Service'].value)

    def poll(self, *, context):
        assert context.inputs['Service'] is self.inputs['Service']
        procedure_calls.append(('poll', context.inputs['Service']))


@native_block_type(registry, has_native_state=False)
class ProcedureConsumer:
    @staticmethod
    def read(*, context):
        assert context.inputs['Service'] is context.inputs['child']
        assert isinstance(context.inputs['Value'], int)
        context.set_result('Count', context.inputs['Service'].value)


@native_block_type(registry, has_native_state=True)
class Dependent(blocks.NativeState):
    pass


def compile_resources(source):
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = blocks.compiler.compile_strs([
            ('wirely/lib/std.wrl', Path('wirely/lib/std.wrl').read_text()),
            ('<native-resources>', source),
        ])
    assert graph is not None
    return graph


source = '''
namespace TestLibrary {
  resource type Service;

  native node type Provider {
    default output: resource Service;
    output Named: resource Service;
    native initializer;
  }

  singleton native node type Shared {
    default output: resource Service;
  }

  node type Forward {
    input Service: resource Service;
    default output: resource Service;
    output Named: resource Service;
    connect Service -> default output;
    connect Service -> Named;
  }

  node type Factory {
    default output: resource Service;
    node source: Provider {}
    connect source -> default output;
  }

  native node type Consumer {
    input Service: resource Service;
    input Services: list resource Service;
    input Empty: list resource Service;
    default input: resource Service;
    output Count: value uint8;

    node forwarded: Forward {}
    connect Service -> forwarded.Service;

    native initializer
      depends on Service, Services, Empty, default input as "implicit",
        forwarded as "child";

    native procedure read
      depends on Service, Services, Empty, default input as "implicit",
        forwarded as "child"
      produces Count;
    native procedure poll stage input depends on Service;
  }

  node type ProcedureConsumer {
    input Service: resource Service;
    input Value: value uint8;
    output Count: value uint8;

    node forwarded: Forward {}
    connect Service -> forwarded.Service;

    native procedure read
      depends on Service, Value, forwarded.Named as "child"
      produces Count;
  }
}

node consumer: TestLibrary.Consumer {}
node procedure_consumer: TestLibrary.ProcedureConsumer {}
node factory: TestLibrary.Factory {}
node shared: TestLibrary.Shared {}
node other_shared: TestLibrary.Shared {}
node forward: TestLibrary.Forward {}
node value_source: Std.DebugValueSource { Type: uint8 }
connect factory -> forward.Service;
connect forward -> consumer.Service;
connect factory, shared, forward -> consumer.Services;
connect other_shared -> consumer;
connect factory -> procedure_consumer.Service;
connect value_source -> procedure_consumer.Value;
'''
native_resource_program = program_compiler.compile_nocatch(compile_resources(source))
with patch.dict(wirely.native.test_library.native_block_types, registry):
    native_resource_engine = blocks.Engine(native_resource_program)
consumer = native_resource_engine.get_native_state('consumer')
provider = native_resource_engine.get_native_state('factory.source')
shared = native_resource_engine.get_native_state('shared')
assert native_resource_engine.get_native_state('other_shared') is shared
assert consumer.inputs['Service'] is provider
assert consumer.inputs['implicit'] is shared
assert constructed.index(provider) < constructed.index(consumer)
value_source = native_resource_engine.get_native_state('value_source')
value_source.set_value(7)
native_resource_engine.step()
native_resource_engine.assert_value_is('consumer.Count', 7)
native_resource_engine.assert_value_is('procedure_consumer.Count', 7)
assert [name for name, _ in procedure_calls] == ['poll', 'read']
provider.value = 8
native_resource_engine.step()
assert len(procedure_calls) == 2
native_resource_engine.assert_value_is('consumer.Count', 7)
native_resource_engine.assert_value_is('procedure_consumer.Count', 7)
native_resource_engine.assert_is_idle()
value_source.set_value(9)
native_resource_engine.step()
native_resource_engine.assert_value_is('procedure_consumer.Count', 8)
assert len(procedure_calls) == 2

# Explicit aliases and direct named resource outputs use the same key rules.
alias_graph = compile_resources('''
namespace TestLibrary {
  resource type Service;
  native node type Provider {
    default output: resource Service;
    output Named: resource Service;
  }
  native node type Dependent {
    input Service: resource Service;
    node provider: Provider {}
    native initializer
      depends on Service as "service", provider.Named as "child";
  }
}
node sink: TestLibrary.Dependent {}
node source: TestLibrary.Provider {}
connect source -> sink.Service;
''')
with patch.dict(wirely.native.test_library.native_block_types, registry):
    alias_engine = blocks.Engine(program_compiler.compile_nocatch(alias_graph))
alias_inputs = alias_engine.get_native_state('sink').inputs
assert alias_inputs['service'] is alias_engine.get_native_state('source')
assert alias_inputs['child'] is alias_engine.get_native_state('sink.provider')


for nodes, connection in [
    ('node a: TestLibrary.Dependent {}', 'connect a -> a.Service;'),
    ('''
node a: TestLibrary.Dependent {}
node b: TestLibrary.Dependent {}
''', 'connect a -> b.Service;\nconnect b -> a.Service;'),
]:
    graph = compile_resources(f'''
namespace TestLibrary {{
  resource type Service;
  native node type Dependent {{
    input Service: resource Service;
    default output: resource Service;
    native initializer depends on Service;
  }}
}}
{nodes}
{connection}
''')
    with checks.assertRaisesRegex(blocks.ProgramError, 'Circular native initializer dependency') as error:
        program_compiler.compile_nocatch(graph)
    assert error.exception.source_element.key == 'Service'

graph = compile_resources('''
namespace TestLibrary {
  resource type Service;
  node type Forward {
    input Service: resource Service;
    default output: resource Service;
    connect Service -> default output;
  }
  native node type Dependent {
    input Service: resource Service;
    native initializer depends on Service;
  }
}
node a: TestLibrary.Forward {}
node b: TestLibrary.Forward {}
node sink: TestLibrary.Dependent {}
connect a -> b.Service;
connect b -> a.Service;
connect a -> sink.Service;
''')
with checks.assertRaisesRegex(blocks.ProgramError, 'Circular resource connection'):
    program_compiler.compile_nocatch(graph)

for binding in ['initializer', 'procedure']:
    declaration = ('native initializer depends on Service;' if binding == 'initializer'
                   else 'native procedure read depends on Service produces Count;')
    node_type = 'native node type Dependent' if binding == 'initializer' else 'node type ProcedureConsumer'
    output = 'output Count: value uint8;' if binding == 'procedure' else ''
    graph = compile_resources(f'''
namespace TestLibrary {{
  resource type Service;
  node type Missing {{
    default output: resource Service;
  }}
  {node_type} {{
    input Service: resource Service;
    {output}
    {declaration}
  }}
}}
node source: TestLibrary.Missing {{}}
node sink: TestLibrary.{'Dependent' if binding == 'initializer' else 'ProcedureConsumer'} {{}}
connect source -> sink.Service;
''')
    with checks.assertRaisesRegex(blocks.ProgramError, 'Resource binding has no native provider') as error:
        program_compiler.compile_nocatch(graph)
    assert error.exception.source_element.key == 'Service'


async def run_test():
    async with asyncio.TaskGroup() as task_group, contextlib.AsyncExitStack() as exit_stack:
        await native_resource_engine.start_tasks_async(task_group, exit_stack)
        assert startup_inputs == [consumer.inputs]
