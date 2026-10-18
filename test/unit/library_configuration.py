import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs, standard_library_sources
from wirely.compiler import program as program_compiler
from wirely.runtime.engine import Engine
from wirely.test import test_extension, test_contracts, test_engine


libraries = standard_library_sources(['std', 'schedule', 'timer', 'memory', 'two_n', 'path'])


def settings_engine(program):
    """Create an Engine whose TestLibrary.SettingsDevice provides TwoN.Device resources."""
    with test_contracts(program.graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class SettingsDevice(contracts.SettingsDeviceInitializer):
            def initialize(self, *, services):
                self.log_receiver = self
                self.handlers = []
                return contracts.SettingsDeviceInitializerResult(device=self)

            def add_handler(self, events, handler):
                self.handlers.append((events, handler))
        registry['initializers']['TestLibrary.SettingsDevice'] = SettingsDevice

        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources={'TwoN.Device': SettingsDevice}, contracts=contracts)
        return Engine(program, override_extensions=[bindings, resource_bindings])


def compile_program(source, *, filename='<configuration>'):
    graph = compile_strs(libraries + [(filename, source)])
    assert graph is not None
    return program_compiler.compile_nocatch(graph)


def node_program(node_type, output_type, output_value, *, forward=False, minimum_delay='PT2S'):
    settings = []
    if output_type is not None:
        settings.append('OutputType: ' + output_type)
    if output_value is not None:
        settings.append('OutputValue: ' + output_value)
    child_settings = ['Time: "12:00"'] if node_type == 'Schedule.TriggerAt' else []
    if node_type == 'Memory.ToggleFlipFlopTrigger':
        child_settings.append('MinimumDelay: ' + json.dumps(minimum_delay))
    if forward:
        child_settings += ['OutputType: Type', 'OutputValue?: optional(Value)']
        ports = ''
        connections = ''
        if node_type == 'Memory.ToggleFlipFlopTrigger':
            ports = '  default input: event boolean;\n  input Feedback: value Option[boolean];\n'
            connections = '  connect default input -> child;\n  connect Feedback -> child.Feedback;\n'
        arguments = ', '.join(settings).replace('OutputType:', 'Type:').replace('OutputValue:', 'Value:')
        source = f'''node type Wrapper {{
  parameter Type: ValueType = unit;
  optional parameter Value: Type;
{ports}  node child: {node_type} {{ {', '.join(child_settings)} }}
{connections}}}
node wrapper: Wrapper {{ {arguments} }}
'''
        target = 'wrapper.child'
    else:
        source = f'node checked: {node_type} {{ {", ".join(child_settings + settings)} }}\n'
        target = 'checked'
    if node_type == 'Memory.ToggleFlipFlopTrigger':
        external = 'wrapper' if forward else target
        source += f'''node input: Std.DebugEventSource {{ Type: boolean }}
node feedback: Std.DebugValueSource {{ Type: Option[boolean] }}
connect input -> {external};
connect feedback -> {external}.Feedback;
'''
    return compile_program(source), target


def check_event_payloads():
    cases = [(None, None, wirely.Unit()), ('unit', '()', wirely.Unit()), ('boolean', 'false', False),
             ('uint16', '0', 0), ('Option[boolean]', 'None', None),
             ('Option[boolean]', 'Some(false)', wirely.Some(False))]
    for node_type in ('Schedule.TriggerAt', 'Memory.ToggleFlipFlopTrigger'):
        # A supplied false-like value must survive optional parameter forwarding.
        scenarios = [(forward, case) for forward in (False, True) for case in cases]
        for forward, (output_type, output_value, expected) in scenarios:
            program, target = node_program(node_type, output_type, output_value, forward=forward)
            runtime = test_engine(program)
            if node_type == 'Schedule.TriggerAt':
                clock = runtime.get_native_state_singleton('Schedule.TimeOfDayMinute')
                clock.set_fake_time(wirely.TimeOfDay.from_parts(11, 59))
                runtime.step()
                runtime.assert_value_is(target, None)
                clock.set_fake_time(wirely.TimeOfDay.from_parts(12, 0))
            else:
                runtime.get_native_state('feedback').set_value(wirely.Some(False))
                runtime.step()
                runtime.get_native_state('input').send_event(wirely.Some(True))
            runtime.step()
            runtime.assert_value_is(target, wirely.Some(expected))
            runtime.step()
            runtime.assert_value_is(target, None)
            runtime.assert_is_idle()


