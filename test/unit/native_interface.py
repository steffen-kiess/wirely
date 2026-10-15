import asyncio
import contextlib
import datetime
from pathlib import Path
from textwrap import indent
from unittest import TestCase
from unittest.mock import patch

from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_node_type
from wirely.program import RunStage
from wirely.runtime.contexts import NativeState
from wirely.runtime.engine import Engine


checks = TestCase()
std_source = Path('wirely/lib/std.wrl').read_text()


def make_engine(declaration, callback, *, extra='', parameters='', singleton=False):
    registry = {}

    @native_node_type(registry, has_native_state=False)
    class Probe:
        @staticmethod
        def run(*, context):
            callback(context)

    qualifier = 'singleton ' if singleton else ''
    source = f'''
namespace TestLibrary {{
  {qualifier}node type Probe {{
{indent(declaration.strip(), "    ")}
  }}
}}

node probe: TestLibrary.Probe {{ {parameters} }}
{extra}
'''
    with patch.dict(wirely.native.test_library.native_node_types, registry):
        graph = wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', std_source),
            ('<native-interface>', source),
        ])
        assert graph is not None
        program = program_compiler.compile(graph)
        assert program is not None
        return Engine(program)


def check_input_ports():
    seen = []

    def check_inputs(context):
        assert dict(context.inputs) == {'value': 7, 'named': 7}
        with checks.assertRaises(TypeError):
            context.inputs['value'] = 8
        with checks.assertRaises(KeyError):
            context.inputs['missing']
        with checks.assertRaises(AttributeError):
            context.name = 'other'
        assert context.name == 'probe'
        seen.append(context.inputs)
        context.set_output('output', context.inputs['value'])

    probe = make_engine('''
    default input: value uint8;
    input named: value uint8;
    default output: value uint8;

    native procedure run
      depends on default input as "value", named
      produces default output as "output";
    ''', check_inputs, extra='''
    node source: Std.Constant { Type: uint8, Value: 7 }
    connect source -> probe;
    connect source -> probe.named;
    ''')
    probe.step()
    probe.assert_value_is('probe', 7)
    probe.assert_is_idle()
    assert len(seen) == 1


def check_wakeups():
    def check_wakeup(context, deadline):
        assert context.wakeup_time is None
        with checks.assertRaises(ValueError):
            context.set_wakeup_time('soon')
        context.set_wakeup_time(deadline)
        assert context.wakeup_time == deadline
        with checks.assertRaisesRegex(ValueError, 'already set'):
            context.set_wakeup_time(None)

    for deadline in [None, wirely.MonotonicTime(generation=1, ticks=100),
                     wirely.MonotonicTime(generation=0, ticks=0), wirely.MonotonicTime(generation=2, ticks=100)]:
        wakeup_probe = make_engine(
            'native procedure run;',
            lambda context, deadline=deadline: check_wakeup(context, deadline),
        )
        with patch.object(wirely.MonotonicTime, 'current_time', return_value=wirely.MonotonicTime(generation=1, ticks=0)):
            wakeup_probe.step()
        if deadline is None:
            assert wakeup_probe.next_wakeup is None
        else:
            # A wakeup from a non-current generation is due now.
            expected = deadline if deadline.generation == 1 else wirely.MonotonicTime(generation=1, ticks=0)
            assert wakeup_probe.next_wakeup == expected

    for explicit_cancellation in [False, True]:
        wakeup_calls = []
        deadline = wirely.MonotonicTime(generation=1, ticks=10)

        def schedule_wakeup(context):
            assert context.wakeup_time is None
            wakeup_calls.append(context)
            if len(wakeup_calls) == 1:
                context.set_wakeup_time(deadline)
            elif explicit_cancellation:
                context.set_wakeup_time(None)

        wakeup_probe = make_engine('native procedure run;', schedule_wakeup)
        with patch.object(wirely.MonotonicTime, 'current_time', return_value=wirely.MonotonicTime(generation=1, ticks=0)):
            wakeup_probe.step()
            assert wakeup_probe.next_wakeup == deadline
            wakeup_probe.step()
            assert len(wakeup_calls) == 1
            assert wakeup_probe.next_wakeup == deadline
        with patch.object(wirely.MonotonicTime, 'current_time', return_value=deadline):
            wakeup_probe.step()
            assert len(wakeup_calls) == 2
            assert wakeup_probe.next_wakeup is None
            wakeup_probe.step()
            assert len(wakeup_calls) == 2
            wakeup_probe.assert_is_idle()


