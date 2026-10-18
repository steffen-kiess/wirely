from dataclasses import replace
import subprocess
import sys
from textwrap import dedent
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.compiler
from wirely.compiler import program as program_compiler
from wirely.program import ResourceBinding, RunStage
from wirely.runtime.engine import Engine
from wirely.test import test_extension, test_contracts, test_engine


checks = TestCase()

# Run stages shared by immutable programs cannot be changed through a member.
for stage in RunStage:
    with checks.assertRaises(AttributeError):
        stage._value_ = 99
    with checks.assertRaises(AttributeError):
        stage.extra = True
    with checks.assertRaises(AttributeError):
        del stage._name_
    checks.assertIs(RunStage(stage.value), stage)

# Runtime imports must work without loading the optional compiler dependencies.
subprocess.run([sys.executable, '-c', dedent('''
    import importlib
    import pkgutil
    import sys

    from wirely.program import Program
    from wirely.runtime.engine import Engine
    import wirely.native

    for module in pkgutil.iter_modules(wirely.native.__path__, 'wirely.native.'):
        importlib.import_module(module.name)
    unexpected = {name for name in sys.modules
                  if name == 'lark' or name.startswith(('lark.', 'wirely.compiler'))}
    assert not unexpected, unexpected
''')], check=True)


def check_compilation_without_native_implementations():
    source = '''namespace Uninstalled {
  node type Example {
    native procedure run;
  }
}
node example: Uninstalled.Example {}
'''
    with patch('wirely.runtime.linking.link_native_contracts',
               side_effect=AssertionError('Compilation looked up native code')):
        graph = wirely.compiler.compile_strs([('<uninstalled>', source)])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
    try:
        test_engine(program)
    except ValueError as error:
        assert 'Uninstalled.Example' in str(error), error
    else:
        raise AssertionError('Engine accepted an unavailable native implementation')


def check_engine_isolation():
    graph = wirely.compiler.compile_strs([('<engine-isolation>', '''node type Envelope {
  default input: value boolean;
  default output: value boolean;

  state delay: boolean = false;
  connect default input -> delay;
  connect delay.old -> default output;
}

singleton node type Shared {
  default output: value boolean;
  node constant: Std.Constant { Type: boolean, Value: true }
  connect constant -> default output;
}

node source: Std.DebugValueSource { Type: boolean }
node envelope: Envelope {}
connect source -> envelope;
node shared: Shared {}
node shared_again: Shared {}
''')], add_std=True)
    assert graph is not None
    program = replace(program_compiler.compile_nocatch(graph), graph=None)
    first = test_engine(program, graph)
    second = test_engine(program, graph)
    first.get_native_state('source').set_value(True)
    second.get_native_state('source').set_value(False)
    first.step()
    second.step()
    first.assert_value_is('envelope', False)
    second.assert_value_is('envelope', False)
    first.step()
    first.assert_value_is('envelope', True)
    second.assert_is_idle()
    second.assert_value_is('envelope', False)
    for runtime in (first, second):
        runtime.assert_value_is('shared', True)
        runtime.assert_value_is('shared_again', True)


def check_resource_isolation():
    source = '''namespace TestLibrary {
  resource type Service;
  resource type ConsumerState;
  singleton native node type Provider {
    default output: resource Service;
    native initializer produces default output as "service";
  }
  native node type Consumer {
    input Service: resource Service;
    binding state: resource ConsumerState;
    default output: value uint8;
    native initializer depends on Service as service produces state as "state";
    native procedure run depends on state as "state" produces default output as output;
  }
}
node provider: TestLibrary.Provider {}
node alias: TestLibrary.Provider {}
node consumer: TestLibrary.Consumer {}
connect alias -> consumer.Service;
'''
    graph = wirely.compiler.compile_strs([('<resource-isolation>', source)])
    assert graph is not None
    program = replace(program_compiler.compile_nocatch(graph), graph=None)
    with test_contracts(graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class Provider(contracts.ProviderInitializer):
            def initialize(self, *, services):
                return contracts.ProviderInitializerResult(service=self)
        registry['initializers']['TestLibrary.Provider'] = Provider

        class Consumer(contracts.ConsumerInitializer):
            def initialize(self, *, services, service):
                self.service = service
                self.count = 0
                return contracts.ConsumerInitializerResult(state=self)

            class Run(contracts.ConsumerRun):
                def run(self, *, services, state):
                    state.count += 1
                    return contracts.ConsumerRunResult(output=wirely.UInt8(state.count))
        registry['initializers']['TestLibrary.Consumer'] = Consumer
        registry['procedures']['TestLibrary.Consumer:run'] = Consumer.Run

        resource_types = {'TestLibrary.Service': Provider, 'TestLibrary.ConsumerState': Consumer}
        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources=resource_types, contracts=contracts)
        first = Engine(program, override_extensions=[bindings, resource_bindings])
        second = Engine(program, override_extensions=[bindings, resource_bindings])
    for runtime in (first, second):
        provider = runtime.get_native_state('provider')
        assert provider is runtime.get_native_state('alias')
        assert runtime.get_native_state('consumer').service is provider
    assert first.get_native_state('provider') is not second.get_native_state('provider')
    first.step()
    first.trigger('consumer', 'run')
    first.step()
    first.assert_value_is('consumer', 2)
    second.step()
    second.assert_value_is('consumer', 1)


