from dataclasses import replace
import datetime
import subprocess
import sys
from textwrap import dedent
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.compiler
from wirely.compiler import program as program_compiler
from wirely.program import InputSource, NativeProcedureEntry, RunStage, Variable, VariableBank, validate_program
from wirely.runtime.linking import link_native_signatures
from wirely.runtime.engine import Engine
from tests.infra.support import test_extension, test_contracts, test_engine


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
  singleton node type Provider {
    default output: fixed Service;
    native initializer create writes default output as "service";
  }
  node type Consumer {
    input Service: fixed Service;
    binding state: fixed ConsumerState;
    default output: value uint8;
    native initializer create reads Service as service writes state as "state";
    native procedure run reads state as "state" writes default output as output;
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

        class Provider(contracts.ProviderCreate):
            def initialize(self, *, services):
                return contracts.ProviderCreateResult(service=self)
        registry['initializers']['TestLibrary.Provider:create'] = Provider

        class Consumer(contracts.ConsumerCreate):
            def initialize(self, *, services, service):
                self.service = service
                self.count = 0
                return contracts.ConsumerCreateResult(state=self)

            class Run(contracts.ConsumerRun):
                def run(self, *, services, state):
                    state.count += 1
                    return contracts.ConsumerRunResult(output=wirely.UInt8(state.count))
        registry['initializers']['TestLibrary.Consumer:create'] = Consumer
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
  node type ServiceProvider {
    default output: fixed Service;
    native initializer create writes default output as "service";
  }
  node type OtherProvider {
    default output: fixed Other;
    native initializer create writes default output as "other";
  }
  node type Consumer {
    input Service: fixed Service;
    native initializer create reads Service;
  }
}
node service: TestLibrary.ServiceProvider {}
node other: TestLibrary.OtherProvider {}
node consumer: TestLibrary.Consumer {}
connect service -> consumer.Service;
''')])
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    schedule = program.procs[RunStage.LOAD]
    consumer = next(proc for proc in schedule if proc.name == 'consumer:create')
    resources = {variable.var_type.native_name: variable for variable in program.fixed_values.variables.values()
                 if variable.is_resource}
    service, other = resources['TestLibrary.Service'], resources['TestLibrary.Other']
    unknown = Variable('missing', service.var_type)

    def with_source(variable):
        modified = replace(consumer, inputs=(InputSource(VariableBank.FIXED_VALUES, variable),))
        procs = {**program.procs, RunStage.LOAD: tuple(proc for proc in schedule if proc is not consumer) + (modified,)}
        return replace(program, procs=procs)

    with checks.assertRaisesRegex(ValueError, 'not in its declared bank'):
        validate_program(with_source(unknown))
    # Resource types are checked when linking.
    with test_contracts(graph) as contracts:
        names = ('TestLibrary.ServiceProvider:create', 'TestLibrary.OtherProvider:create',
                 'TestLibrary.Consumer:create')
        extension = test_extension(initializers={name: object for name in names}, contracts=contracts)
        with checks.assertRaisesRegex(ValueError, 'expected resource `TestLibrary.Service`'):
            link_native_signatures(with_source(other), extension)


def check_native_names():
    # Native names replace qualified names in native interfaces and linking.
    source = '''namespace Renamed {
  resource type Link as "TestLibrary.Service";
  node type Source as "TestLibrary.Provider" {
    default output: fixed Link;
    native initializer create writes default output as "service";
  }
  node type Sink {
    input Service: fixed Link;
    default output: value uint8;
    native procedure run as "TestLibrary.Consumer:run" reads Service writes default output as "output";
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

        class Provider(contracts.ProviderCreate):
            def initialize(self, *, services):
                return contracts.ProviderCreateResult(service=self)
        registry['initializers']['TestLibrary.Provider:create'] = Provider

        class Consumer:
            class Run(contracts.ConsumerRun):
                def run(self, *, services, service):
                    return contracts.ConsumerRunResult(output=wirely.UInt8(7))
        registry['procedures']['TestLibrary.Consumer:run'] = Consumer.Run

        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources={'TestLibrary.Service': Provider}, contracts=contracts)
        engine = Engine(program, override_extensions=[bindings, resource_bindings])
    initializer = next(proc for proc in program.procs[RunStage.LOAD] if proc.node == 'source')
    assert initializer.native == NativeProcedureEntry('TestLibrary.Provider:create', (), ('service',), True)
    assert program.native_procedures == (
        NativeProcedureEntry('TestLibrary.Consumer:run', ('Service',), ('output',)),
        initializer.native,
    )
    engine.step()
    engine.assert_value_is('sink', 7)


def check_monotonic_clock():
    graph = wirely.compiler.compile_strs([('<clock>', '')], add_std=False)
    program = program_compiler.compile_nocatch(graph)
    runtime = test_engine(program)
    other = test_engine(program)
    with checks.assertRaisesRegex(ValueError, 'Fixed monotonic time'):
        runtime.advance_fake_monotonic_time_generation()

    before = wirely.MonotonicTime.current_time()
    now = runtime.get_monotonic_time()
    after = wirely.MonotonicTime.current_time()
    assert before.generation == now.generation == after.generation
    assert before.ticks <= now.ticks <= after.ticks
    offset = wirely.Duration.from_seconds(5)
    runtime.advance_fake_monotonic_time(offset)
    before = wirely.MonotonicTime.current_time()
    now = runtime.get_monotonic_time()
    after = wirely.MonotonicTime.current_time()
    assert (before + offset).ticks <= now.ticks <= (after + offset).ticks

    runtime.enable_fixed_monotonic_time()
    fixed = runtime.get_monotonic_time()
    assert fixed == wirely.MonotonicTime(generation=after.generation + 1, ticks=offset.ticks)
    runtime.enable_fixed_monotonic_time()
    runtime.step()
    assert runtime.get_monotonic_time() == fixed
    runtime.advance_fake_monotonic_time(wirely.Duration(ticks=1))
    advanced = runtime.get_monotonic_time()
    assert advanced == fixed + wirely.Duration(ticks=1)
    runtime.advance_fake_monotonic_time_generation()
    assert runtime.get_monotonic_time() == wirely.MonotonicTime(
        generation=fixed.generation + 1, ticks=advanced.ticks)
    runtime.enable_fixed_monotonic_time()
    assert runtime.get_monotonic_time().generation == fixed.generation + 1

    # Another engine still uses the real clock, and starts fixed time at zero.
    before = wirely.MonotonicTime.current_time()
    now = other.get_monotonic_time()
    after = wirely.MonotonicTime.current_time()
    assert before.generation == now.generation == after.generation
    assert before.ticks <= now.ticks <= after.ticks
    other.enable_fixed_monotonic_time()
    assert other.get_monotonic_time() == wirely.MonotonicTime(generation=fixed.generation, ticks=0)
    other.advance_fake_monotonic_time(wirely.Duration(ticks=3))
    assert runtime.get_monotonic_time().ticks == advanced.ticks
    assert other.get_monotonic_time().ticks == 3


def check_wall_clock():
    graph = wirely.compiler.compile_strs([('<wall-clock>', '')], add_std=False)
    program = program_compiler.compile_nocatch(graph)
    runtime = test_engine(program)
    other = test_engine(program)
    fixed = datetime.datetime(2026, 1, 1, 14, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    runtime.triggered_procs_event.clear()
    runtime.set_fake_wall_time(fixed)
    assert runtime.triggered_procs_event.is_set()
    assert runtime.get_wall_time() == fixed
    runtime.step()
    assert runtime.get_wall_time() == fixed
    runtime.enable_fixed_monotonic_time()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(60))
    assert runtime.get_wall_time() == fixed

    before = datetime.datetime.now(datetime.timezone.utc)
    now = other.get_wall_time()
    after = datetime.datetime.now(datetime.timezone.utc)
    assert now.utcoffset() is not None
    assert before <= now <= after
    runtime.triggered_procs_event.clear()
    runtime.set_fake_wall_time(fixed + datetime.timedelta(seconds=1))
    assert runtime.get_wall_time().isoformat() == '2026-01-01T14:00:01+02:00'
    assert runtime.triggered_procs_event.is_set()
    runtime.clear_fake_wall_time()
    before = datetime.datetime.now(datetime.timezone.utc)
    now = runtime.get_wall_time()
    after = datetime.datetime.now(datetime.timezone.utc)
    assert before <= now <= after
    assert runtime.triggered_procs_event.is_set()
    runtime.set_fake_wall_time(None)
    assert runtime.get_wall_time() is None
    snapshot = runtime.step()[2]
    assert all(time is None for time in snapshot.stage_start_time.values())
    runtime.advance_fake_monotonic_time(wirely.Duration(ticks=1))
    assert runtime.get_wall_time() is None
    with checks.assertRaisesRegex(ValueError, 'timezone-aware'):
        runtime.set_fake_wall_time(datetime.datetime(2026, 1, 1))


check_compilation_without_native_implementations()
check_engine_isolation()
check_resource_isolation()
check_resource_binding_validation()
check_native_names()
check_monotonic_clock()
check_wall_clock()


async def run_test():
    pass
