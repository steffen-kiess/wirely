import asyncio
import contextlib
import dataclasses
from pathlib import Path
from unittest import TestCase

import wirely
from wirely.compiler import program as program_compiler
import wirely.compiler
from wirely.runtime.engine import Engine
from wirely.runtime.resources import ResourceValue
from tests.infra.support import test_extension, test_contracts


checks = TestCase()
procedure_calls = []
startup_inputs = []


class Service:
    """The Python type of TestLibrary.Service resources."""


def native_classes(contracts):
    """Return the native classes of the TestLibrary node types whose contracts a program has."""
    def contract(name):
        # Classes of interfaces a program does not declare are not registered.
        return getattr(contracts, name, None) or type(name, (), {})

    class Provider(contract('ProviderInitializer'), Service):
        def initialize(self, *, services):
            self.value = 7
            self.named = Service()
            return contracts.ProviderInitializerResult(service=self, named=self.named)

    class Shared(contract('SharedInitializer'), Service):
        def initialize(self, *, services):
            self.value = 7
            return contracts.SharedInitializerResult(service=self)

    class Incomplete(contract('IncompleteInitializer'), Service):
        def initialize(self, *, services):
            return None

    class WrongProvider(contract('WrongProviderInitializer')):
        def initialize(self, *, services):
            return contracts.WrongProviderInitializerResult(service=object())

    class Consumer(contract('ConsumerInitializer')):
        def initialize(self, *, services, service, other_service, repeated_service, implicit, child):
            assert other_service is implicit
            assert repeated_service is service
            assert child is service
            assert service.value == 7
            self.resources = dict(service=service, other_service=other_service, repeated_service=repeated_service,
                                  implicit=implicit, child=child)
            return contracts.ConsumerInitializerResult(state=self)

        def start_tasks(self, task_group, async_exit_stack):
            startup_inputs.append(self.resources)

        class Read(contract('ConsumerRead')):
            def run(self, *, services, state, **resources):
                assert resources == state.resources
                procedure_calls.append(('read', resources['service']))
                return contracts.ConsumerReadResult(count=wirely.UInt8(resources['service'].value))

        class Poll(contract('ConsumerPoll')):
            def run(self, *, services, state, service):
                assert service is state.resources['service']
                procedure_calls.append(('poll', service))
                return contracts.ConsumerPollResult()

    def read_count(service, value, child):
        assert service is child
        assert isinstance(value, wirely.UInt8)
        return wirely.UInt8(service.value)

    class ProcedureConsumer:
        class Read(contract('ProcedureConsumerRead')):
            def run(self, *, services, service, value, child):
                return contracts.ProcedureConsumerReadResult(count=read_count(service, value, child))

    class TypedProcedureConsumer:
        class Read(contract('TypedProcedureConsumerRead')):
            def run(self, *, services, service, value, child):
                return contracts.TypedProcedureConsumerReadResult(count=read_count(service, value, child))

    class Dependent(contract('DependentInitializer')):
        def initialize(self, *, services, service, child):
            self.resources = dict(service=service, child=child)
            return contracts.DependentInitializerResult()

    initializers = {f'TestLibrary.{cls.__name__}': cls
                    for cls in (Provider, Shared, Incomplete, WrongProvider, Consumer, Dependent)
                    if hasattr(contracts, cls.__name__ + 'Initializer')}
    procedures = {name: cls for name, contract_name, cls in (
        ('TestLibrary.Consumer:read', 'ConsumerRead', Consumer.Read),
        ('TestLibrary.Consumer:poll', 'ConsumerPoll', Consumer.Poll),
        ('TestLibrary.ProcedureConsumer:read', 'ProcedureConsumerRead', ProcedureConsumer.Read),
        ('TestLibrary.TypedProcedureConsumer:read', 'TypedProcedureConsumerRead', TypedProcedureConsumer.Read))
        if hasattr(contracts, contract_name)}
    return {'initializers': initializers, 'procedures': procedures}, {
        'TestLibrary.Service': Service, 'TestLibrary.ConsumerState': Consumer}


def make_engine(graph):
    """Construct an Engine for a graph with this test's native node and resource types."""
    with test_contracts(graph) as contracts:
        registry, resource_types = native_classes(contracts)
        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources=resource_types, contracts=contracts)
        return Engine(program_compiler.compile_nocatch(graph), override_extensions=[bindings, resource_bindings])


def compile_resources(source):
    graph = wirely.compiler.compile_strs([
        ('wirely/lib/std.wrl', Path('wirely/lib/std.wrl').read_text()),
        ('<native-resources>', source),
    ])
    assert graph is not None
    return graph


source = '''
namespace TestLibrary {
  resource type Service;
  resource type ConsumerState;

  native node type Provider {
    default output: resource Service;
    output Named: resource Service;
    native initializer produces default output as "service", Named as "named";
  }

  singleton native node type Shared {
    default output: resource Service;
    native initializer produces default output as "service";
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
    input OtherService: resource Service;
    input RepeatedService: resource Service;
    default input: resource Service;
    output Count: value uint8;

    node forwarded: Forward {}
    connect Service -> forwarded.Service;

    binding state: resource ConsumerState;
    native initializer
      depends on Service, OtherService, RepeatedService,
        default input as "implicit", forwarded as "child"
      produces state as "state";

    native procedure read
      depends on state as "state", Service, OtherService, RepeatedService,
        default input as "implicit", forwarded as "child"
      produces Count;
    native procedure poll stage input depends on state as "state", Service;
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
connect shared -> consumer.OtherService;
connect forward -> consumer.RepeatedService;
connect other_shared -> consumer;
connect factory -> procedure_consumer.Service;
connect value_source -> procedure_consumer.Value;
connect factory -> typed_consumer.Service;
connect value_source -> typed_consumer.Value;
'''
native_resource_engine = make_engine(compile_resources(source))
consumer = native_resource_engine.get_native_state('consumer')
provider = native_resource_engine.get_native_state('factory.source')
shared = native_resource_engine.get_native_state('shared')
assert native_resource_engine.get_native_state('other_shared') is shared
assert consumer.resources['service'] is provider
assert consumer.resources['implicit'] is shared
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
    native initializer produces default output as "service", Named as "named";
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
alias_engine = make_engine(alias_graph)
alias_inputs = alias_engine.get_native_state('sink').resources
assert alias_inputs['service'] is alias_engine.get_native_state('source')
# Each output supplies the object its provider wrote under the output's key.
assert alias_inputs['child'] is alias_engine.get_native_state('sink.provider').named