def check_resource_binding_validation():
    graph = wirely.compiler.compile_strs([('<resource-validation>', '''namespace TestLibrary {
  resource type Service;
  resource type Other;
  native node type ServiceProvider {
    default output: resource Service;
    native initializer produces default output as "service";
  }
  native node type OtherProvider {
    default output: resource Other;
    native initializer produces default output as "other";
  }
  native node type Consumer {
    input Service: resource Service;
    native initializer depends on Service;
  }
}
node service: TestLibrary.ServiceProvider {}
node other: TestLibrary.OtherProvider {}
node consumer: TestLibrary.Consumer {}
connect service -> consumer.Service;
''')])
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    consumer = program.instances['consumer']
    for binding, message in ((ResourceBinding('service', 'missing'), 'unknown output service.missing'),
                             (ResourceBinding('other', 'other'), 'of type TestLibrary.Service is bound to other.other')):
        instances = {**program.instances, 'consumer': replace(consumer, initializer_bindings={'Service': binding})}
        with checks.assertRaisesRegex(ValueError, message):
            test_engine(replace(program, instances=instances))


def check_native_names():
    # Native names replace qualified names in native interfaces and linking.
    source = '''namespace Renamed {
  resource type Link as "TestLibrary.Service";
  native node type Source as "TestLibrary.Provider" {
    default output: resource Link;
    native initializer produces default output as "service";
  }
  node type Sink {
    input Service: resource Link;
    default output: value uint8;
    native procedure run as "TestLibrary.Consumer:run" depends on Service produces default output as "output";
  }
}
node source: Renamed.Source {}
node sink: Renamed.Sink {}
connect source -> sink.Service;
'''
    graph = wirely.compiler.compile_strs([('<native-names>', source)])
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    with test_contracts(graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class Provider(contracts.ProviderInitializer):
            def initialize(self, *, services):
                return contracts.ProviderInitializerResult(service=self)
        registry['initializers']['TestLibrary.Provider'] = Provider

        class Consumer:
            class Run(contracts.ConsumerRun):
                def run(self, *, services, service):
                    return contracts.ConsumerRunResult(output=wirely.UInt8(7))
        registry['procedures']['TestLibrary.Consumer:run'] = Consumer.Run

        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources={'TestLibrary.Service': Provider}, contracts=contracts)
        engine = Engine(program, override_extensions=[bindings, resource_bindings])
    assert program.instances['source'].type_name == 'TestLibrary.Provider'
    assert program.instances['sink'].type_name is None
    assert set(program.native_types) == {'TestLibrary.Provider'}
    assert program.native_types['TestLibrary.Provider'].resource_outputs == {'service': 'TestLibrary.Service'}
    assert set(program.native_procedures) == {'TestLibrary.Consumer:run'}
    assert program.native_procedures['TestLibrary.Consumer:run'].resources == {'Service': 'TestLibrary.Service'}
    engine.step()
    engine.assert_value_is('sink', 7)


check_compilation_without_native_implementations()
check_engine_isolation()
check_resource_isolation()
check_resource_binding_validation()
check_native_names()


async def run_test():
    pass
