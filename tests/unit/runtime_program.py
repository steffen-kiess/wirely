from dataclasses import replace
import datetime
import io
import subprocess
import sys
from textwrap import dedent
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.compiler
from wirely.compiler import program as program_compiler
from wirely.program import InputSource, NativeProcedureEntry, RunStage, Variable, VariableBank, validate_program
from wirely.program.loader import load_program
from wirely.program.writer import write_program
from wirely.program.program import procedure_instances
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

node source: Std.DebugValueSource { Type: boolean, Initial: false }
node envelope: Envelope {}
connect source -> envelope;
node shared: Shared {}
node shared_again: Shared {}
''')], add_std=True)
    assert graph is not None
    program = replace(program_compiler.compile_nocatch(graph), graph=None)
    first = test_engine(program, graph)
    second = test_engine(program, graph)
    first.get_native_state('source.create').set_value(True)
    second.get_native_state('source.create').set_value(False)
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
        provider = runtime.get_native_state('TestLibrary.Provider:create')
        aliases = runtime.program.symbols.node_aliases
        assert aliases['provider'] == aliases['alias']
        assert runtime.get_native_state('consumer.create').service is provider
    assert (first.get_native_state('TestLibrary.Provider:create')
            is not second.get_native_state('TestLibrary.Provider:create'))
    first.step()
    first.trigger('consumer.run')
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
    consumer = next(proc for proc in schedule if proc.name == 'consumer.create')
    resources = {variable.var_type.native_name: variable for variable in program.fixed_values.variables
                 if variable.is_resource}
    service, other = resources['TestLibrary.Service'], resources['TestLibrary.Other']
    unknown = Variable(service.var_type, name='missing')

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


def check_instance_names():
    graph = wirely.compiler.compile_strs([('<instance-names>', '''
node type Counter {
  state count: uint8 = 0;
  procedure run writes count { count = 1; }
}
node type Container {
  node child: Counter {}
}
namespace First {
  singleton node type Shared {
    state count: boolean = false;
    default output: value boolean;
    binding value: value boolean;
    node source: Std.DebugValueSource { Type: boolean, Initial: false }
    node child: Counter {}
    connect source -> value;
    connect value -> default output;
    procedure run writes count { count = true; }
  }
}
namespace Second {
  singleton node type Shared {
    node source: Std.DebugValueSource { Type: boolean, Initial: false }
  }
}
node ordinary: Container {}
node first: First.Shared {}
node repeated: First.Shared {}
node second: Second.Shared {}
state count: boolean = false;
procedure run writes count { count = true; }
''')], add_std=True)
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    runtime = test_engine(program, graph)
    first = runtime.get_native_state('First.Shared:source.create')
    second = runtime.get_native_state('Second.Shared:source.create')
    assert first is not second
    assert first.services.name == 'First.Shared:source'
    for alias in ('first.source', 'repeated.source', 'First.Shared:source'):
        assert runtime.get_variable(alias) is runtime.get_variable('First.Shared:value')
    assert runtime.get_variable('ordinary.child.count').name == 'ordinary.child.count'
    assert runtime.get_variable('First.Shared:count').name == 'First.Shared:count'
    assert runtime.get_variable('first.child.count').name == 'First.Shared:child.count'
    assert runtime.get_variable('count').name == 'count'
    for name in ('first.source.create', 'ordinary.child.run', 'missing'):
        with checks.assertRaisesRegex(ValueError, 'Unknown initializer name'):
            runtime.get_native_state(name)
    for name in ('First.Shared:source.create', 'ordinary:child:run', 'missing'):
        with checks.assertRaisesRegex(ValueError, 'Unknown procedure name'):
            runtime.trigger(name)
    first.set_value(True)
    runtime.step()
    runtime.assert_value_is('repeated.value', True)
    runtime.assert_value_is('First.Shared:value', True)
    for name in ('run', 'ordinary.child.run', 'First.Shared:run', 'First.Shared:child.run'):
        runtime.trigger(name)
    assert runtime.step()[0] == 4
    runtime.assert_value_is('count', True)
    runtime.assert_value_is('ordinary.child.count', 1)
    runtime.assert_value_is('first.count', True)
    runtime.assert_value_is('First.Shared:child.count', 1)


def check_unnamed_initializers():
    graph = wirely.compiler.compile_strs([('<unnamed-initializers>', '''