def check_context_names():
    named_contexts = []

    def check_node_name(context):
        with checks.assertRaisesRegex(ValueError, f'on {context.name}$'):
            context.set_output('missing', None)
        named_contexts.append(context.name)

    named_engine = make_engine('native procedure run;', check_node_name, extra='''
    node type Outer {
      node child: TestLibrary.Probe {}
    }
    node outer: Outer {}
    namespace TestLibrary {
      singleton node type SharedContainer {
        node child: Probe {}
      }
    }
    node shared: TestLibrary.SharedContainer {}
    node shared_again: TestLibrary.SharedContainer {}
    ''')
    named_engine.step()
    assert sorted(named_contexts) == ['TestLibrary.SharedContainer.child', 'outer.child', 'probe']
    named_contexts.clear()
    named_engine = make_engine('native procedure run;', check_node_name, singleton=True)
    named_engine.step()
    assert named_contexts == ['TestLibrary.Probe']


def check_stage_times():
    clock_registry = {}
    clock_methods = {}
    clock_declarations = []
    stage_snapshots = {}
    clock_contexts = []
    stages = list(RunStage)
    monotonic_times = [wirely.MonotonicTime(generation=7, ticks=index * wirely.types.time.TICKS_PER_SECOND)
                       for index in range(1, 4)]
    utc_times = [datetime.datetime(2026, 1, 1, 12, 0, index,
                                   tzinfo=datetime.timezone.utc)
                 for index in range(3)]
    clock_offset = wirely.Duration.from_seconds(5)

    def check_stage_time(context, stage):
        snapshot = context.start_time_for_stage(stage)
        index = stages.index(stage)
        assert snapshot.monotonic == monotonic_times[index] + clock_offset
        assert snapshot.utc == utc_times[index]
        assert snapshot.utc.tzinfo is datetime.timezone.utc
        assert snapshot.local.isoformat() == snapshot.utc.astimezone().isoformat()
        assert snapshot.local.tzinfo is not None
        with checks.assertRaises(AttributeError):
            snapshot.utc = None
        if stage in stage_snapshots:
            assert stage_snapshots[stage] == snapshot
        else:
            stage_snapshots[stage] = snapshot
        for candidate in stages:
            if stages.index(candidate) <= index:
                assert context.start_time_for_stage(candidate) == stage_snapshots[candidate]
            else:
                with checks.assertRaises(KeyError):
                    context.start_time_for_stage(candidate)
        with checks.assertRaises(ValueError):
            context.start_time_for_stage('DEFAULT')
        clock_contexts.append(context)

    for stage in stages:
        for index in range(2):
            def record_time(*, context, stage=stage):
                check_stage_time(context, stage)

            name = f'{stage.name.lower()}_{index}'
            clock_methods[name] = staticmethod(record_time)
            stage_clause = '' if stage is RunStage.DEFAULT else f' stage {stage.name.lower()}'
            clock_declarations.append(f'native procedure {name}{stage_clause};')

    native_node_type(clock_registry, has_native_state=False)(
        type('ClockProbe', (), clock_methods),
    )
    clock_source = '''namespace TestLibrary {
      node type ClockProbe {
    ''' + '\n'.join(f'    {line}' for line in clock_declarations) + '''
      }
    }
    node probe: TestLibrary.ClockProbe {}
    '''
    with patch.dict(wirely.native.test_library.native_node_types, clock_registry):
        graph = wirely.compiler.compile_strs([('<stage-times>', clock_source)])
        assert graph is not None
        program = program_compiler.compile(graph)
        assert program is not None
        clock_engine = Engine(program)
    clock_engine.fake_monotonic_time_offset = clock_offset

    class ClockDateTime(datetime.datetime):
        timestamps = iter(utc_times)

        @classmethod
        def now(cls, tz=None):
            assert tz is datetime.timezone.utc
            return next(cls.timestamps)

    with patch.object(wirely.MonotonicTime, 'current_time', side_effect=monotonic_times), \
            patch('wirely.runtime.contexts.datetime.datetime', ClockDateTime):
        clock_engine.step()
    assert len(clock_contexts) == 6
    clock_engine.step()
    assert len(clock_contexts) == 6