def check_trigger_startup_after():
    program, target = node_program('Schedule.TriggerAt', None, None)
    runtime = test_engine(program)
    clock = runtime.get_native_state_singleton('Schedule.TimeOfDayMinute')
    # Starting after the scheduled time must not emit or re-arm on rollback.
    for time in ('12:30', '11:59', '12:00', '15:00', '11:59'):
        clock.set_fake_time(wirely.TimeOfDay.parse(time))
        runtime.step()
        runtime.assert_value_is(target, None)
        runtime.step()
        runtime.assert_is_idle()
    clock.set_fake_time(wirely.TimeOfDay.parse('12:00'))
    runtime.step()
    runtime.assert_value_is(target, wirely.Some(wirely.Unit()))
    runtime.step()
    runtime.assert_value_is(target, None)
    runtime.assert_is_idle()


def check_delayed_event():
    program, target = node_program('Memory.ToggleFlipFlopTrigger', 'boolean', 'false', forward=True)
    runtime = test_engine(program)
    runtime.get_native_state('feedback').set_value(wirely.Some(False))
    runtime.step()
    source = runtime.get_native_state('input')
    source.send_event(wirely.Some(True))
    runtime.step()
    runtime.assert_value_is(target, wirely.Some(False))
    runtime.step()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    source.send_event(wirely.Some(True))
    runtime.step()
    runtime.assert_value_is(target, None)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1.1))
    runtime.step()
    runtime.assert_value_is(target, wirely.Some(False))
    runtime.step()
    runtime.assert_value_is(target, None)
    runtime.assert_is_idle()


