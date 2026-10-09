import asyncio
import contextlib
import datetime
from pathlib import Path
from textwrap import indent
from unittest import TestCase
from unittest.mock import patch

import wirely as blocks
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type
from wirely.program import RunStage
from wirely.runtime.contexts import NativeState
from wirely.runtime.engine import Engine


checks = TestCase()
std_source = Path('wirely/lib/std.wrl').read_text()


def make_engine(declaration, callback, *, extra='', parameters='', singleton=False):
    registry = {}

    @native_block_type(registry, has_native_state=False)
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
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', std_source),
            ('<native-interface>', source),
        ])
        assert graph is not None
        program = program_compiler.compile(graph)
        assert program is not None
        return Engine(program)


def check_input_bindings():
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

    for deadline in [None, blocks.MonotonicTime(1, 100),
                     blocks.MonotonicTime(0, 0), blocks.MonotonicTime(2, 100)]:
        wakeup_probe = make_engine(
            'native procedure run;',
            lambda context, deadline=deadline: check_wakeup(context, deadline),
        )
        with patch.object(blocks.MonotonicTime, 'current_time', return_value=blocks.MonotonicTime(1, 0)):
            wakeup_probe.step()
        if deadline is None:
            assert wakeup_probe.next_wakeup is None
        else:
            # A wakeup from a non-current generation is due now.
            expected = deadline if deadline.generation == 1 else blocks.MonotonicTime(1, 0)
            assert wakeup_probe.next_wakeup == expected

    for explicit_cancellation in [False, True]:
        wakeup_calls = []
        deadline = blocks.MonotonicTime(1, 10)

        def schedule_wakeup(context):
            assert context.wakeup_time is None
            wakeup_calls.append(context)
            if len(wakeup_calls) == 1:
                context.set_wakeup_time(deadline)
            elif explicit_cancellation:
                context.set_wakeup_time(None)

        wakeup_probe = make_engine('native procedure run;', schedule_wakeup)
        with patch.object(blocks.MonotonicTime, 'current_time', return_value=blocks.MonotonicTime(1, 0)):
            wakeup_probe.step()
            assert wakeup_probe.next_wakeup == deadline
            wakeup_probe.step()
            assert len(wakeup_calls) == 1
            assert wakeup_probe.next_wakeup == deadline
        with patch.object(blocks.MonotonicTime, 'current_time', return_value=deadline):
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
    monotonic_times = [blocks.MonotonicTime(7, index * 1_000_000_000)
                       for index in range(1, 4)]
    utc_times = [datetime.datetime(2026, 1, 1, 12, 0, index,
                                   tzinfo=datetime.timezone.utc)
                 for index in range(3)]
    clock_offset = blocks.Duration.from_seconds(5)

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

    native_block_type(clock_registry, has_native_state=False)(
        type('ClockProbe', (), clock_methods),
    )
    clock_source = '''namespace TestLibrary {
      node type ClockProbe {
    ''' + '\n'.join(f'    {line}' for line in clock_declarations) + '''
      }
    }
    node probe: TestLibrary.ClockProbe {}
    '''
    with patch.dict(wirely.native.test_library.native_block_types, clock_registry):
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

    with patch.object(blocks.MonotonicTime, 'current_time', side_effect=monotonic_times), \
            patch('wirely.runtime.contexts.datetime.datetime', ClockDateTime):
        clock_engine.step()
    assert len(clock_contexts) == 6
    clock_engine.step()
    assert len(clock_contexts) == 6


