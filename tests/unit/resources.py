import asyncio
import contextlib
import dataclasses
from pathlib import Path
from unittest import TestCase

import wirely
from wirely.compiler import program as program_compiler
import wirely.compiler.compilation
from wirely.program.program import RunStage
from wirely.runtime.engine import Engine
from wirely.runtime.loading import LoadedProgram
from wirely.runtime.procedures import LoadError
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

    class Provider(contract('ProviderCreate'), Service):
        def initialize(self, *, services):
            self.value = 7
            self.named = Service()
            return contracts.ProviderCreateResult(service=self, named=self.named)

    class Shared(contract('SharedCreate'), Service):
        def initialize(self, *, services):
            self.value = 7
            return contracts.SharedCreateResult(service=self)

    class Incomplete(contract('IncompleteCreate'), Service):
        def initialize(self, *, services):
            return None

    class WrongProvider(contract('WrongProviderCreate')):
        def initialize(self, *, services):
            return contracts.WrongProviderCreateResult(service=object())

    class Consumer(contract('ConsumerCreate')):
        def initialize(self, *, services, service, other_service, repeated_service, implicit, child):
            assert other_service is implicit
            assert repeated_service is service
            assert child is service
            assert service.value == 7
            self.resources = dict(service=service, other_service=other_service, repeated_service=repeated_service,
                                  implicit=implicit, child=child)
            return contracts.ConsumerCreateResult(state=self)

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

    class Dependent(contract('DependentCreate')):
        def initialize(self, *, services, service, child):
            self.resources = dict(service=service, child=child)
            return contracts.DependentCreateResult()

    initializers = {f'TestLibrary.{cls.__name__}:create': cls
                    for cls in (Provider, Shared, Incomplete, WrongProvider, Consumer, Dependent)
                    if hasattr(contracts, cls.__name__ + 'Create')}
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
        return Engine(LoadedProgram.load(program_compiler.compile_nocatch(graph), override_extensions=[bindings, resource_bindings]))


def compile_resources(source):
    graph = wirely.compiler.compilation.compile_strs([
        ('wirely/lib/std.wrl', Path('wirely/lib/std.wrl').read_text()),
        ('<native-resources>', source),
    ])
    assert graph is not None
    return graph


source = '''
namespace TestLibrary {
  resource type Service;
  resource type ConsumerState;

  node type Provider {
    default output: fixed Service;
    output Named: fixed Service;
    native initializer create writes default output as "service", Named as "named";
  }

  singleton node type Shared {
    default output: fixed Service;
    native initializer create writes default output as "service";
  }

  node type Forward {
    input Service: fixed Service;
    default output: fixed Service;
    output Named: fixed Service;
    connect Service -> default output;
    connect Service -> Named;
  }

  node type Factory {
    default output: fixed Service;
    node source: Provider {}
    connect source -> default output;
  }

  node type Consumer {
    input Service: fixed Service;
    input OtherService: fixed Service;
    input RepeatedService: fixed Service;
    default input: fixed Service;
    output Count: value uint8;

    node forwarded: Forward {}
    connect Service -> forwarded.Service;

    binding state: fixed ConsumerState;
    native initializer create
      reads Service, OtherService, RepeatedService,
        default input as "implicit", forwarded as "child"
      writes state as "state";

    native procedure read
      reads state as "state", Service, OtherService, RepeatedService,
        default input as "implicit", forwarded as "child"
      writes Count;
    native procedure poll stage input reads state as "state", Service;
  }

  node type ProcedureConsumer {
    input Service: fixed Service;
    input Value: value uint8;
    output Count: value uint8;

    node forwarded: Forward {}
    connect Service -> forwarded.Service;

    native procedure read
      reads Service, Value, forwarded.Named as "child"
      writes Count;
  }

  node type TypedProcedureConsumer {
    input Service: fixed Service;
    input Value: value uint8;
    output Count: value uint8;

    node forwarded: Forward {}
    connect Service -> forwarded.Service;

    native procedure read
      reads Service: fixed Service, Value: value uint8, child: fixed Service
      writes Count: value uint8;
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
node value_source: Std.DebugValueSource { Type: uint8, Initial: 0 }
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
consumer = native_resource_engine.get_native_state('consumer.create')
provider = native_resource_engine.get_native_state('factory.source.create')
shared = native_resource_engine.get_native_state('TestLibrary.Shared:create')
node_aliases = native_resource_engine.program.symbols.node_aliases
assert node_aliases['shared'] == node_aliases['other_shared']
assert consumer.resources['service'] is provider
assert consumer.resources['implicit'] is shared
value_source = native_resource_engine.get_native_state('value_source.create')
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
  node type Provider {
    default output: fixed Service;
    output Named: fixed Service;
    native initializer create writes default output as "service", Named as "named";
  }
  node type Dependent {
    input Service: fixed Service;
    node provider: Provider {}
    native initializer create
      reads Service as "service", provider.Named as "child";
  }
}
node sink: TestLibrary.Dependent {}
node source: TestLibrary.Provider {}
connect source -> sink.Service;
''')
alias_engine = make_engine(alias_graph)
alias_inputs = alias_engine.get_native_state('sink.create').resources
assert alias_inputs['service'] is alias_engine.get_native_state('source.create')
# Each output supplies the object its provider wrote under the output's key.
assert alias_inputs['child'] is alias_engine.get_native_state('sink.provider.create').named