def check_flipflop_behavior():
    def make_runtime(minimum_delay='PT2S'):
        program, target = node_program('Memory.ToggleFlipFlopTrigger', None, None,
                                       minimum_delay=minimum_delay)
        runtime = test_engine(program)
        runtime.enable_fixed_monotonic_time()
        runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
        return runtime, target

    def command(value):
        runtime.get_native_state('input').send_event(wirely.Some(value))

    def feedback(value):
        runtime.get_native_state('feedback').set_value(value)

    def step(*, fire=False, settle=True):
        runtime.step()
        runtime.assert_value_is(target, wirely.Some(wirely.Unit()) if fire else None)
        if settle:
            runtime.step()
            runtime.assert_value_is(target, None)
            runtime.assert_is_idle()

    runtime, target = make_runtime()
    feedback(None)
    step()
    command(True)
    step()
    feedback(wirely.Some(False))
    step()
    command(False)
    step()
    command(True)
    step(fire=True)

    # A new matching command or matching feedback cancels the pending one.
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(True)
    step()
    command(False)
    step()
    command(True)
    step()
    feedback(wirely.Some(True))
    step()
    feedback(wirely.Some(False))
    step()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step()

    # Expiration consumes the pending command without needing another input.
    command(True)
    step(fire=True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(True)
    step()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step(fire=True)

    # A fresh command at expiration replaces the pending request.
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(True)
    step()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(False)
    step()
    command(True)
    step(fire=True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(True)
    step()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    feedback(wirely.Some(True))
    command(False)
    step(fire=True)

    # Feedback loss clears a pending command even if feedback returns.
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(False)
    step()
    feedback(None)
    step()
    feedback(wirely.Some(True))
    step()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step()

    # Clock loss drops stale requests, but accepts a fresh one immediately.
    command(False)
    step(fire=True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(False)
    step()
    runtime.advance_fake_monotonic_time_generation()
    step()
    command(False)
    step(fire=True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    command(False)
    step()
    runtime.advance_fake_monotonic_time_generation()
    command(False)
    step(fire=True)

    # With zero delay, identical commands in consecutive steps each emit.
    runtime, target = make_runtime('PT0S')
    feedback(wirely.Some(False))
    step()
    command(True)
    step(fire=True, settle=False)
    command(True)
    step(fire=True, settle=False)
    step()


def check_debounce_duration():
    program = compile_program('''node type Wrapper {
  parameter Span: Duration;
  default input: value uint16;
  default output: value uint16;
  node child: Timer.DebounceStable { Type: uint16, Duration: Span }
  connect default input -> child;
  connect child -> default output;
}
node input: Std.DebugValueSource { Type: uint16 }
node wrapper: Wrapper { Span: "PT2S" }
connect input -> wrapper;
''')
    runtime = test_engine(program)
    source = runtime.get_native_state('input')
    source.set_value(1)
    runtime.step()
    runtime.assert_value_is('wrapper', 1)
    source.set_value(2)
    runtime.step()
    runtime.assert_value_is('wrapper', 1)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(3))
    runtime.step()
    runtime.assert_value_is('wrapper', 2)


def check_debounce_behavior():
    def make_runtime(value_type='uint16', duration='PT10S'):
        program = compile_program(f'''node type Wrapper {{
  parameter Type: ValueType;
  parameter Span: Duration;
  default input: value Type;
  default output: value Type;
  node child: Timer.DebounceStable {{ Type: Type, Duration: Span }}
  connect default input -> child;
  connect child -> default output;
}}
node input: Std.DebugValueSource {{ Type: {value_type} }}
node wrapper: Wrapper {{ Type: {value_type}, Span: {json.dumps(duration)} }}
connect input -> wrapper;
''')
        runtime = test_engine(program)
        runtime.enable_fixed_monotonic_time()
        runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
        return runtime

    def step(expected, *, settle=True):
        runtime.step()
        runtime.assert_value_is('wrapper', expected)
        if settle:
            runtime.step()
            runtime.assert_value_is('wrapper', expected)
            runtime.assert_is_idle()

    # Fix the clock so expiration and generation changes are deterministic.
    runtime = make_runtime()
    source = runtime.get_native_state('input')
    source.set_value(1)
    step(1)
    source.set_value(2)
    step(1)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    step(1)
    # An identical pending input must not restart its interval.
    source.set_value(2)
    step(1)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step(2)

    # A new pending value restarts the interval.
    source.set_value(3)
    step(2)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    step(2)
    source.set_value(4)
    step(2)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step(2)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    step(4)

    # Returning to the current output cancels the pending change.
    source.set_value(5)
    step(4)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    source.set_value(4)
    step(4)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
    step(4)
    source.set_value(5)
    step(4)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    step(4)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step(5)

    # Clock loss accepts the pending input immediately and clears the wait.
    source.set_value(6)
    step(5)
    runtime.advance_fake_monotonic_time_generation()
    step(6)

    runtime = make_runtime(duration='PT0S')
    source = runtime.get_native_state('input')
    source.set_value(1)
    step(1)
    source.set_value(2)
    step(1, settle=False)
    step(2)

    # An absent Option payload is a value, distinct from no pending change.
    runtime = make_runtime('Option[boolean]', 'PT2S')
    source = runtime.get_native_state('input')
    source.set_value(None)
    step(None)
    source.set_value(wirely.Some(False))
    step(None)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
    step(wirely.Some(False))
    source.set_value(None)
    step(wirely.Some(False))
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
    step(None)


def code_program(node_type, parameter, value, *, parameters=None):
    arguments = {parameter: 'Value', **(parameters or {})}
    settings = ', '.join(f'{name}: {expression}' for name, expression in arguments.items())
    return compile_program(f'''namespace TestLibrary {{
  native node type SettingsDevice {{
    default output: resource TwoN.Device;
    native initializer produces default output as "device";
  }}
}}
node type Wrapper {{
  parameter Value: String;
  input Device: resource TwoN.Device;
  node child: TwoN.{node_type} {{ {settings} }}
  connect Device -> child.Device;
}}
node device: TestLibrary.SettingsDevice {{}}
node wrapper: Wrapper {{ Value: {json.dumps(value)} }}
connect device -> wrapper.Device;
''')


def check_key_state():
    def make_runtime(timeout='PT10S'):
        program = code_program('KeyState', 'Key', '%2', parameters={'Timeout': json.dumps(timeout)})
        runtime = settings_engine(program)
        runtime.enable_fixed_monotonic_time()
        runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
        return runtime

    def event(pressed):
        runtime.get_native_state('wrapper.child.key_event').handler({
            'event': 'KeyPressed' if pressed else 'KeyReleased',
            'params': {'key': '%2'},
        })

    def step(pressed, *, warning=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            runtime.step()
        runtime.assert_value_is('wrapper.child', pressed)
        expected = ('Warning: [wrapper.child.timeout_warning]: '
                    'TwoN.KeyState: Resetting key after timeout\n') if warning else ''
        assert output.getvalue() == expected, output.getvalue()

    # Fix the clock so deadline boundaries and generation changes are exact.
    runtime = make_runtime()
    step(False)
    runtime.assert_is_idle()
    event(True)
    step(True)
    step(True)
    runtime.assert_is_idle()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    step(True)
    runtime.assert_is_idle()
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step(False, warning=True)
    step(False)
    runtime.assert_is_idle()

    # A repeated press restarts the wait instead of using the old deadline.
    event(True)
    step(True)
    step(True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    step(True)
    event(True)
    step(True)
    step(True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
    step(True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
    step(False, warning=True)
    step(False)
    runtime.assert_is_idle()

    # A release coinciding with expiration is explicit, so it must not warn.
    event(True)
    step(True)
    step(True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
    event(False)
    step(False)
    step(False)
    runtime.assert_is_idle()

    # A press at expiration wins and starts another wait without a warning.
    event(True)
    step(True)
    step(True)
    runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
    event(True)
    step(True)
    step(True)
    runtime.assert_is_idle()

    # Losing the clock generation releases the key and warns only once.
    runtime.advance_fake_monotonic_time_generation()
    step(False, warning=True)
    step(False)
    runtime.assert_is_idle()

    # Zero duration still accepts the press in its event step.
    runtime = make_runtime('PT0S')
    step(False)
    event(True)
    step(True)
    step(False, warning=True)
    step(False)
    runtime.assert_is_idle()


def check_codes_and_tracing():
    for code in ('', '123456789012345'):
        program = code_program('CodeEnteredEvent', 'Code', code)
        runtime = settings_engine(program)
        runtime.get_native_state('wrapper.child').handler({'event': 'CodeEntered', 'params': {'code': code}})
        runtime.step()
        runtime.assert_value_is('wrapper.child', wirely.Some(wirely.Unit()))
        runtime.step()
        runtime.assert_value_is('wrapper.child', None)
    for enabled in ('0', '1'):
        for node_type, parameter, value in [('KeyEvent', 'Key', '%secret'),
                                       ('CodeEnteredEvent', 'Code', '019283')]:
            program = code_program(node_type, parameter, value)
            output = io.StringIO()
            with patch.dict(os.environ, {'WIRELY_TWON_TRACE_KEYS': enabled}), contextlib.redirect_stdout(output):
                settings_engine(program)
            assert (value in output.getvalue()) == (enabled == '1')


def check_prepared_credentials():
    with tempfile.TemporaryDirectory() as directory:
        filename = Path(directory) / 'credentials.txt'
        filename.write_text(' username \n password \nignored\n')
        source = '''node checked: TwoN.Connection {
  Host: "localhost", TLSA: [],
  UsernamePasswordFile: Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt"),
}
'''
        program = compile_program(source, filename=str(Path(directory) / 'main.wrl'))
        filename.unlink()
        output = io.StringIO()
        with patch('wirely.two_n.client.Client') as client, contextlib.redirect_stdout(output):
            test_engine(program)
        client.assert_called_once_with(hostname='localhost', port=443, tlsa=(),
                                       http_api_username_password=('username', 'password'))
        assert 'username' not in output.getvalue() and 'password' not in output.getvalue()


check_event_payloads()
check_trigger_startup_after()
check_delayed_event()
check_flipflop_behavior()
check_debounce_duration()
check_debounce_behavior()
check_key_state()
check_codes_and_tracing()
check_prepared_credentials()


async def run_test():
    pass