def check_triggers():
    trigger_registry = {}

    @native_block_type(trigger_registry, has_native_state=True)
    class TriggerProbe(NativeState):
        def __init__(self, *, context):
            super().__init__(context=context)
            assert not context.values and not context.inputs
            with checks.assertRaises(AttributeError):
                context.name = 'other'
            self.calls = []
            self.reschedule_once = False
            self.trigger('refresh')
            assert not self.calls

        def refresh(self, *, context):
            assert context.name == self.context.name
            self.calls.append('refresh')
            if self.reschedule_once:
                self.reschedule_once = False
                self.trigger('refresh')

        def run(self, *, context):
            self.calls.append('run')

    @native_block_type(trigger_registry, has_native_state=True)
    class SharedTriggerProbe(TriggerProbe):
        pass

    with patch.dict(wirely.native.test_library.native_block_types, trigger_registry):
        trigger_graph = wirely.compiler.compile_strs([('<native-triggers>', '''
    namespace TestLibrary {
      native node type TriggerProbe {
        native procedure Poll as "refresh";
        native procedure run;
      }
      singleton native node type SharedTriggerProbe {
        native procedure Poll as "refresh";
        native procedure run;
      }
    }
    node first: TestLibrary.TriggerProbe {}
    node second: TestLibrary.TriggerProbe {}
    node shared: TestLibrary.SharedTriggerProbe {}
    node shared_again: TestLibrary.SharedTriggerProbe {}
    ''')])
    assert trigger_graph is not None
    trigger_program = program_compiler.compile(trigger_graph)
    assert trigger_program is not None
    with patch.dict(wirely.native.test_library.native_block_types, trigger_registry):
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
        assert state.calls == ['refresh', 'run']
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    for invalid in ['Poll', 'missing', 1]:
        with checks.assertRaisesRegex(ValueError, 'Unknown procedure name'):
            first.trigger(invalid)
    trigger_engine.step()
    assert first.calls == ['refresh', 'run']

    first.trigger('refresh')
    first.trigger('refresh')
    assert first.calls == ['refresh', 'run']
    trigger_engine.step()
    assert first.calls == ['refresh', 'run', 'refresh']
    assert second.calls == shared.calls == ['refresh', 'run']
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    first.reschedule_once = True
    first.trigger('refresh')
    trigger_engine.step()
    assert first.calls == ['refresh', 'run', 'refresh', 'refresh']
    trigger_engine.step()
    assert first.calls == ['refresh', 'run', 'refresh', 'refresh', 'refresh']
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    second.trigger('run')
    shared.trigger('refresh')
    trigger_engine.step()
    assert second.calls == ['refresh', 'run', 'run']
    assert shared.calls == ['refresh', 'run', 'refresh']
    assert len(first.calls) == 5
    trigger_engine.step()
    trigger_engine.assert_is_idle()


def check_multi_inputs():
    def check_list(context):
        assert context.inputs['values'] == (7, 2)
        context.set_output('output', sum(context.inputs['values']))

    probe = make_engine('''
    default input: multi value uint8;
    default output: value uint8;

    native procedure run
      depends on default input as "values"
      produces default output as "output";
    ''', check_list, extra='''
    node first: Std.Constant { Type: uint8, Value: 7 }
    node second: Std.Constant { Type: uint8, Value: 2 }
    connect first, second -> probe;
    ''')
    probe.step()
    probe.assert_value_is('probe', 9)

    def check_empty_list(context):
        assert context.inputs['values'] == ()
        context.set_output('output', 0)

    probe = make_engine('''
    default input: multi value uint8;
    default output: value uint8;

    native procedure run
      depends on default input as "values"
      produces default output as "output";
    ''', check_empty_list)
    probe.step()
    probe.assert_value_is('probe', 0)

    event_snapshots = []

    def check_event_connections(context):
        values = context.inputs['values']
        assert type(values) is tuple
        event_snapshots.append(values)

    probe = make_engine('''
    default input: multi event uint8;

    native procedure run
      depends on default input as "values";
    ''', check_event_connections, extra='''
    node first: Std.DebugEventSource { Type: uint8 }
    node second: Std.DebugEventSource { Type: uint8 }
    connect first, second -> probe;
    ''')
    probe.step()
    assert event_snapshots[-1] == (None, None)
    probe.get_native_state('first').send_event(blocks.Some(1))
    probe.get_native_state('second').send_event(blocks.Some(3))
    probe.step()
    assert event_snapshots[-1] == (blocks.Some(1), blocks.Some(3))
    probe.step()
    assert event_snapshots[-1] == (None, None)
    probe.assert_is_idle()


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
        for invalid in [True, blocks.Some(None)]:
            try:
                context.set_output('output', invalid)
            except ValueError as error:
                assert 'Invalid value' in str(error)
            else:
                raise AssertionError('Invalid option output was accepted')
        context.set_output('output', blocks.Some(True))

    probe = make_engine('''
    parameter Type: ValueType;
    default output: value Type;

    native procedure run produces default output as "output";
    ''', check_generic_output, parameters='Type: Option[boolean]')
    probe.step()
    probe.assert_value_is('probe', blocks.Some(True))


