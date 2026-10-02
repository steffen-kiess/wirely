import asyncio
import contextlib
import traceback
from collections.abc import Mapping
from pathlib import Path
from textwrap import indent
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely as blocks
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type, native_procedure


checks = TestCase()
std_source = Path('wirely/lib/std.wrl').read_text()


def make_engine(declaration, callback, *, extra='', parameters=''):
    registry = {}

    @native_block_type(registry, has_state=False)
    class Probe:
        @native_procedure
        @staticmethod
        def run(*, context):
            callback(context)

        @native_procedure
        @staticmethod
        def run2(*, context):
            callback(context)

    source = f'''
namespace TestLibrary {{
  node type Probe {{
{indent(declaration.strip(), "    ")}
  }}
}}

node probe : TestLibrary.Probe{parameters};
{extra}
'''
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', std_source),
            ('<native-interface>', source),
        ])
        assert graph is not None
        program = blocks.Program.compile(graph)
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
    with checks.assertRaises(TypeError):
        context.inputs['value'] = 8
    with checks.assertRaises(KeyError):
        context.inputs['missing']
    for attribute in ('dep_values', 'results', 'state_old', 'state_new'):
        assert not hasattr(context, attribute)
    for attribute in ('sources', 'targets', 'state'):
        assert not hasattr(context.static, attribute)
    seen.append(context.inputs)
    context.set_result('result', context.inputs['value'])


probe = make_engine('''
implicit input : value uint8;
input named : value uint8;
implicit output : value uint8;

native procedure run
  depends on implicit input as "value", named
  produces implicit output as "result";
''', check_inputs, extra='''
node source : Std.Constant(Type = uint8, Value = 7);
connect source -> probe;
connect source -> probe.named;
''')
probe.step()
probe.assert_value_is('probe', 7)


trigger_registry = {}


@native_block_type(trigger_registry, has_state=True)
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

    @native_procedure
    def refresh(self, *, context):
        assert not hasattr(context, 'trigger')
        self.calls.append('refresh')
        if self.reschedule_once:
            self.reschedule_once = False
            self.trigger('refresh')

    @native_procedure
    def run(self, *, context):
        self.calls.append('run')


@native_block_type(trigger_registry, has_state=True)
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
node first : TestLibrary.TriggerProbe;
node second : TestLibrary.TriggerProbe;
node shared : TestLibrary.SharedTriggerProbe;
node shared_again : TestLibrary.SharedTriggerProbe;
''')])
assert trigger_graph is not None
trigger_program = blocks.Program.compile(trigger_graph)
assert trigger_program is not None
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
    assert context.inputs['values'] == [7, 2]
    context.set_result('result', sum(context.inputs['values']))


probe = make_engine('''
implicit input : list value uint8;
implicit output : value uint8;

native procedure run
  depends on implicit input as "values"
  produces implicit output as "result";
''', check_list, extra='''
node first : Std.Constant(Type = uint8, Value = 7);
node second : Std.Constant(Type = uint8, Value = 2);
connect first, second -> probe;
''')
probe.step()
probe.assert_value_is('probe', 9)


def check_state(context):
    old = context.inputs['previous']
    context.set_result('next', old + 1)
    assert context.inputs['previous'] == old
    context.set_result('result', old)


probe = make_engine('''
state count : uint8 = 0;
implicit output : value uint8;

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
state count : uint8 = 0;
implicit output : value uint8;

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
node child : Child;
implicit output : value uint8;

native procedure run
  depends on child.Value as "value"
  produces implicit output as "result";
''', forward_value, extra=f'''
resource type Session;

{singleton}node type Child {{
  output Service : resource Session;
  output Value : value uint8;

  node source : Std.Constant(Type = uint8, Value = 7);
  connect source -> Value;
}}

node another : TestLibrary.Probe;
''')
    probe.step()
    probe.assert_value_is('probe', 7)
    probe.assert_value_is('another', 7)


def check_result_errors(context):
    with checks.assertRaisesRegex(ValueError, 'Unknown result'):
        context.set_result('missing', 7)
    try:
        context.set_result('result', 'invalid')
    except ValueError as error:
        assert 'Invalid value' in str(error)
        assert 'check_result_errors' in [
            frame.name for frame in traceback.extract_tb(error.__traceback__)
        ]
    else:
        raise AssertionError('Invalid result was accepted')
    context.set_result('result', 7)
    with checks.assertRaisesRegex(ValueError, 'already written'):
        context.set_result('result', 8)


probe = make_engine('''
implicit output : value uint8;

native procedure run produces implicit output as "result";
''', check_result_errors)
probe.step()
probe.assert_value_is('probe', 7)

probe = make_engine('''
implicit output : value uint8;

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
parameter Type : ValueType;
implicit output : value Type;

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
implicit input : event {payload_type};
implicit output : event {payload_type};

native procedure run
  depends on implicit input as "event"
  produces implicit output as "result";
''', forward_event, extra=f'''
node source : Std.DebugEventSource(Type = {payload_type});
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
implicit output : value unit;

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
implicit output : event unit;

native procedure run produces implicit output as "result";
''', check_unit_event_result)
probe.step()
probe.assert_value_is('probe', blocks.Some(blocks.Unit()))


def check_keys(context):
    assert context.inputs['line\nkey'] == 7
    assert context.inputs[r'raw\key'] == 7
    context.set_result('', 7)


probe = make_engine(r'''
node source : Std.Constant(Type = uint8, Value = 7);
implicit output : value uint8;

native procedure run
  depends on source as "line\nkey", source as r"raw\key"
  produces implicit output as "";
''', check_keys)
probe.step()
probe.assert_value_is('probe', 7)


# Aliases are checked independently for input and result bindings.
probe = make_engine('''
node source : Std.Constant(Type = uint8, Value = 7);
implicit output : value uint8;

native procedure run
  depends on source as "value"
  produces implicit output as "value";
''', lambda context: context.set_result('value', context.inputs['value']))
probe.step()
probe.assert_value_is('probe', 7)


cleanup_observations = []
lifecycle_registry = {}


@native_block_type(lifecycle_registry, has_state=True)
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
    parameter Fail : boolean = false;
  }}
}}
node first : TestLibrary.LifecycleProbe;
node second : TestLibrary.LifecycleProbe(Fail = {'true' if fail else 'false'});
''')])
        assert graph is not None
        program = blocks.Program.compile(graph)
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