def check_triggers():
    trigger_registry = {}

    @native_node_type(trigger_registry, has_native_state=True)
    class TriggerProbe(NativeState):
        def __init__(self, *, context):
            super().__init__(context=context)
            assert not context.values and not context.inputs
            with checks.assertRaises(AttributeError):
                context.name = 'other'
            context.set_output('refresher', self)
            context.set_output('runner', self)
            self.calls = []
            self.reschedule_once = False
            self.trigger('refresher')
            assert not self.calls

        @staticmethod
        def refresh(*, context):
            state = context.inputs['state'].object
            assert context.name == state.context.name
            state.calls.append('refresh')
            if state.reschedule_once:
                state.reschedule_once = False
                context.trigger_self()

        @staticmethod
        def run(*, context):
            context.inputs['state'].object.calls.append('run')

        @staticmethod
        def log(*, context):
            context.inputs['state'].object.calls.append('log')

    @native_node_type(trigger_registry, has_native_state=True)
    class SharedTriggerProbe(TriggerProbe):
        pass

    @native_node_type(trigger_registry, has_native_state=True)
    class Provider(NativeState):
        def __init__(self, *, context):
            super().__init__(context=context)
            context.set_output('service', self)

    @native_node_type(trigger_registry, has_native_state=False)
    class Reader:
        calls = []

        @staticmethod
        def poll(*, context):
            Reader.calls.append(context.name)

    probe_body = '''
        binding refresher: resource ProbeState;
        binding runner: resource ProbeState;
        native initializer produces refresher as "refresher", runner as "runner";
        native procedure refresh depends on refresher as "state";
        native procedure run depends on runner as "state";
        native procedure log depends on runner as "state";
    '''
    with patch.dict(wirely.native.test_library.native_node_types, trigger_registry):
        trigger_graph = wirely.compiler.compile_strs([('<native-triggers>', f'''
    namespace TestLibrary {{
      resource type ProbeState;
      resource type Service;
      native node type TriggerProbe {{{probe_body}}}
      singleton native node type SharedTriggerProbe {{{probe_body}}}
      native node type Provider {{
        default output: resource Service;
        native initializer produces default output as "service";
      }}
      node type Reader {{
        input Service: resource Service;
        native procedure poll depends on Service as "service";
      }}
    }}
    node first: TestLibrary.TriggerProbe {{}}
    node second: TestLibrary.TriggerProbe {{}}
    node shared: TestLibrary.SharedTriggerProbe {{}}
    node shared_again: TestLibrary.SharedTriggerProbe {{}}
    node provider: TestLibrary.Provider {{}}
    node other_provider: TestLibrary.Provider {{}}
    node reader: TestLibrary.Reader {{}}
    node another_reader: TestLibrary.Reader {{}}
    node other_reader: TestLibrary.Reader {{}}
    connect provider -> reader.Service;
    connect provider -> another_reader.Service;
    connect other_provider -> other_reader.Service;
    ''')])
    assert trigger_graph is not None
    trigger_program = program_compiler.compile(trigger_graph)
    assert trigger_program is not None
    resource_types = {'ProbeState': TriggerProbe, 'Service': Provider}
    with patch.dict(wirely.native.test_library.native_node_types, trigger_registry), \
            patch.dict(wirely.native.test_library.native_resource_types, resource_types):
        trigger_engine = Engine(trigger_program)
    first = trigger_engine.get_native_state('first')
    second = trigger_engine.get_native_state('second')
    shared = trigger_engine.get_native_state('shared')
    assert first.context.name == 'first'
    assert second.context.name == 'second'
    assert shared.context.name == 'TestLibrary.SharedTriggerProbe'
    assert shared is trigger_engine.get_native_state('shared_again')
    assert not first.calls and not second.calls and not shared.calls
    trigger_engine.step()
    for state in [first, second, shared]:
        checks.assertCountEqual(state.calls, ['refresh', 'run', 'log'])
    first_startup = first.calls.copy()
    second_startup = second.calls.copy()
    shared_startup = shared.calls.copy()
    Reader.calls.clear()
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    with checks.assertRaisesRegex(ValueError, "Unknown output name 'missing'"):
        first.trigger('missing')
    trigger_engine.step()
    assert first.calls == first_startup

    # A trigger reruns the procedures depending on the output, once per step.
    first.trigger('refresher')
    first.trigger('refresher')
    assert first.calls == first_startup
    trigger_engine.step()
    assert first.calls == first_startup + ['refresh']
    assert second.calls == second_startup
    assert shared.calls == shared_startup
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    first.reschedule_once = True
    first.trigger('refresher')
    trigger_engine.step()
    assert first.calls == first_startup + ['refresh', 'refresh']
    trigger_engine.step()
    assert first.calls == first_startup + ['refresh', 'refresh', 'refresh']
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    second.trigger('runner')
    shared.trigger('refresher')
    trigger_engine.step()
    checks.assertCountEqual(second.calls, second_startup + ['run', 'log'])
    assert shared.calls == shared_startup + ['refresh']
    assert len(first.calls) == 6
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    # Dependents in other nodes rerun too.
    trigger_engine.get_native_state('provider').trigger('service')
    trigger_engine.step()
    checks.assertCountEqual(Reader.calls, ['reader', 'another_reader'])
    trigger_engine.assert_is_idle()


