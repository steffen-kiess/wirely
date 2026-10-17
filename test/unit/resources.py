import asyncio
import contextlib
import dataclasses
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_node_type, native_procedure
from wirely.runtime.contexts import NativeProcedure, NativeState
from wirely.test import test_engine
from wirely.runtime.resources import ResourceValue


checks = TestCase()
registry = {}
procedure_calls = []
startup_inputs = []


class Service:
    """The Python type of TestLibrary.Service resources."""


@native_node_type(registry, has_native_state=True)
class Provider(NativeState, Service):
    def __init__(self, *, context):
        super().__init__(context=context)
        inputs = context.inputs
        assert not inputs
        self.value = 7
        self.named = Service()
        context.set_output('service', self)
        context.set_output('named', self.named)


@native_node_type(registry, has_native_state=True)
class Shared(NativeState, Service):
    def __init__(self, *, context):
        super().__init__(context=context)
        self.value = 7
        context.set_output('service', self)


@native_node_type(registry, has_native_state=True)
class Incomplete(NativeState, Service):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('service', self)


@native_node_type(registry, has_native_state=True)
class Consumer(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)
        inputs = context.inputs
        assert inputs['OtherService'] is inputs['implicit']
        assert inputs['RepeatedService'] is inputs['Service']
        assert inputs['child'] is inputs['Service']
        assert inputs['Service'].object.value == 7
        with checks.assertRaises(TypeError):
            inputs['Service'] = None
        with checks.assertRaises(KeyError):
            inputs['missing']

    def start_tasks(self, task_group, async_exit_stack):
        startup_inputs.append(self.inputs)

    @native_procedure('read')
    class Read(NativeProcedure):
        def __call__(self, *, context):
            state = context.inputs['state'].object
            assert {key: value for key, value in context.inputs.items() if key != 'state'} == dict(state.inputs)
            procedure_calls.append(('read', context.inputs['Service'].object))
            context.set_output('Count', context.inputs['Service'].object.value)

    @native_procedure('poll')
    class Poll(NativeProcedure):
        def __call__(self, *, context):
            assert context.inputs['Service'] is context.inputs['state'].object.inputs['Service']
            procedure_calls.append(('poll', context.inputs['Service'].object))


@native_node_type(registry, has_native_state=False)
class ProcedureConsumer:
    @native_procedure('read')
    class Read(NativeProcedure):
        def __call__(self, *, context):
            assert context.inputs['Service'] is context.inputs['child']
            assert isinstance(context.inputs['Value'], int)
            context.set_output('Count', context.inputs['Service'].object.value)


@native_node_type(registry, has_native_state=False)
class TypedProcedureConsumer(ProcedureConsumer):
    pass


@native_node_type(registry, has_native_state=True)
class Dependent(NativeState):
    pass


def native_registries():
    """Patch the test library with this test's native node and resource types."""
    stack = contextlib.ExitStack()
    stack.enter_context(patch.dict(wirely.native.test_library.native_node_types, registry))
    stack.enter_context(patch.dict(wirely.native.test_library.native_resource_types,
                                   {'Service': Service, 'ConsumerState': Consumer}))
    return stack


def compile_resources(source):
    with native_registries():
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
native_resource_program = program_compiler.compile_nocatch(compile_resources(source))
with native_registries():
    native_resource_engine = test_engine(native_resource_program)
consumer = native_resource_engine.get_native_state('consumer')
provider = native_resource_engine.get_native_state('factory.source')
shared = native_resource_engine.get_native_state('shared')
assert native_resource_engine.get_native_state('other_shared') is shared
assert consumer.inputs['Service'].object is provider
assert consumer.inputs['implicit'].object is shared
with checks.assertRaisesRegex(ValueError, 'can only be written by the native initializer'):
    provider.context.set_output('service', provider)
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
with native_registries():
    alias_engine = test_engine(program_compiler.compile_nocatch(alias_graph))
alias_inputs = alias_engine.get_native_state('sink').inputs
assert alias_inputs['service'].object is alias_engine.get_native_state('source')
# Each output supplies the object its provider wrote under the output's key.
assert alias_inputs['child'].object is alias_engine.get_native_state('sink.provider').named

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
with native_registries(), \
        checks.assertRaisesRegex(ValueError, r"did not write outputs: \['named'\]"):
    test_engine(program_compiler.compile_nocatch(incomplete_graph))


# Resource inputs are typed ResourceValues; their objects are checked
# against the Python type registered for the resource type.
service_value = consumer.inputs['Service']
assert isinstance(service_value, ResourceValue)
checks.assertEqual((service_value.type.name, service_value.type.python_type), ('TestLibrary.Service', Service))
with checks.assertRaises(dataclasses.FrozenInstanceError):
    service_value.object = None


@native_node_type(registry, has_native_state=True)
class WrongProvider(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('service', object())


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
with native_registries(), checks.assertRaisesRegex(
        ValueError, "Invalid value for output 'service' on source: Resource TestLibrary.Service requires a Service"):
    test_engine(program_compiler.compile_nocatch(wrong_graph))

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
with native_registries(), checks.assertRaisesRegex(ValueError, "Cannot find native resource type 'TestLibrary.Unknown'"):
    test_engine(program_compiler.compile_nocatch(unknown_graph))


# A resource declaration passes on its one source, like a temporary: a native
# node can hand its state to a child, and a composite node can name a child's
# resource.
alias_registry = {}
alias_reads = []


@native_node_type(alias_registry, has_native_state=True)
class AliasSource(NativeState, Service):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('service', self)


@native_node_type(alias_registry, has_native_state=True)
class AliasChild(NativeState):
    pass


@native_node_type(alias_registry, has_native_state=True)
class AliasHolder(NativeState, Service):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)


@native_node_type(alias_registry, has_native_state=False)
class AliasComposite:
    @native_procedure('read')
    class Read(NativeProcedure):
        def __call__(self, *, context):
            alias_reads.append(context.inputs['service'].object)


with patch.dict(wirely.native.test_library.native_node_types, alias_registry), \
        patch.dict(wirely.native.test_library.native_resource_types, {'Service': Service}):
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
    alias_resource_engine = test_engine(alias_program)
holder = alias_resource_engine.get_native_state('holder')
assert alias_resource_engine.get_native_state('holder.child').inputs['service'].object is holder
source_state = alias_resource_engine.get_native_state('composite.source')
assert alias_resource_engine.get_native_state('composite.child').inputs['service'].object is source_state
read = next(proc for schedule in alias_program.procs.values() for proc in schedule if proc.node_id == 'composite')
assert dict(read.resources) == {'service': wirely.program.ResourceBinding('composite.source', 'service')}
alias_resource_engine.step()
assert alias_reads == [source_state]


async def run_test():
    async with asyncio.TaskGroup() as task_group, contextlib.AsyncExitStack() as exit_stack:
        await native_resource_engine.start_tasks_async(task_group, exit_stack)
        assert startup_inputs == [consumer.inputs]