incomplete_graph = compile_resources('''
namespace TestLibrary {
  resource type Service;
  native node type Incomplete {
    default output: resource Service;
    output Named: resource Service;
    native initializer produces default output as "service", Named as "named";
  }
}
node source: TestLibrary.Incomplete {}
''')
with checks.assertRaisesRegex(ValueError, "Native state type `TestLibrary.Incomplete` of `source` must return "
                              'a IncompleteInitializerResult, got None'):
    make_engine(incomplete_graph)


# Resource outputs are typed ResourceValues; their objects are checked
# against the Python type registered for the resource type.
service_value = native_resource_engine.resource_outputs['factory.source', 'service']
assert isinstance(service_value, ResourceValue) and service_value.object is provider
checks.assertEqual((service_value.type.name, service_value.type.python_type), ('TestLibrary.Service', Service))
with checks.assertRaises(dataclasses.FrozenInstanceError):
    service_value.object = None


wrong_graph = compile_resources('''
namespace TestLibrary {
  resource type Service;
  native node type WrongProvider {
    default output: resource Service;
    native initializer produces default output as "service";
  }
}
node source: TestLibrary.WrongProvider {}
''')
with checks.assertRaisesRegex(
        ValueError, "invalid value for output `service`: Resource `TestLibrary.Service` requires a Service"):
    make_engine(wrong_graph)

unknown_graph = compile_resources('''
namespace TestLibrary {
  resource type Unknown;
  native node type Provider {
    default output: resource Unknown;
    native initializer produces default output as "service";
  }
}
node source: TestLibrary.Provider {}
''')
with checks.assertRaisesRegex(ValueError, "Cannot find native resource type `TestLibrary.Unknown`"):
    make_engine(unknown_graph)


# A resource declaration passes on its one source, like a temporary: a native
# node can hand its state to a child, and a composite node can name a child's
# resource.
alias_reads = []
alias_graph = wirely.compiler.compile_strs([('<resource-aliases>', '''
namespace TestLibrary {
  resource type Service;
  native node type AliasSource {
    default output: resource Service;
    native initializer produces default output as "service";
  }
  native node type AliasChild {
    input Service: resource Service;
    native initializer depends on Service as "service";
  }
  native node type AliasHolder {
    binding state: resource Service;
    native initializer produces state as "state";
    node child: AliasChild {}
    connect state -> child.Service;
  }
  node type AliasComposite {
    binding service: resource Service;
    node source: AliasSource {}
    connect source -> service;
    node child: AliasChild {}
    connect service -> child.Service;
    native procedure read depends on service as "service";
  }
}
node holder: TestLibrary.AliasHolder {}
node composite: TestLibrary.AliasComposite {}
''')])
assert alias_graph is not None
alias_program = program_compiler.compile_nocatch(alias_graph)
with test_contracts(alias_graph) as contracts:
    alias_registry = {'initializers': {}, 'procedures': {}}

    class AliasSource(contracts.AliasSourceInitializer, Service):
        def initialize(self, *, services):
            return contracts.AliasSourceInitializerResult(service=self)
    alias_registry['initializers']['TestLibrary.AliasSource'] = AliasSource

    class AliasChild(contracts.AliasChildInitializer):
        def initialize(self, *, services, service):
            self.service = service
            return contracts.AliasChildInitializerResult()
    alias_registry['initializers']['TestLibrary.AliasChild'] = AliasChild

    class AliasHolder(contracts.AliasHolderInitializer, Service):
        def initialize(self, *, services):
            return contracts.AliasHolderInitializerResult(state=self)
    alias_registry['initializers']['TestLibrary.AliasHolder'] = AliasHolder

    class AliasComposite:
        class Read(contracts.AliasCompositeRead):
            def run(self, *, services, service):
                alias_reads.append(service)
                return contracts.AliasCompositeReadResult()
    alias_registry['procedures']['TestLibrary.AliasComposite:read'] = AliasComposite.Read

    bindings = test_extension(**alias_registry, contracts=contracts)
    resource_bindings = test_extension(resources={'TestLibrary.Service': Service}, contracts=contracts)
    alias_resource_engine = Engine(alias_program, override_extensions=[bindings, resource_bindings])
holder = alias_resource_engine.get_native_state('holder')
assert alias_resource_engine.get_native_state('holder.child').service is holder
source_state = alias_resource_engine.get_native_state('composite.source')
assert alias_resource_engine.get_native_state('composite.child').service is source_state
read = next(proc for schedule in alias_program.procs.values() for proc in schedule if proc.node_id == 'composite')
assert dict(read.resources) == {'service': wirely.program.ResourceBinding('composite.source', 'service')}
alias_resource_engine.step()
assert alias_reads == [source_state]


async def run_test():
    async with asyncio.TaskGroup() as task_group, contextlib.AsyncExitStack() as exit_stack:
        await native_resource_engine.start_tasks_async(task_group, exit_stack)
        assert startup_inputs == [consumer.resources]