def check_typed_ports():
    # Typed ports receive their sources through connections.
    snapshots = []

    def check_ports(context):
        values = context.inputs['values'], context.inputs['other']
        level = context.inputs['level']
        snapshots.append(values)
        context.set_output('total', level + sum(value.value for value in values if value is not None))

    probe = make_engine('''
    default output: value uint8;
    node first: Std.DebugEventSource { Type: uint8 }
    node second: Std.DebugEventSource { Type: uint8 }
    node level: Std.Constant { Type: uint8, Value: 7 }

    native procedure run
      depends on values: event uint8, other: event uint8, level: value uint8
      produces total: value uint8;
    connect first -> run.values;
    connect second -> run.other;
    connect level -> run.level;
    connect run.total -> default output;
    ''', check_ports)
    probe.step()
    assert snapshots[-1] == (None, None)
    probe.assert_value_is('probe', 7)
    probe.get_native_state('probe.first').send_event(wirely.Some(1))
    probe.get_native_state('probe.second').send_event(wirely.Some(3))
    probe.step()
    assert snapshots[-1] == (wirely.Some(1), wirely.Some(3))
    probe.assert_value_is('probe', 11)


def check_outputs():
    def check_output_errors(context):
        with checks.assertRaisesRegex(ValueError, f'Unknown output.*on {context.name}$'):
            context.set_output('missing', 7)
        try:
            context.set_output('output', 'invalid')
        except ValueError as error:
            assert 'Invalid value' in str(error)
            assert f'on {context.name}:' in str(error)
        else:
            raise AssertionError('Invalid output was accepted')
        for invalid in (256, -1, True):
            with checks.assertRaisesRegex(ValueError, 'Invalid value'):
                context.set_output('output', invalid)
        context.set_output('output', 7)
        with checks.assertRaisesRegex(ValueError, f'on {context.name} was already written'):
            context.set_output('output', 8)

    probe = make_engine('''
    default output: value uint8;

    native procedure run produces default output as "output";
    ''', check_output_errors)
    probe.step()
    probe.assert_value_is('probe', 7)

    probe = make_engine('''
    default output: value uint8;

    native procedure run produces default output as "output";
    ''', lambda context: None)
    with checks.assertRaisesRegex(ValueError, 'did not write outputs'):
        probe.step()

    def check_generic_output(context):
        for invalid in [True, wirely.Some(None)]:
            try:
                context.set_output('output', invalid)
            except ValueError as error:
                assert 'Invalid value' in str(error)
            else:
                raise AssertionError('Invalid option output was accepted')
        context.set_output('output', wirely.Some(True))

    probe = make_engine('''
    parameter Type: ValueType;
    default output: value Type;

    native procedure run produces default output as "output";
    ''', check_generic_output, parameters='Type: Option[boolean]')
    probe.step()
    probe.assert_value_is('probe', wirely.Some(True))


