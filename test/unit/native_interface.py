import asyncio
import contextlib
import datetime
import io
import traceback
from collections.abc import Mapping
from pathlib import Path
from textwrap import indent
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely as blocks
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type


checks = TestCase()
std_source = Path('wirely/lib/std.wrl').read_text()


def make_engine(declaration, callback, *, extra='', parameters='', singleton=False):
    registry = {}

    @native_block_type(registry, has_native_state=False)
    class Probe:
        @staticmethod
        def run(*, context):
            callback(context)

        @staticmethod
        def run2(*, context):
            callback(context)

    qualifier = 'singleton ' if singleton else ''
    source = f'''
namespace TestLibrary {{
  {qualifier}node type Probe {{
{indent(declaration.strip(), "    ")}
  }}
}}

node probe: TestLibrary.Probe{parameters};
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
        return blocks.Engine(program)


engine.step()
engine.assert_value_is('forward', True)
engine.assert_value_is('empty', True)
engine.assert_value_is('delay', False)
engine.step()
engine.assert_value_is('delay', True)


seen = []


def check_inputs(context):
    assert isinstance(context.inputs, Mapping)
    assert isinstance(context.inputs, MappingProxyType)
    assert dict(context.inputs) == {'value': 7, 'named': 7}
    assert context.inputs.get('missing') is None
    inputs = context.inputs
    with checks.assertRaises(AttributeError):
        context.inputs = {'value': 8}
    with checks.assertRaises(AttributeError):
        del context.inputs
    with checks.assertRaises(AttributeError):
        context.not_inputs = 5
    assert context.inputs is inputs
    assert dict(context.inputs) == {'value': 7, 'named': 7}
    with checks.assertRaises(TypeError):
        context.inputs['value'] = 8
    with checks.assertRaises(KeyError):
        context.inputs['missing']
    for attribute in ('static', 'step_info', 'time', 'dep_values', 'results', 'state_old', 'state_new'):
        assert not hasattr(context, attribute)
    assert context.name == 'probe'
    with checks.assertRaises(AttributeError):
        context.name = 'other'
    assert repr(context) == "ProcedureContext(name='probe')"
    seen.append(context.inputs)
    context.set_result('result', context.inputs['value'])


probe = make_engine('''
implicit input: value uint8;
input named: value uint8;
implicit output: value uint8;

native procedure run
  depends on implicit input as "value", named
  produces implicit output as "result";
