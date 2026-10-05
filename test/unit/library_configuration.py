import contextlib
import datetime
import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import program as program_compiler
from wirely.native import native_block_type


registry = {}
libraries = _standard_library_sources(['std', 'time', 'memory', 'two_n', 'path'])


@native_block_type(registry, has_native_state=True)
class SettingsDevice(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        self.log_receiver = self
        self.handlers = []

    def add_handler(self, events, handler):
        self.handlers.append((events, handler))


def compile_program(source, *, filename='<configuration>'):
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = compile_strs(libraries + [(filename, source)])
        assert graph is not None
        return program_compiler.compile_nocatch(graph)


def block_program(block, output_type, output_value, *, forward=False):
    settings = []
    if output_type is not None:
        settings.append('OutputType: ' + output_type)
    if output_value is not None:
        settings.append('OutputValue: ' + output_value)
    child_settings = ['Time: "12:00"'] if block == 'Time.TriggerAt' else []
    if block == 'Memory.ToggleFlipFlopTrigger':
        child_settings.append('MinimumDelay: "PT2S"')
    if forward:
        child_settings += ['OutputType: Type', 'OutputValue?: parameter_option(Value)']
        connectors = ''
        connections = ''
        if block == 'Memory.ToggleFlipFlopTrigger':
            connectors = '  default input: event boolean;\n  input Feedback: value Option[boolean];\n'
            connections = '  connect default input -> child;\n  connect Feedback -> child.Feedback;\n'
        arguments = ', '.join(settings).replace('OutputType:', 'Type:').replace('OutputValue:', 'Value:')
        source = f'''node type Wrapper {{
  parameter Type: ValueType = unit;
  optional parameter Value: Type;
{connectors}  node child: {block} {{ {', '.join(child_settings)} }}
{connections}}}
node wrapper: Wrapper {{ {arguments} }}
'''
        target = 'wrapper.child'
    else:
        source = f'node checked: {block} {{ {", ".join(child_settings + settings)} }}\n'
        target = 'checked'
    if block == 'Memory.ToggleFlipFlopTrigger':
        external = 'wrapper' if forward else target
        source += f'''node input: Std.DebugEventSource {{ Type: boolean }}
node feedback: Std.DebugValueSource {{ Type: Option[boolean] }}
connect input -> {external};
connect feedback -> {external}.Feedback;
'''
    return compile_program(source), target


def check_event_payloads():
    cases = [(None, None, wirely.Unit()), ('boolean', 'false', False),
             ('uint16', '0', 0), ('Option[boolean]', 'None', None),
             ('Option[boolean]', 'Some(false)', wirely.Some(False))]
    for block in ('Time.TriggerAt', 'Memory.ToggleFlipFlopTrigger'):
        # A supplied false-like value must survive optional parameter forwarding.
        scenarios = [(False, case) for case in cases] + [(True, cases[1])]
        for forward, (output_type, output_value, expected) in scenarios:
            program, target = block_program(block, output_type, output_value, forward=forward)
            runtime = wirely.Engine(program)
            if block == 'Time.TriggerAt':
                clock = runtime.get_native_state_singleton('Time.TimeOfDayMinute')
                clock.set_fake_time(datetime.time(11, 59))
                runtime.step()
                runtime.assert_value_is(target, None)
                clock.set_fake_time(datetime.time(12))
            else:
                runtime.get_native_state('feedback').set_value(wirely.Some(False))
                runtime.step()
                runtime.get_native_state('input').send_event(wirely.Some(True))
            runtime.step()
            runtime.assert_value_is(target, wirely.Some(expected))
            runtime.step()
            runtime.assert_value_is(target, None)
            runtime.assert_is_idle()


def check_delayed_event():
    program, target = block_program('Memory.ToggleFlipFlopTrigger', 'boolean', 'false', forward=True)
    runtime = wirely.Engine(program)
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


def check_debounce_duration():
    program = compile_program('''node type Wrapper {
  parameter Span: Duration;
  default input: value uint16;
  default output: value uint16;
  node child: Time.DebounceStable { Type: uint16, Duration: Span }
  connect default input -> child;
  connect child -> default output;
}
node input: Std.DebugValueSource { Type: uint16 }
node wrapper: Wrapper { Span: "PT2S" }
connect input -> wrapper;
''')
    runtime = wirely.Engine(program)
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


def code_program(block, parameter, value):
    return compile_program(f'''namespace TestLibrary {{
  native node type SettingsDevice {{
    default output: resource TwoN.Device;
  }}
}}
node type Wrapper {{
  parameter Value: String;
  input Device: resource TwoN.Device;
  node child: TwoN.{block} {{ {parameter}: Value }}
  connect Device -> child.Device;
}}
node device: TestLibrary.SettingsDevice {{}}
node wrapper: Wrapper {{ Value: {json.dumps(value)} }}
connect device -> wrapper.Device;
''')


def check_codes_and_tracing():
    for code in ('', '123456789012345'):
        program = code_program('CodeEnteredEvent', 'Code', code)
        with patch.dict(wirely.native.test_library.native_block_types, registry):
            runtime = wirely.Engine(program)
        runtime.get_native_state('wrapper.child').handler({'event': 'CodeEntered', 'params': {'code': code}})
        runtime.step()
        runtime.assert_value_is('wrapper.child', wirely.Some(wirely.Unit()))
        runtime.step()
        runtime.assert_value_is('wrapper.child', None)
    for enabled in ('0', '1'):
        for block, parameter, value in [('KeyEvent', 'Key', '%secret'),
                                       ('CodeEnteredEvent', 'Code', '019283')]:
            program = code_program(block, parameter, value)
            output = io.StringIO()
            with patch.dict(os.environ, {'WIRELY_TWON_TRACE_KEYS': enabled}), \
                    patch.dict(wirely.native.test_library.native_block_types, registry), \
                    contextlib.redirect_stdout(output):
                wirely.Engine(program)
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
            wirely.Engine(program)
        client.assert_called_once_with(hostname='localhost', port=443, tlsa=(),
                                       http_api_username_password=('username', 'password'))
        assert 'username' not in output.getvalue() and 'password' not in output.getvalue()


check_event_payloads()
check_delayed_event()
check_debounce_duration()
check_codes_and_tracing()
check_prepared_credentials()