def check_events():
    # Events use an outer option even when the payload is itself an option.
    for payload_type, event, command in [
        ('unit', wirely.Some(wirely.Unit()), '()'),
        ('Option[boolean]', wirely.Some(None), 'none'),
        ('Option[boolean]', wirely.Some(wirely.Some(True)), 'true'),
    ]:
        seen_events = []

        def forward_event(context):
            seen_events.append(context.inputs['event'])
            context.set_output('output', context.inputs['event'])

        probe = make_engine(f'''
    default input: event {payload_type};
    default output: event {payload_type};

    native procedure run
      depends on default input as "event"
      produces default output as "output";
    ''', forward_event, extra=f'''
    node source: Std.DebugEventSource {{ Type: {payload_type} }}
    connect source -> probe;
    ''')
        probe.step()
        probe.assert_value_is('probe', None)
        source = probe.get_native_state('source')
        for send in [lambda: source.send_event(event),
                     lambda: source.debug_command(['send', command])]:
            send()
            probe.step()
            probe.assert_value_is('probe', event)
            probe.assert_value_is('source', event)
            assert seen_events[-1] == event
            probe.step()
            probe.assert_value_is('probe', None)
            probe.assert_value_is('source', None)
            assert seen_events[-1] is None
            probe.assert_is_idle()


def check_unit_outputs():
    def check_unit_output(context):
        with checks.assertRaisesRegex(ValueError, 'Invalid value'):
            context.set_output('output', None)
        context.set_output('output', wirely.Unit())

    probe = make_engine('''
    default output: value unit;

    native procedure run produces default output as "output";
    ''', check_unit_output)
    probe.step()
    probe.assert_value_is('probe', wirely.Unit())

    def check_unit_event_output(context):
        for invalid in [wirely.Some(None), wirely.Unit()]:
            with checks.assertRaisesRegex(ValueError, 'Invalid value'):
                context.set_output('output', invalid)
        context.set_output('output', wirely.Some(wirely.Unit()))

    probe = make_engine('''
    default output: event unit;

    native procedure run produces default output as "output";
    ''', check_unit_event_output)
    probe.step()
    probe.assert_value_is('probe', wirely.Some(wirely.Unit()))


def check_enum_outputs():
    # Enum outputs accept only EnumValues of the output's enum type.
    output = 'Off'

    def check_enum_output(context):
        mode, other = context.values['mode'], context.values['other']
        assert (mode.name, mode.value, mode.type.name) == ('On', 2, 'Example.Mode')
        for invalid in [2, other]:
            with checks.assertRaisesRegex(ValueError, 'Expected an EnumValue of Example.Mode'):
                context.set_output('output', invalid)
        value = mode.type.from_name(output) if isinstance(output, str) else mode.type.from_value(output)
        context.set_output('output', value)

    probe = make_engine('''
    default output: value Example.Mode;

    native procedure run produces default output as "output"
      uses Example.Mode.On as mode, Example.Other.On as other;
    ''', check_enum_output, extra='''
namespace Example {
  open enum type Mode: uint8 { Off = 1, On = 2, }
  open enum type Other: uint8 { Off = 1, On = 2, }
}
''')
    probe.step()
    # Inside the runtime, named and unnamed enum values are numbers.
    probe.assert_value_is('probe', 1)
    output = 255
    probe.trigger('probe', 'run')
    probe.step()
    probe.assert_value_is('probe', 255)