incomplete_graph = compile_resources('''
namespace TestLibrary {
  resource type Service;
  node type Incomplete {
    default output: fixed Service;
    output Named: fixed Service;
    native initializer create writes default output as "service", Named as "named";
  }
}
node source: TestLibrary.Incomplete {}
''')
with checks.assertRaisesRegex(LoadError, "Native initializer `TestLibrary.Incomplete:create` of `source` must "
                              'return a IncompleteCreateResult, got None'):
    make_engine(incomplete_graph)


# Resource outputs are typed ResourceValues; their objects are checked
# against the Python type registered for the resource type.
initializer = next(proc for proc in native_resource_engine.program.procs[RunStage.LOAD]
                   if proc.name == 'factory.source.create')
service_slot = dict(initializer.named_outputs())['service'].variable
service_value = native_resource_engine.resource_values[service_slot]
assert isinstance(service_value, ResourceValue) and service_value.object is provider
checks.assertEqual((service_value.type.name, service_value.type.python_type), ('TestLibrary.Service', Service))
with checks.assertRaises(dataclasses.FrozenInstanceError):
    service_value.object = None


wrong_graph = compile_resources('''
namespace TestLibrary {
  resource type Service;
  node type WrongProvider {
    default output: fixed Service;
    native initializer create writes default output as "service";
  }
}
node source: TestLibrary.WrongProvider {}
''')
with checks.assertRaisesRegex(
        LoadError, "invalid value for output `service`: Resource `TestLibrary.Service` requires a Service"):
    make_engine(wrong_graph)

unknown_graph = compile_resources('''
namespace TestLibrary {
  resource type Unknown;
  node type Provider {
    default output: fixed Unknown;
    native initializer create writes default output as "service";
  }
}
node source: TestLibrary.Provider {}
''')
with checks.assertRaisesRegex(LoadError, "Cannot find native resource type `TestLibrary.Unknown`"):
    make_engine(unknown_graph)


# A resource declaration passes on its one source, like a temporary: a native
# node can hand its state to a child, and a composite node can name a child's
# resource.
alias_reads = []
alias_graph = wirely.compiler.compilation.compile_strs([('<resource-aliases>', '''
namespace TestLibrary {
  resource type Service;
  node type AliasSource {
    default output: fixed Service;
    native initializer create writes default output as "service";
  }
  node type AliasChild {
    input Service: fixed Service;
    native initializer create reads Service as "service";
  }
  node type AliasHolder {
    binding state: fixed Service;
    native initializer create writes state as "state";
    node child: AliasChild {}
    connect state -> child.Service;
  }
  node type AliasComposite {
    binding service: fixed Service;
    node source: AliasSource {}
    connect source -> service;
    node child: AliasChild {}
    connect service -> child.Service;
    native procedure read reads service as "service";
  }
}
node holder: TestLibrary.AliasHolder {}
node composite: TestLibrary.AliasComposite {}
''')])
assert alias_graph is not None
alias_program = program_compiler.compile_nocatch(alias_graph)
with test_contracts(alias_graph) as contracts:
    alias_registry = {'initializers': {}, 'procedures': {}}

    class AliasSource(contracts.AliasSourceCreate, Service):
        def initialize(self, *, services):
            return contracts.AliasSourceCreateResult(service=self)
    alias_registry['initializers']['TestLibrary.AliasSource:create'] = AliasSource

    class AliasChild(contracts.AliasChildCreate):
        def initialize(self, *, services, service):
            self.service = service
            return contracts.AliasChildCreateResult()
    alias_registry['initializers']['TestLibrary.AliasChild:create'] = AliasChild

    class AliasHolder(contracts.AliasHolderCreate, Service):
        def initialize(self, *, services):
            return contracts.AliasHolderCreateResult(state=self)
    alias_registry['initializers']['TestLibrary.AliasHolder:create'] = AliasHolder

    class AliasComposite:
        class Read(contracts.AliasCompositeRead):
            def run(self, *, services, service):
                alias_reads.append(service)
                return contracts.AliasCompositeReadResult()
    alias_registry['procedures']['TestLibrary.AliasComposite:read'] = AliasComposite.Read

    bindings = test_extension(**alias_registry, contracts=contracts)
    resource_bindings = test_extension(resources={'TestLibrary.Service': Service}, contracts=contracts)
    alias_resource_engine = Engine(LoadedProgram.load(alias_program, override_extensions=[bindings, resource_bindings]))
holder = alias_resource_engine.get_native_state('holder.create')
assert alias_resource_engine.get_native_state('holder.child.create').service is holder
source_state = alias_resource_engine.get_native_state('composite.source.create')
assert alias_resource_engine.get_native_state('composite.child.create').service is source_state
read = next(proc for schedule in alias_program.procs.values() for proc in schedule if proc.node == 'composite')
assert [(name, alias_resource_engine.resource_values[source.resource].object) for name, source in read.named_inputs()] == [
    ('service', source_state),
]
alias_resource_engine.step()
assert alias_reads == [source_state]
resource_alias = next(name for name, source in alias_program.symbols.variable_aliases.items()
                      if source.resource is not None)
with checks.assertRaisesRegex(ValueError, 'refers to a resource'):
    alias_resource_engine.get_variable(resource_alias)


async def run_test():
    async with asyncio.TaskGroup() as task_group, contextlib.AsyncExitStack() as exit_stack:
        native_resource_engine.activate(task_group, exit_stack)
        assert startup_inputs == [consumer.resources]