''', check_inputs, extra='''
node source: Std.Constant(Type = uint8, Value = 7);
connect source -> probe;
connect source -> probe.named;
''')
probe.step()
probe.assert_value_is('probe', 7)


def check_wakeup(context, deadline):
    assert context.wakeup_time is None
    with checks.assertRaises(AttributeError):
        context.wakeup_time = deadline
    with checks.assertRaises(AttributeError):
        del context.wakeup_time
    for invalid in [False, 0, 'soon', [], datetime.datetime.now(), blocks.Duration.from_seconds(1)]:
        with checks.assertRaises(ValueError):
            context.set_wakeup_time(invalid)
        assert context.wakeup_time is None
    context.set_wakeup_time(deadline)
    assert context.wakeup_time is deadline
    for repeated in [None, blocks.MonotonicTime(1, 100), 'invalid']:
        with checks.assertRaisesRegex(ValueError, 'already set'):
            context.set_wakeup_time(repeated)
        assert context.wakeup_time is deadline


for deadline in [None, blocks.MonotonicTime(1, 100),
                 blocks.MonotonicTime.create_invalid(), blocks.MonotonicTime(2, 100)]:
    wakeup_probe = make_engine(
        'native procedure run;',
        lambda context, deadline=deadline: check_wakeup(context, deadline),
    )
    with patch.object(blocks.MonotonicTime, 'current_time', return_value=blocks.MonotonicTime(1, 0)):
        wakeup_probe.step()
    if deadline is None:
        assert not wakeup_probe.wakeups
        assert wakeup_probe.next_wakeup is None
    else:
        assert list(wakeup_probe.wakeups.values()) == [deadline]
        expected = deadline if deadline.generation == 1 else blocks.MonotonicTime.create_invalid()
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
        assert not wakeup_probe.wakeups
        assert wakeup_probe.next_wakeup is None
        wakeup_probe.step()
        assert len(wakeup_calls) == 2
        wakeup_probe.assert_is_idle()


named_contexts = []


def check_node_name(context):
    assert not hasattr(context, 'static')
    with checks.assertRaisesRegex(ValueError, f'on {context.name}$'):
        context.set_result('missing', None)
    named_contexts.append(context.name)


named_engine = make_engine('native procedure run;', check_node_name, extra='''
node type Outer {
  node child: TestLibrary.Probe;
}
node outer: Outer;
namespace TestLibrary {
  singleton node type SharedContainer {
    node child: Probe;
  }
}
node shared: TestLibrary.SharedContainer;
node shared_again: TestLibrary.SharedContainer;
''')
named_engine.step()
assert sorted(named_contexts) == ['TestLibrary.SharedContainer.child', 'outer.child', 'probe']
named_contexts.clear()
named_engine = make_engine('native procedure run;', check_node_name, singleton=True)
named_engine.step()
assert named_contexts == ['TestLibrary.Probe']

assert [stage.name for stage in blocks.RunStage] == ['INPUT', 'DEFAULT', 'OUTPUT']
assert [stage.value for stage in blocks.RunStage] == [1, 2, 3]

for has_native_state in [False, True]:
    registry = {}

    @native_block_type(registry, has_native_state=has_native_state)
    class RegistrationProbe:
        @staticmethod
        def run(*, context):
            pass

    qualifier = '' if has_native_state else 'native '
    source = f'''namespace TestLibrary {{
  {qualifier}node type RegistrationProbe {{
    native procedure run;
  }}
}}
node probe: TestLibrary.RegistrationProbe;
'''
    output = io.StringIO()
    errors = io.StringIO()
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            graph = wirely.compiler.compile_strs([('<native-registration>', source)])
            program = program_compiler.compile(graph) if graph is not None else None
    assert program is None
    assert not errors.getvalue()
    assert f'requires has_native_state={not has_native_state}' in output.getvalue()
    assert 'Error in <native-registration>:2:' in output.getvalue()
    assert 'Traceback' not in output.getvalue()


clock_registry = {}
clock_methods = {}
clock_declarations = []
stage_snapshots = {}
clock_contexts = []
stages = list(blocks.RunStage)
monotonic_times = [blocks.MonotonicTime(7, index * 1_000_000_000)
                   for index in range(1, 4)]
utc_times = [datetime.datetime(2026, 1, 1, 12, 0, index,
                               tzinfo=datetime.timezone.utc)
             for index in range(3)]
clock_offset = blocks.Duration.from_seconds(5)


def check_stage_time(context, stage):
    assert not hasattr(context, 'step_info')
    assert not hasattr(context, 'time')
    snapshot = context.start_time_for_stage(stage)
    assert isinstance(snapshot, blocks.TimeSnapshot)
    assert snapshot is context.start_time_for_stage(stage)
    index = stages.index(stage)
    assert snapshot.monotonic == monotonic_times[index] + clock_offset
    assert snapshot.utc == utc_times[index]
    assert snapshot.utc.tzinfo is datetime.timezone.utc
    assert snapshot.local == snapshot.utc
    assert snapshot.local.isoformat() == snapshot.utc.astimezone().isoformat()
    assert snapshot.local.tzinfo is not None
    for attribute in ['monotonic', 'utc', 'local']:
        with checks.assertRaises(AttributeError):
            setattr(snapshot, attribute, None)
    for attribute in ['generation', 'time_ns']:
        with checks.assertRaises(AttributeError):
            setattr(snapshot.monotonic, attribute, 0)
        with checks.assertRaises(AttributeError):
            delattr(snapshot.monotonic, attribute)
    assert snapshot.monotonic == monotonic_times[index] + clock_offset
    if stage in stage_snapshots:
        assert stage_snapshots[stage] is snapshot
    else:
        stage_snapshots[stage] = snapshot
    for candidate in stages:
        if stages.index(candidate) <= index:
            assert context.start_time_for_stage(candidate) is stage_snapshots[candidate]
        else:
            with checks.assertRaises(KeyError):
                context.start_time_for_stage(candidate)
    for invalid in [None, 'DEFAULT', 2, []]:
        with checks.assertRaises(ValueError):
            context.start_time_for_stage(invalid)
    clock_contexts.append(context)


for stage in stages:
    for index in range(2):
        def record_time(*, context, stage=stage):
            check_stage_time(context, stage)

        name = f'{stage.name.lower()}_{index}'
        clock_methods[name] = staticmethod(record_time)
        stage_clause = '' if stage is blocks.RunStage.DEFAULT else f' stage {stage.name.lower()}'
        clock_declarations.append(f'native procedure {name}{stage_clause};')

ClockProbe = native_block_type(clock_registry, has_native_state=False)(
    type('ClockProbe', (), clock_methods),
)
clock_source = '''namespace TestLibrary {
  node type ClockProbe {
''' + '\n'.join(f'    {line}' for line in clock_declarations) + '''
  }
}
node probe: TestLibrary.ClockProbe;
'''
with patch.dict(wirely.native.test_library.native_block_types, clock_registry):
    graph = wirely.compiler.compile_strs([('<stage-times>', clock_source)])
    assert graph is not None
    program = program_compiler.compile(graph)
    assert program is not None
    clock_engine = blocks.Engine(program)
clock_engine.fake_monotonic_time_offset = clock_offset


class ClockDateTime(datetime.datetime):
    timestamps = iter(utc_times)

    @classmethod
    def now(cls, tz=None):
        assert tz is datetime.timezone.utc
        return next(cls.timestamps)


with patch.object(blocks.MonotonicTime, 'current_time', side_effect=monotonic_times) as monotonic_clock:
    with patch('wirely.runtime.contexts.datetime.datetime', ClockDateTime):
        _, _, step_info = clock_engine.step()
assert monotonic_clock.call_count == 3
assert len(clock_contexts) == 6
assert len({id(snapshot) for snapshot in stage_snapshots.values()}) == 3
for stage, snapshot in stage_snapshots.items():
    assert step_info.stage_start_time_monotonic[stage] is snapshot.monotonic
    assert step_info.stage_start_time_utc[stage] == snapshot.utc
    assert step_info.stage_start_time[stage] == snapshot.local
clock_engine.step()
assert len(clock_contexts) == 6
for stage, snapshot in stage_snapshots.items():
    assert clock_contexts[-1].start_time_for_stage(stage) is snapshot


trigger_registry = {}


@native_block_type(trigger_registry, has_native_state=True)
class TriggerProbe(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert isinstance(context, blocks.NativeNodeContext)
        assert self.context is context
        assert self.parameters is context.parameters
        assert self.inputs is context.inputs
        assert not context.parameters and not context.inputs
        assert context.configuration is None
        assert not hasattr(context, 'trigger')
        assert not hasattr(context, 'shutdown_time')
        for target in [self, context]:
            for name in ['engine', 'block_path', 'block', 'block_type',
                         'block_or_singleton', 'static_block_context']:
                assert not hasattr(target, name)
        for name in ['name', 'parameters', 'inputs', 'configuration']:
            with checks.assertRaises(AttributeError):
                setattr(context, name, None)
        with checks.assertRaises(AttributeError):
            self.context = None
        self.calls = []
        self.reschedule_once = False
        self.trigger('refresh')
        assert not self.calls

    def refresh(self, *, context):
        assert not hasattr(context, 'trigger')
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
node first: TestLibrary.TriggerProbe;
node second: TestLibrary.TriggerProbe;
node shared: TestLibrary.SharedTriggerProbe;
node shared_again: TestLibrary.SharedTriggerProbe;
''')])
assert trigger_graph is not None
trigger_program = program_compiler.compile(trigger_graph)
assert trigger_program is not None
with patch.dict(wirely.native.test_library.native_block_types, trigger_registry):
    trigger_engine = blocks.Engine(trigger_program)