def check_struct_outputs():
    # Struct outputs accept only StructValues of the output's struct type.
    def check_struct_output(context):
        pair, other = context.values['pair'], context.values['other']
        assert (pair.type.name, pair.values) == ('Example.Pair', (True, 7))
        for invalid in [(True, 7), other]:
            with checks.assertRaisesRegex(ValueError, 'Expected a StructValue of Example.Pair'):
                context.set_output('output', invalid)
        context.set_output('output', pair.type.make((False, 8)))

    probe = make_engine('''
    default output: value Example.Pair;

    native procedure run produces default output as "output"
      uses Example.Pair { enabled: true, number: 7 } as pair,
        Example.Other { enabled: true, number: 7 } as other;
    ''', check_struct_output, extra='''
namespace Example {
  struct type Pair { enabled: boolean, number: uint8 }
  struct type Other { enabled: boolean, number: uint8 }
}
''')
    probe.step()
    # Inside the runtime, a struct is a tuple of its field values.
    probe.assert_value_is('probe', (False, 8))


def check_port_keys():
    def check_keys(context):
        assert context.inputs['line\nkey'] == 7
        assert context.inputs[r'raw\key'] == 7
        context.set_output('', 7)

    probe = make_engine(r'''
    node source: Std.Constant { Type: uint8, Value: 7 }
    default output: value uint8;

    native procedure run
      depends on source as "line\nkey", source as r"raw\key"
      produces default output as "";
    ''', check_keys)
    probe.step()
    probe.assert_value_is('probe', 7)

    # Aliases are checked independently for input and output ports.
    probe = make_engine('''
    node source: Std.Constant { Type: uint8, Value: 7 }
    default output: value uint8;

    native procedure run
      depends on source as "value"
      produces default output as "value";
    ''', lambda context: context.set_output('value', context.inputs['value']))
    probe.step()
    probe.assert_value_is('probe', 7)

    # Typed port keys replace port names only in Python.
    probe = make_engine('''
    node source: Std.Constant { Type: uint8, Value: 7 }
    default output: value uint8;

    native procedure run
      depends on level as "value": value uint8
      produces result as "output": value uint8;
    connect source -> run.level;
    connect run.result -> default output;
    ''', lambda context: context.set_output('output', context.inputs['value']))
    probe.step()
    probe.assert_value_is('probe', 7)


startup_observations = []
cleanup_observations = []
lifecycle_registry = {}


@native_node_type(lifecycle_registry, has_native_state=True)
class LifecycleProbe(NativeState):
    def start_tasks(self, task_group, async_exit_stack):
        async_exit_stack.callback(self.observe_cleanup)
        startup_observations.append(self.context.name)

    async def start_tasks_async(self, task_group, async_exit_stack):
        if self.values['Fail']:
            raise RuntimeError('Startup failed')

    def observe_cleanup(self):
        cleanup_observations.append(self.context.name)


def make_lifecycle_engine(*, fail=False):
    with patch.dict(wirely.native.test_library.native_node_types, lifecycle_registry):
        graph = wirely.compiler.compile_strs([('<native-lifecycle>', f'''
namespace TestLibrary {{
  native node type LifecycleProbe {{
    parameter Fail: boolean = false;
    native initializer uses Fail;
  }}
}}
node first: TestLibrary.LifecycleProbe {{}}
node second: TestLibrary.LifecycleProbe {{ Fail: {'true' if fail else 'false'} }}
''')])
        assert graph is not None
        program = program_compiler.compile(graph)
        assert program is not None
        return Engine(program)


