import contextlib
import io
import json
import os
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import program as program_compiler
from wirely.native import native_block_type


registry = {}


@native_block_type(registry, has_native_state=True)
class SettingsDevice(wirely.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        self.log_receiver = self
        self.handlers = []

    def add_handler(self, events, handler):
        self.handlers.append((events, handler))


device_source = '''namespace TestLibrary {
  native node type SettingsDevice {
    default output: resource TwoN.Device;
  }
}
node device: TestLibrary.SettingsDevice {}
'''


def compile_program(source):
    libraries = _standard_library_sources(['std', 'time', 'csv', 'process', 'gpio', 'two_n'])
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = compile_strs(libraries + [('<settings>', source)])
        assert graph is not None
        return program_compiler.compile_nocatch(graph)


def block_program(block, arguments, *, forward=None):
    prefix = ''
    connectors = ''
    internal = ''
    connections = ''
    target = 'checked'
    external = 'checked'
    if forward is not None:
        parameter, parameter_type = forward
        value = arguments[parameter]
        arguments = {**arguments, parameter: 'Value'}
        target = 'wrapper.child'
        external = 'wrapper'
    if block.startswith('TwoN.'):
        prefix = device_source
        connections += f'connect device -> {external}.Device;\n'
        connectors += '  input Device: resource TwoN.Device;\n'
        internal += '  connect Device -> child.Device;\n'
    if block in ('Std.DetectEdge', 'GPIO.Output', 'Time.DebounceStable', 'CSV.ValueWriter'):
        value_type = arguments.get('Type', 'boolean')
        prefix += f'node input: Std.DebugValueSource {{ Type: {value_type} }}\n'
        connections += f'connect input -> {external};\n'
        connectors += f'  default input: value {value_type};\n'
        internal += '  connect default input -> child;\n'
    elif block in ('Process.LaunchOnEvent', 'TwoN.OutputControl'):
        value_type = arguments.get('Type', 'boolean' if block == 'TwoN.OutputControl' else 'unit')
        prefix += f'node input: Std.DebugEventSource {{ Type: {value_type} }}\n'
        connections += f'connect input -> {external};\n'
        connectors += f'  default input: event {value_type};\n'
        internal += '  connect default input -> child;\n'
    settings = ', '.join(f'{key}: {setting}' for key, setting in arguments.items())
    if forward is None:
        source = f'node checked: {block} {{ {settings} }}\n'
    else:
        source = f'''node type Wrapper {{
  parameter Value: {parameter_type};
{connectors}  node child: {block} {{ {settings} }}
{internal}}}
node wrapper: Wrapper {{ Value: {value} }}
'''
    return compile_program(prefix + source + connections), target

program, node = block_program('Process.LaunchOnEvent', {'ShellCommand': '"true"'},
                              forward=('ShellCommand', 'String'))
runtime = wirely.Engine(program)
assert runtime.get_native_state(node).values['ShellCommand'] == 'true'

program, node = block_program('Time.DebounceStable', {'Type': 'uint16', 'Duration': '"PT2S"'},
                              forward=('Duration', 'Duration'))
runtime = wirely.Engine(program)
runtime.get_native_state('input').set_value(1)
runtime.step()
runtime.assert_value_is(node, 1)
runtime.step()
runtime.get_native_state('input').set_value(2)
runtime.step()
runtime.assert_value_is(node, 1)
runtime.advance_fake_monotonic_time(wirely.Duration.parse('PT3S'))
runtime.step()
runtime.assert_value_is(node, 2)

# Compile against a resource provider without preparing a TwoN connection.
for code in ('', '123456789012345'):
    program, node = block_program('TwoN.CodeEnteredEvent', {'Code': json.dumps(code)},
                                  forward=('Code', 'String'))
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        runtime = wirely.Engine(program)
    state = runtime.get_native_state(node)
    state.handler({'event': 'CodeEntered', 'params': {'code': code}})
    runtime.step()
    runtime.assert_value_is(node, wirely.Some(wirely.Unit()))
    runtime.step()
    runtime.assert_value_is(node, None)
for block, parameter, value, value_type in [
    ('TwoN.KeyEvent', 'Key', '"%2"', 'String'),
    ('TwoN.IOState', 'Port', '"ext3.input1"', 'String'),
    ('TwoN.SwitchState', 'SwitchNumber', '2', 'int32'),
    ('TwoN.OutputControl', 'Port', '"led"', 'String'),
]:
    program, node = block_program(block, {parameter: value}, forward=(parameter, value_type))
    expected = json.loads(value)
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        runtime = wirely.Engine(program)
    assert dict(runtime.get_native_state(node).values) == {parameter: expected}

# Key and code tracing remains opt-in.
for enabled in ('0', '1'):
    for block, parameter, value in [('TwoN.KeyEvent', 'Key', '%secret'),
                                   ('TwoN.CodeEnteredEvent', 'Code', '019283')]:
        program, node = block_program(block, {parameter: json.dumps(value)})
        output = io.StringIO()
        with patch.dict(os.environ, {'WIRELY_TWON_TRACE_KEYS': enabled}), \
                patch.dict(wirely.native.test_library.native_block_types, registry), \
                contextlib.redirect_stdout(output):
            wirely.Engine(program)
        assert (value in output.getvalue()) == (enabled == '1')