first = trigger_engine.get_native_state('first')
second = trigger_engine.get_native_state('second')
shared = trigger_engine.get_native_state('shared')
assert first.context.name == 'first'
assert second.context.name == 'second'
assert shared.context.name == 'TestLibrary.SharedTriggerProbe'
assert first.context is not second.context
assert shared is trigger_engine.get_native_state('shared_again')
assert not first.calls and not second.calls and not shared.calls
trigger_engine.step()
for state in [first, second, shared]:
    assert state.calls == ['refresh', 'run']
trigger_engine.step()
trigger_engine.assert_is_idle()

for invalid in ['Poll', 'Refresh', '', 'missing', first.refresh, 1, []]:
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

static_alias = make_engine(
    'native procedure Poll as "run";',
    lambda context: checks.assertFalse(hasattr(context, 'trigger')),
)
static_alias.step()
probe.step()
assert len(seen) == 1  # Unchanged dependencies use cached results.
assert seen[0]['value'] == 7


def check_list(context):
    assert context.inputs['values'] == (7, 2)
    context.set_result('result', sum(context.inputs['values']))


probe = make_engine('''
implicit input: list value uint8;
implicit output: value uint8;

native procedure run
  depends on implicit input as "values"
  produces implicit output as "result";
''', check_list, extra='''
node first: Std.Constant(Type = uint8, Value = 7);
node second: Std.Constant(Type = uint8, Value = 2);
connect first, second -> probe;
''')
probe.step()
probe.assert_value_is('probe', 9)