async def run_test():
    startup_observations.clear()
    cleanup_observations.clear()
    lifecycle_engine = make_lifecycle_engine()
    async with asyncio.TaskGroup() as task_group:
        async with contextlib.AsyncExitStack() as exit_stack:
            await lifecycle_engine.start_tasks_async(task_group, exit_stack)
    timestamp = lifecycle_engine.last_shutdown_time
    assert timestamp is not None and timestamp.tzinfo is not None
    checks.assertCountEqual(startup_observations, ['first', 'second'])
    assert cleanup_observations == startup_observations[::-1]

    startup_observations.clear()
    cleanup_observations.clear()
    failing_engine = make_lifecycle_engine(fail=True)
    async with asyncio.TaskGroup() as task_group:
        with checks.assertRaisesRegex(RuntimeError, 'Startup failed'):
            async with contextlib.AsyncExitStack() as exit_stack:
                await failing_engine.start_tasks_async(task_group, exit_stack)
    assert startup_observations in (['second'], ['first', 'second'])
    assert cleanup_observations == startup_observations[::-1]
    assert failing_engine.last_shutdown_time is None


def check_native_methods():
    # Native procedures are static or class methods of the class their name selects.
    registry = {}
    calls = []

    class Methods:
        @staticmethod
        def shared(*, context):
            calls.append(('shared', context.name))

        @classmethod
        def class_method(cls, *, context):
            calls.append((cls.__name__, context.name))

    @native_node_type(registry, has_native_state=False)
    class ClassProbe(Methods):
        pass

    @native_node_type(registry, has_native_state=False)
    class OverrideProbe(ClassProbe):
        @staticmethod
        def shared(*, context):
            calls.append(('override', context.name))

    @native_node_type(registry, has_native_state=True)
    class StateProbe(Methods, NativeState):
        def __init__(self, *, context):
            super().__init__(context=context)
            context.set_output('state', self)

    source = '''namespace TestLibrary {
  resource type ProbeState;
  node type ClassProbe {
    native procedure shared;
    native procedure class_method;
  }
  node type OverrideProbe {
    native procedure shared;
    native procedure class_method;
  }
  native node type StateProbe {
    binding state: resource ProbeState;
    native initializer produces state as "state";
    native procedure class_method depends on state as "state";
  }
  // Procedures may use another type's implementation.
  node type KeyedProbe {
    native procedure shared as "TestLibrary.ClassProbe:shared";
  }
}
node class_only: TestLibrary.ClassProbe {}
node overridden: TestLibrary.OverrideProbe {}
node stateful: TestLibrary.StateProbe {}
node keyed: TestLibrary.KeyedProbe {}
'''
    with patch.dict(wirely.native.test_library.native_node_types, registry), \
            patch.dict(wirely.native.test_library.native_resource_types, {'ProbeState': StateProbe}):
        graph = wirely.compiler.compile_strs([('<native-methods>', source)])
        assert graph is not None
        runtime = Engine(program_compiler.compile_nocatch(graph))
    runtime.step()
    expected = [
        ('shared', 'class_only'), ('ClassProbe', 'class_only'),
        ('override', 'overridden'), ('OverrideProbe', 'overridden'),
        ('StateProbe', 'stateful'), ('shared', 'keyed'),
    ]
    assert sorted(calls) == sorted(expected)
    runtime.assert_is_idle()