node first: Std.DebugValueSource { Type: boolean, Initial: false }
node second: Std.DebugValueSource { Type: boolean, Initial: false }
state seen: boolean = false;
connect first -> seen;
''')], add_std=True)
    assert graph is not None
    data = write_program(program_compiler.compile_nocatch(graph))
    loaded = load_program(data)
    assert write_program(loaded) == data
    instances = procedure_instances(loaded)
    assert all(proc.name is None and proc.node is None for proc in instances)
    assert all(var.name is None for table in (loaded.state_variables, loaded.temporaries, loaded.fixed_values)
               for var in table.variables)
    runtime = Engine(loaded)
    first, second = loaded.procs[RunStage.LOAD]
    readers = loaded.procs[RunStage.INPUT]
    assert runtime.get_native_state(first) is not runtime.get_native_state(second)
    runtime.step()
    assert runtime.step()[0] == 0
    runtime.get_native_state(first).set_value(True)
    assert runtime.step()[0] == 2
    state, = loaded.state_variables.variables
    assert runtime.state[state] is True
    values = [runtime.cache[proc.outputs[0].variable] for proc in readers]
    assert values == [True, False]
    runtime.get_native_state(second).set_value(True)
    assert runtime.step()[0] == 1
    assert all(runtime.cache[proc.outputs[0].variable] for proc in readers)
    assert runtime.get_native_state(first).services.name == 'procedure[0]'
    with checks.assertRaisesRegex(ValueError, 'Unknown initializer instance'):
        runtime.get_native_state(replace(first))
    with checks.assertRaisesRegex(ValueError, 'Unknown initializer instance'):
        runtime.get_native_state(readers[0])
    with checks.assertRaisesRegex(ValueError, 'Unknown initializer name'):
        runtime.get_native_state('procedure[0]')
    with checks.assertRaisesRegex(ValueError, 'Unknown procedure instance'):
        runtime.trigger(first)
    with checks.assertRaisesRegex(ValueError, 'Unknown procedure instance'):
        runtime.trigger(replace(readers[0]))
    with checks.assertRaisesRegex(ValueError, 'Unknown procedure name'):
        runtime.trigger('procedure[2]')
    runtime.trigger(readers[0])
    assert runtime.step()[0] == 1
    duplicate_names = replace(loaded, procs={stage: tuple(replace(proc, name='same') for proc in schedule)
                                           for stage, schedule in loaded.procs.items()})
    other = Engine(duplicate_names)
    with checks.assertRaisesRegex(ValueError, 'Ambiguous initializer name'):
        other.get_native_state('same')
    with checks.assertRaisesRegex(ValueError, 'Ambiguous procedure name'):
        other.trigger('same')
    output = io.StringIO()
    loaded.dump(output)
    assert 'temporary[0]' in output.getvalue() and 'fixed[0]' in output.getvalue()
    assert 'procedure[0]' in output.getvalue() and 'None' not in output.getvalue()


def check_unnamed_scheduling():
    graph = wirely.compiler.compile_strs([('<unnamed-scheduling>', '''
namespace TestLibrary {
  node type Probe {
    native procedure run;
  }
}
node first: TestLibrary.Probe {}
node second: TestLibrary.Probe {}
''')])
    assert graph is not None
    compiled = program_compiler.compile_nocatch(graph)
    loaded = load_program(write_program(compiled))
    first, second = loaded.procs[RunStage.DEFAULT]
    assert first is not second and first != second
    assert first.native is second.native and first.inputs == second.inputs == ()
    probes = []
    base = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    with test_contracts(graph) as contracts:
        class ProbeRun(contracts.ProbeRun):
            def prepare(self, *, services):
                self.index = len(probes)
                self.calls = 0
                probes.append(self)

            def run(self, *, services):
                self.calls += 1
                if self.calls == 1:
                    self.deadline = services.start_time_for_stage(RunStage.DEFAULT).monotonic + wirely.Duration.from_seconds(3)
                    services.trigger_self()
                if self.calls <= 2:
                    if self.index == 0:
                        services.set_wakeup_time(self.deadline)
                    else:
                        services.set_wall_wakeup_time(base, base + datetime.timedelta(minutes=1), utc_offset=None)
                return contracts.ProbeRunResult()

        extension = test_extension(procedures={'TestLibrary.Probe:run': ProbeRun}, contracts=contracts)
        runtime = Engine(loaded, override_extensions=[extension])
    runtime.enable_fixed_monotonic_time()
    runtime.set_fake_wall_time(base)
    assert runtime.step()[0] == 2
    assert runtime.step()[0] == 2
    assert runtime.step()[0] == 0
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(4))
    assert runtime.step()[0] == 1
    assert [probe.calls for probe in probes] == [3, 2]
    runtime.set_fake_wall_time(base + datetime.timedelta(minutes=1))
    assert runtime.step()[0] == 1
    assert [probe.calls for probe in probes] == [3, 3]
    assert runtime.step()[0] == 0
    runtime.trigger(second)
    assert runtime.step()[0] == 1
    assert [probe.calls for probe in probes] == [3, 4]
    with checks.assertRaisesRegex(ValueError, 'scheduled more than once'):
        validate_program(replace(loaded, procs={RunStage.DEFAULT: (first, first)}))


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
check_instance_names()
check_unnamed_initializers()
check_unnamed_scheduling()
check_monotonic_clock()
check_wall_clock()


async def run_test():
    pass
