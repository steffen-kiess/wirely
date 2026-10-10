import asyncio
import contextlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import wirely as blocks
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type
from wirely.runtime.contexts import NativeState
from wirely.runtime.engine import Engine


checks = TestCase()
registry = {}
procedure_calls = []
startup_inputs = []


@native_block_type(registry, has_native_state=True)
class Provider(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        inputs = context.inputs
        assert not inputs
        self.value = 7


@native_block_type(registry, has_native_state=True)
class Shared(Provider):
    pass


@native_block_type(registry, has_native_state=True)
class Consumer(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        inputs = context.inputs
        assert inputs['Services'] == (inputs['Service'], inputs['implicit'], inputs['Service'])
        assert inputs['Empty'] == ()
        assert inputs['child'] is inputs['Service']
        assert inputs['Service'].value == 7
        with checks.assertRaises(TypeError):
            inputs['Service'] = None
        with checks.assertRaises(KeyError):
            inputs['missing']

    def start_tasks(self, task_group, async_exit_stack):
        startup_inputs.append(self.inputs)

    def read(self, *, context):
        assert dict(context.inputs) == dict(self.inputs)
        procedure_calls.append(('read', context.inputs['Service']))
        context.set_output('Count', context.inputs['Service'].value)

    def poll(self, *, context):
        assert context.inputs['Service'] is self.inputs['Service']
        procedure_calls.append(('poll', context.inputs['Service']))


@native_block_type(registry, has_native_state=False)
class ProcedureConsumer:
    @staticmethod
    def read(*, context):
        assert context.inputs['Service'] is context.inputs['child']
        assert isinstance(context.inputs['Value'], int)
        context.set_output('Count', context.inputs['Service'].value)


@native_block_type(registry, has_native_state=False)
class TypedProcedureConsumer(ProcedureConsumer):
    pass


@native_block_type(registry, has_native_state=True)
class Dependent(NativeState):
    pass


def compile_resources(source):
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs([
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
    input Services: multi resource Service;
    input Empty: multi resource Service;
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

  node type TypedProcedureConsumer {
    input Service: resource Service;
    input Value: value uint8;
    output Count: value uint8;

    node forwarded: Forward {}
    connect Service -> forwarded.Service;

    native procedure read
      depends on Service: resource Service, Value: value uint8, child: resource Service
      produces Count: value uint8;
    connect Service -> read.Service;
    connect Value -> read.Value;
    connect forwarded.Named -> read.child;
    connect read.Count -> Count;
  }
}

node consumer: TestLibrary.Consumer {}
node procedure_consumer: TestLibrary.ProcedureConsumer {}
node typed_consumer: TestLibrary.TypedProcedureConsumer {}
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
connect factory -> typed_consumer.Service;
connect value_source -> typed_consumer.Value;
'''
native_resource_program = program_compiler.compile_nocatch(compile_resources(source))
with patch.dict(wirely.native.test_library.native_block_types, registry):
    native_resource_engine = Engine(native_resource_program)
consumer = native_resource_engine.get_native_state('consumer')
provider = native_resource_engine.get_native_state('factory.source')
shared = native_resource_engine.get_native_state('shared')
assert native_resource_engine.get_native_state('other_shared') is shared
assert consumer.inputs['Service'] is provider
assert consumer.inputs['implicit'] is shared
value_source = native_resource_engine.get_native_state('value_source')
value_source.set_value(7)
native_resource_engine.step()
native_resource_engine.assert_value_is('consumer.Count', 7)
native_resource_engine.assert_value_is('procedure_consumer.Count', 7)
native_resource_engine.assert_value_is('typed_consumer.Count', 7)
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
native_resource_engine.assert_value_is('typed_consumer.Count', 8)
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
    alias_engine = Engine(program_compiler.compile_nocatch(alias_graph))
alias_inputs = alias_engine.get_native_state('sink').inputs
assert alias_inputs['service'] is alias_engine.get_native_state('source')
assert alias_inputs['child'] is alias_engine.get_native_state('sink.provider')


async def run_test():
    async with asyncio.TaskGroup() as task_group, contextlib.AsyncExitStack() as exit_stack:
        await native_resource_engine.start_tasks_async(task_group, exit_stack)
        assert startup_inputs == [consumer.inputs]