def check_empty_list(context):
    assert context.inputs['values'] == ()
    context.set_result('result', 0)


probe = make_engine('''
implicit input: list value uint8;
implicit output: value uint8;

native procedure run
  depends on implicit input as "values"
  produces implicit output as "result";
''', check_empty_list)
probe.step()
probe.assert_value_is('probe', 0)

event_snapshots = []


def check_event_connections(context):
    values = context.inputs['values']
    assert type(values) is tuple
    event_snapshots.append(values)


probe = make_engine('''
implicit input: list event uint8;

native procedure run
  depends on implicit input as "values";
''', check_event_connections, extra='''
node first: Std.DebugEventSource(Type = uint8);
node second: Std.DebugEventSource(Type = uint8);
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


def check_state(context):
    old = context.inputs['previous']
    context.set_result('next', old + 1)
    assert context.inputs['previous'] == old
    context.set_result('result', old)


probe = make_engine('''
state count: uint8 = 0;
implicit output: value uint8;

native procedure run
  depends on count.old as "previous"
  produces count as "next", implicit output as "result";
''', check_state)
for expected in range(3):
    probe.step()
    probe.assert_value_is('probe', expected)


def check_state_snapshot(context):
    if 'previous' in context.inputs:
        context.set_result('next', context.inputs['previous'] + 1)
    else:
        context.set_result('result', context.inputs['snapshot'])


probe = make_engine('''
state count: uint8 = 0;
implicit output: value uint8;

native procedure run
  depends on count.old as "previous"
  produces count as "next";
native procedure run2
  depends on count.old as "snapshot"
  produces implicit output as "result";
''', check_state_snapshot)
for expected in range(3):
    probe.step()
    probe.assert_value_is('probe', expected)


def forward_value(context):
    context.set_result('result', context.inputs['value'])


# Named data outputs work alongside resource outputs and shared singletons.
for singleton in ('', 'singleton '):
    probe = make_engine('''
node child: Child;
implicit output: value uint8;

native procedure run
  depends on child.Value as "value"
  produces implicit output as "result";
''', forward_value, extra=f'''
resource type Session;

{singleton}node type Child {{
  output Service: resource Session;
  output Value: value uint8;

  node source: Std.Constant(Type = uint8, Value = 7);
  connect source -> Value;
}}

node another: TestLibrary.Probe;
''')
    probe.step()
    probe.assert_value_is('probe', 7)
    probe.assert_value_is('another', 7)


def check_result_errors(context):
    with checks.assertRaisesRegex(ValueError, f'Unknown result.*on {context.name}$'):
        context.set_result('missing', 7)
    try:
        context.set_result('result', 'invalid')
    except ValueError as error:
        assert 'Invalid value' in str(error)
        assert f'on {context.name}:' in str(error)
        assert 'check_result_errors' in [
            frame.name for frame in traceback.extract_tb(error.__traceback__)
        ]
    else:
        raise AssertionError('Invalid result was accepted')
    context.set_result('result', 7)
    with checks.assertRaisesRegex(ValueError, f'on {context.name} was already written'):
        context.set_result('result', 8)


probe = make_engine('''
implicit output: value uint8;

native procedure run produces implicit output as "result";
''', check_result_errors)
probe.step()
probe.assert_value_is('probe', 7)

probe = make_engine('''
implicit output: value uint8;

native procedure run produces implicit output as "result";
''', lambda context: None)
with checks.assertRaisesRegex(ValueError, 'did not write results'):
    probe.step()


def check_generic_result(context):
    for invalid in [(True,), True, blocks.Some(None)]:
        try:
            context.set_result('result', invalid)
        except ValueError as error:
            assert 'Invalid value' in str(error)
            assert 'check_generic_result' in [
                frame.name for frame in traceback.extract_tb(error.__traceback__)
            ]
        else:
            raise AssertionError('Invalid option result was accepted')
    context.set_result('result', blocks.Some(True))


probe = make_engine('''
parameter Type: ValueType;
implicit output: value Type;

native procedure run produces implicit output as "result";
''', check_generic_result, parameters='(Type = Option[boolean])')
probe.step()
probe.assert_value_is('probe', blocks.Some(True))


# Events use an outer option even when the payload is itself an option.
for payload_type, event, command in [
    ('unit', blocks.Some(blocks.Unit()), '()'),
    ('Option[boolean]', blocks.Some(None), 'none'),
    ('Option[boolean]', blocks.Some(blocks.Some(True)), 'true'),
]:
    seen_events = []

    def forward_event(context):
        seen_events.append(context.inputs['event'])
        context.set_result('result', context.inputs['event'])

    probe = make_engine(f'''
implicit input: event {payload_type};
implicit output: event {payload_type};

native procedure run
  depends on implicit input as "event"
  produces implicit output as "result";
''', forward_event, extra=f'''
node source: Std.DebugEventSource(Type = {payload_type});
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


def check_unit_result(context):
    with checks.assertRaisesRegex(ValueError, 'Invalid value'):
        context.set_result('result', None)
    context.set_result('result', blocks.Unit())


probe = make_engine('''
implicit output: value unit;

native procedure run produces implicit output as "result";
''', check_unit_result)
probe.step()
probe.assert_value_is('probe', blocks.Unit())


def check_unit_event_result(context):
    for invalid in [blocks.Some(None), blocks.Unit()]:
        with checks.assertRaisesRegex(ValueError, 'Invalid value'):
            context.set_result('result', invalid)
    context.set_result('result', blocks.Some(blocks.Unit()))


probe = make_engine('''
implicit output: event unit;

native procedure run produces implicit output as "result";
''', check_unit_event_result)
probe.step()
probe.assert_value_is('probe', blocks.Some(blocks.Unit()))


def check_keys(context):
    assert context.inputs['line\nkey'] == 7
    assert context.inputs[r'raw\key'] == 7
    context.set_result('', 7)


probe = make_engine(r'''
node source: Std.Constant(Type = uint8, Value = 7);
implicit output: value uint8;

native procedure run
  depends on source as "line\nkey", source as r"raw\key"
  produces implicit output as "";
''', check_keys)
probe.step()
probe.assert_value_is('probe', 7)


# Aliases are checked independently for input and result bindings.
probe = make_engine('''
node source: Std.Constant(Type = uint8, Value = 7);
implicit output: value uint8;

native procedure run
  depends on source as "value"
  produces implicit output as "value";
''', lambda context: context.set_result('value', context.inputs['value']))
probe.step()
probe.assert_value_is('probe', 7)


cleanup_observations = []
lifecycle_registry = {}


@native_block_type(lifecycle_registry, has_native_state=True)
class LifecycleProbe(blocks.NativeState):
    def start_tasks(self, task_group, async_exit_stack):
        assert self._shutdown_time is None
        async_exit_stack.callback(self.observe_cleanup)

    async def start_tasks_async(self, task_group, async_exit_stack):
        if self.parameters['Fail']:
            raise RuntimeError('Startup failed')

    def observe_cleanup(self):
        cleanup_observations.append((self.context.name, self._shutdown_time))


def make_lifecycle_engine(*, fail=False):
    with patch.dict(wirely.native.test_library.native_block_types, lifecycle_registry):
        graph = wirely.compiler.compile_strs([('<native-lifecycle>', f'''
namespace TestLibrary {{
  native node type LifecycleProbe {{
    parameter Fail: boolean = false;
  }}
}}
node first: TestLibrary.LifecycleProbe;
node second: TestLibrary.LifecycleProbe(Fail = {'true' if fail else 'false'});
''')])
        assert graph is not None
        program = program_compiler.compile(graph)
        assert program is not None
        return blocks.Engine(program)


async def run_test():
    lifecycle_engine = make_lifecycle_engine()
    async with asyncio.TaskGroup() as task_group:
        async with contextlib.AsyncExitStack() as exit_stack:
            await lifecycle_engine.start_tasks_async(task_group, exit_stack)
            assert lifecycle_engine.get_native_state('first')._shutdown_time is None
    timestamp = lifecycle_engine.last_shutdown_time
    assert timestamp is not None and timestamp.tzinfo is not None
    assert cleanup_observations == [('second', timestamp), ('first', timestamp)]
    assert lifecycle_engine.get_native_state('first')._shutdown_time is None

    cleanup_observations.clear()
    failing_engine = make_lifecycle_engine(fail=True)
    async with asyncio.TaskGroup() as task_group:
        with checks.assertRaisesRegex(RuntimeError, 'Startup failed'):
            async with contextlib.AsyncExitStack() as exit_stack:
                await failing_engine.start_tasks_async(task_group, exit_stack)
    assert cleanup_observations == [('second', None), ('first', None)]
    assert failing_engine.last_shutdown_time is None
    assert failing_engine.get_native_state('first')._shutdown_time is None