def check_events():
    # Events use an outer option even when the payload is itself an option.
    for payload_type, event, command in [
        ('unit', blocks.Some(blocks.Unit()), '()'),
        ('Option[boolean]', blocks.Some(None), 'none'),
        ('Option[boolean]', blocks.Some(blocks.Some(True)), 'true'),
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
        context.set_output('output', blocks.Unit())

    probe = make_engine('''
    default output: value unit;

    native procedure run produces default output as "output";
    ''', check_unit_output)
    probe.step()
    probe.assert_value_is('probe', blocks.Unit())

    def check_unit_event_output(context):
        for invalid in [blocks.Some(None), blocks.Unit()]:
            with checks.assertRaisesRegex(ValueError, 'Invalid value'):
                context.set_output('output', invalid)
        context.set_output('output', blocks.Some(blocks.Unit()))

    probe = make_engine('''
    default output: event unit;

    native procedure run produces default output as "output";
    ''', check_unit_event_output)
    probe.step()
    probe.assert_value_is('probe', blocks.Some(blocks.Unit()))


def check_binding_keys():
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

    # Aliases are checked independently for input and output bindings.
    probe = make_engine('''
    node source: Std.Constant { Type: uint8, Value: 7 }
    default output: value uint8;

    native procedure run
      depends on source as "value"
      produces default output as "value";
    ''', lambda context: context.set_output('value', context.inputs['value']))
    probe.step()
    probe.assert_value_is('probe', 7)


cleanup_observations = []
lifecycle_registry = {}


@native_block_type(lifecycle_registry, has_native_state=True)
class LifecycleProbe(NativeState):
    def start_tasks(self, task_group, async_exit_stack):
        async_exit_stack.callback(self.observe_cleanup)

    async def start_tasks_async(self, task_group, async_exit_stack):
        if self.values['Fail']:
            raise RuntimeError('Startup failed')

    def observe_cleanup(self):
        cleanup_observations.append(self.context.name)


def make_lifecycle_engine(*, fail=False):
    with patch.dict(wirely.native.test_library.native_block_types, lifecycle_registry):
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
    lifecycle_engine = make_lifecycle_engine()
    async with asyncio.TaskGroup() as task_group:
        async with contextlib.AsyncExitStack() as exit_stack:
            await lifecycle_engine.start_tasks_async(task_group, exit_stack)
    timestamp = lifecycle_engine.last_shutdown_time
    assert timestamp is not None and timestamp.tzinfo is not None
    assert cleanup_observations == ['second', 'first']

    cleanup_observations.clear()
    failing_engine = make_lifecycle_engine(fail=True)
    async with asyncio.TaskGroup() as task_group:
        with checks.assertRaisesRegex(RuntimeError, 'Startup failed'):
            async with contextlib.AsyncExitStack() as exit_stack:
                await failing_engine.start_tasks_async(task_group, exit_stack)
    assert cleanup_observations == ['second', 'first']
    assert failing_engine.last_shutdown_time is None


def check_native_methods():
    registry = {}
    calls = []

    class Methods:
        def instance(self, *, context):
            calls.append(('instance', context.name))

        @staticmethod
        def shared(*, context):
            calls.append(('shared', context.name))

        @classmethod
        def class_method(cls, *, context):
            calls.append((cls.__name__, context.name))

    @native_block_type(registry, has_native_state=True)
    class InstanceProbe(Methods, NativeState):
        pass

    @native_block_type(registry, has_native_state=True)
    class OverrideProbe(InstanceProbe):
        def shared(self, *, context):
            calls.append(('override', context.name))

    @native_block_type(registry, has_native_state=False)
    class ClassProbe(Methods):
        pass

    @native_block_type(registry, has_native_state=True)
    class SharedProbe(InstanceProbe):
        pass

    source = '''namespace TestLibrary {
  native node type InstanceProbe {
    native procedure Poll as "instance";
    native procedure shared;
    native procedure class_method;
  }
  native node type OverrideProbe {
    native procedure instance;
    native procedure shared;
    native procedure class_method;
  }
  node type ClassProbe {
    native procedure shared;
    native procedure class_method;
  }
  singleton native node type SharedProbe {
    native procedure instance;
    native procedure shared;
    native procedure class_method;
  }
}
node instance: TestLibrary.InstanceProbe {}
node overridden: TestLibrary.OverrideProbe {}
node class_only: TestLibrary.ClassProbe {}
node shared: TestLibrary.SharedProbe {}
node shared_again: TestLibrary.SharedProbe {}
'''
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs([('<native-methods>', source)])
        assert graph is not None
        runtime = Engine(program_compiler.compile_nocatch(graph))
    runtime.step()
    expected = [
        ('instance', 'instance'), ('shared', 'instance'), ('InstanceProbe', 'instance'),
        ('instance', 'overridden'), ('override', 'overridden'), ('OverrideProbe', 'overridden'),
        ('shared', 'class_only'), ('ClassProbe', 'class_only'),
        ('instance', 'TestLibrary.SharedProbe'), ('shared', 'TestLibrary.SharedProbe'),
        ('SharedProbe', 'TestLibrary.SharedProbe'),
    ]
    assert sorted(calls) == sorted(expected)
    runtime.assert_is_idle()


def check_native_linking_errors():
    constructed = []

    @native_block_type({}, has_native_state=True)
    class ValidProbe(NativeState):
        def __init__(self, *, context):
            constructed.append(context.name)
            super().__init__(context=context)

        def run(self, *, context):
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
        ({}, 'native procedure Poll as "missing";', False, False, 'Cannot find native procedure'),
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
        ({'run': instance_method}, 'native procedure run;', False, True,
         'requires has_native_state=False'),
    )
    for methods, declaration, declared_state, implemented_state, message in cases:
        registry = {'ValidProbe': ValidProbe}
        implementation = type('InvalidProbe', (), methods)
        native_block_type(registry, has_native_state=implemented_state)(implementation)
        kind = 'native node' if declared_state else 'node'
        source = f'''namespace TestLibrary {{
  native node type ValidProbe {{
    native procedure run;
  }}
  {kind} type InvalidProbe {{
    {declaration}
  }}
}}
node valid: TestLibrary.ValidProbe {{}}
node invalid: TestLibrary.InvalidProbe {{}}
'''
        graph = wirely.compiler.compile_strs([('<invalid-native-implementation>', source)])
        assert graph is not None
        program = program_compiler.compile_nocatch(graph)
        with patch.dict(wirely.native.test_library.native_block_types, registry, clear=True):
            with checks.assertRaisesRegex(ValueError, message) as raised:
                Engine(program)
            checks.assertIn('InvalidProbe', str(raised.exception))
        checks.assertEqual(constructed, [])


check_native_linking_errors()
check_input_bindings()
check_wakeups()
check_context_names()
check_stage_times()
check_triggers()
check_multi_inputs()
check_outputs()
check_events()
check_unit_outputs()
check_binding_keys()
check_native_methods()