def check_native_linking_errors():
    constructed = []

    @native_node_type({}, has_native_state=True)
    class ValidProbe(NativeState):
        def __init__(self, *, context):
            constructed.append(context.name)
            super().__init__(context=context)

        @staticmethod
        def run(*, context):
            pass

    def wrong_signature(*, other):
        pass

    def instance_method(self, *, context):
        pass

    async def asynchronous(*, context):
        pass

    def generator(*, context):
        yield None

    async def async_generator(*, context):
        yield None

    cases = (
        ({}, 'native procedure missing;', False, False, 'Cannot find native procedure'),
        ({}, 'native procedure Poll as "TestLibrary.InvalidProbe:missing";', False, False,
         'Cannot find native procedure'),
        ({'run': staticmethod(wrong_signature)}, 'native procedure run;', False, False,
         'Invalid native procedure'),
        ({'run': instance_method}, 'native procedure run;', False, False,
         'Invalid native procedure'),
        ({'run': staticmethod(asynchronous)}, 'native procedure run;', False, False,
         'must be synchronous'),
        ({'run': staticmethod(generator)}, 'native procedure run;', False, False,
         'cannot be a generator'),
        ({'run': staticmethod(async_generator)}, 'native procedure run;', False, False,
         'cannot be a generator'),
        ({'run': property(lambda self: None)}, 'native procedure run;', False, False,
         'Unsupported native procedure method'),
        ({}, '', True, False, 'requires has_native_state=True'),
        ({'run': instance_method}, 'native procedure run;', True, True,
         'Invalid native procedure'),
    )
    for methods, declaration, declared_state, implemented_state, message in cases:
        registry = {'ValidProbe': ValidProbe}
        implementation = type('InvalidProbe', (), methods)
        native_node_type(registry, has_native_state=implemented_state)(implementation)
        kind = 'native node' if declared_state else 'node'
        initializer = 'native initializer;' if declared_state else ''
        source = f'''namespace TestLibrary {{
  native node type ValidProbe {{
    native initializer;
    native procedure run;
  }}
  {kind} type InvalidProbe {{
    {initializer}
    {declaration}
  }}
}}
node valid: TestLibrary.ValidProbe {{}}
node invalid: TestLibrary.InvalidProbe {{}}
'''
        graph = wirely.compiler.compile_strs([('<invalid-native-implementation>', source)])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
        with patch.dict(wirely.native.test_library.native_node_types, registry, clear=True):
            with checks.assertRaisesRegex(ValueError, message) as raised:
                Engine(program)
            checks.assertIn('InvalidProbe', str(raised.exception))
        checks.assertEqual(constructed, [])


def check_input_length_values():
    registry = {}

    @native_node_type(registry, has_native_state=True)
    class LengthProbe(NativeState):
        def __init__(self, *, context):
            super().__init__(context=context)
            context.set_output('state', self)
            self.count = context.values['Count']

        @staticmethod
        def run(*, context):
            state = context.inputs['state'].object
            assert context.values['Count'] == state.count
            context.set_output('output', state.count)

    source = '''
namespace TestLibrary {
  resource type LengthState;
  native node type LengthProbe {
    default input: multi value boolean;
    default output: value uint32;
    binding state: resource LengthState;
    native initializer produces state as "state" uses (default input).Length as Count;
    native procedure run depends on state as "state" produces default output as output
      uses (default input).Length as Count;
  }
}
node source: Std.DebugValueSource { Type: boolean }
node empty: TestLibrary.LengthProbe {}
node multiple: TestLibrary.LengthProbe {}
connect source, source -> multiple;
'''
    with patch.dict(wirely.native.test_library.native_node_types, registry), \
            patch.dict(wirely.native.test_library.native_resource_types, {'LengthState': LengthProbe}):
        graph = wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', std_source), ('<input-length-values>', source),
        ])
        assert graph is not None
        machine = Engine(program_compiler.compile(graph))
    for value in (False, True):
        machine.get_native_state('source').set_value(value)
        machine.step()
        machine.assert_value_is('empty', 0)
        machine.assert_value_is('multiple', 2)
        machine.assert_is_idle()


check_input_length_values()
check_native_linking_errors()
check_input_ports()
check_wakeups()
check_context_names()
check_stage_times()
check_triggers()
check_typed_ports()
check_outputs()
check_events()
check_unit_outputs()
check_enum_outputs()
check_struct_outputs()
check_port_keys()
check_native_methods()
