import contextlib
import io
import json
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import model as compiler_model
from wirely.compiler import program as program_compiler
from wirely.native import native_block_type


checks = TestCase()
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


def compile_program(source, *, unused_settings=False):
    libraries = _standard_library_sources(['std', 'time', 'csv', 'process', 'gpio', 'two_n'])
    if unused_settings:
        # Assertions must not depend on a procedure or initializer consuming
        # the setting. Keep the real declarations, with their bindings omitted.
        clauses = [' uses EdgeType', ' uses ChipDeviceFile, Name, ActiveState, Drive, Bias',
                   ' uses Filename', ' uses Start, End', ' uses Code', ' uses Type']
        for clause in clauses:
            libraries = [(filename, text.replace(clause, '')) for filename, text in libraries]
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = compile_strs(libraries + [('<settings>', source)])
        assert graph is not None
        return program_compiler.compile_nocatch(graph)


def block_program(block, arguments, *, forward=None, unused_settings=False, connect_inputs=True):
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
    if connect_inputs and block in ('Std.DetectEdge', 'GPIO.Output', 'Time.DebounceStable', 'CSV.ValueWriter'):
        value_type = arguments.get('Type', 'boolean')
        prefix += f'node input: Std.DebugValueSource {{ Type: {value_type} }}\n'
        connections += f'connect input -> {external};\n'
        connectors += f'  default input: value {value_type};\n'
        internal += '  connect default input -> child;\n'
    elif connect_inputs and block in ('Process.LaunchOnEvent', 'TwoN.OutputControl'):
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
    return compile_program(prefix + source + connections, unused_settings=unused_settings), target


def procedure(program, node, name):
    return next(proc for schedule in program.procs.values() for proc in schedule
                if proc.node_id == node and proc.reference[1] == name)


def reject(block, arguments, message, library, *, forward=None):
    for unused in (False, True):
        with checks.assertRaises(compiler_model.ProgramError) as caught:
            block_program(block, arguments, forward=forward, unused_settings=unused, connect_inputs=False)
        error = caught.exception
        assert type(error) is compiler_model.ProgramError
        assert str(error) == 'Compile-time assertion failed: ' + message
        position = error.source_element.source_position
        assert Path(position.filename).name == library + '.wrl'
        assert 'assert ' in position.input_data.splitlines()[position.start_line - 1]
        assert any(str(hint) == ("While instantiating 'wrapper.child'" if forward else
                                 "While instantiating 'checked'") for hint in error.hints)


# Each edge choice compiles, including forwarded settings; unfinished modes
# are deliberately not run.
for edge in ('Rising', 'Falling', 'Both'):
    program, node = block_program('Std.DetectEdge', {'EdgeType': json.dumps(edge)},
                                  forward=('EdgeType', 'String'))
    assert dict(procedure(program, node, 'run').values) == {'EdgeType': edge}
    assert program.instances[node].configuration is None
reject('Std.DetectEdge', {'EdgeType': '"invalid"'},
       'EdgeType must be Rising, Falling, or Both', 'std', forward=('EdgeType', 'String'))

# Defaults and every GPIO choice arrive in the initializer.
gpio = {'ChipDeviceFile': '"/dev/gpiochip-test"', 'Name': '"LED"'}
program, node = block_program('GPIO.Output', gpio)
assert dict(program.instances[node].initializer_values) == {
    'ChipDeviceFile': '/dev/gpiochip-test', 'Name': 'LED',
    'ActiveState': 'High', 'Drive': 'PushPull', 'Bias': 'AsIs',
}
choices = {
    'ActiveState': (('High', 'Low'), 'ActiveState must be High or Low'),
    'Drive': (('PushPull', 'OpenDrain', 'OpenSource'),
              'Drive must be PushPull, OpenDrain, or OpenSource'),
    'Bias': (('AsIs', 'Disable', 'PullDown', 'PullUp'),
             'Bias must be AsIs, Disable, PullDown, or PullUp'),
}
for parameter, (allowed, message) in choices.items():
    for value in allowed:
        program, node = block_program('GPIO.Output', {**gpio, parameter: json.dumps(value)},
                                      forward=(parameter, 'String'))
        assert program.instances[node].initializer_values[parameter] == value
    reject('GPIO.Output', {**gpio, parameter: '"invalid"'}, message, 'gpio',
           forward=(parameter, 'String'))

# CSV's type stays on the procedure and its filename belongs to initialization.
for value_type in ('boolean', 'uint16', 'Option[boolean]'):
    program, node = block_program('CSV.ValueWriter', {'Type': value_type, 'Filename': '"log.csv"'},
                                  forward=('Filename', 'String'))
    assert dict(program.instances[node].initializer_values) == {'Filename': 'log.csv'}
    assert procedure(program, node, 'run').values['Type'].supports_csv
compile_program('assert !CSV.SupportsType(List[uint8]); assert !CSV.SupportsType(String);')
for value_type in ('MonotonicTime', 'unit', 'Duration', 'TimeOfDay', 'Option[unit]'):
    reject('CSV.ValueWriter', {'Type': value_type, 'Filename': '"log.csv"'},
           'Type must support CSV conversion', 'csv')

program, node = block_program('Process.LaunchOnEvent', {'ShellCommand': '"true"'},
                              forward=('ShellCommand', 'String'))
assert dict(program.instances[node].initializer_values) == {'ShellCommand': 'true'}
assert program.instances[node].parameters['Type'] == wirely.value_types.unit
assert not procedure(program, node, 'run').values
runtime = wirely.Engine(program)
assert runtime.get_native_state(node).values['ShellCommand'] == 'true'

program, node = block_program('Time.DebounceStable', {'Type': 'uint16', 'Duration': '"PT2S"'},
                              forward=('Duration', 'Duration'))
assert dict(procedure(program, node, 'run').values) == {'Duration': wirely.Duration.parse('PT2S')}
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

for start, end in [('08:00', '12:00'), ('22:00', '06:00')]:
    program, node = block_program('Time.IsInDailyInterval',
                                  {'Start': json.dumps(start), 'End': json.dumps(end)},
                                  forward=('End', 'TimeOfDay'))
    assert dict(procedure(program, node, 'run').values) == {
        'Start': wirely.value_types.time_of_day.parse(start),
        'End': wirely.value_types.time_of_day.parse(end),
    }
reject('Time.IsInDailyInterval', {'Start': '"12:00"', 'End': '"12:00"'},
       'Start and End must differ', 'time', forward=('End', 'TimeOfDay'))

# Compile against a resource provider without preparing a TwoN connection.
for code in ('', '123456789012345'):
    program, node = block_program('TwoN.CodeEnteredEvent', {'Code': json.dumps(code)},
                                  forward=('Code', 'String'))
    assert dict(program.instances[node].initializer_values) == {'Code': code}
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        runtime = wirely.Engine(program)
    state = runtime.get_native_state(node)
    state.handler({'event': 'CodeEntered', 'params': {'code': code}})
    runtime.step()
    runtime.assert_value_is(node, wirely.Some(wirely.Unit()))
    runtime.step()
    runtime.assert_value_is(node, None)
for code in ('1234567890123456', '123x', '１２３', '١٢٣'):
    reject('TwoN.CodeEnteredEvent', {'Code': json.dumps(code, ensure_ascii=False)},
           'Code must contain at most 15 ASCII digits', 'two_n', forward=('Code', 'String'))

for block, parameter, value, value_type in [
    ('TwoN.KeyEvent', 'Key', '"%2"', 'String'),
    ('TwoN.IOState', 'Port', '"ext3.input1"', 'String'),
    ('TwoN.SwitchState', 'SwitchNumber', '2', 'int32'),
    ('TwoN.OutputControl', 'Port', '"led"', 'String'),
]:
    program, node = block_program(block, {parameter: value}, forward=(parameter, value_type))
    expected = json.loads(value)
    assert dict(program.instances[node].initializer_values) == {parameter: expected}
    assert program.instances[node].initializer_bindings == (('Device', ('device',), False),)
    assert program.native_construction_order.index('device') < program.native_construction_order.index(node)
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        runtime = wirely.Engine(program)
    assert dict(runtime.get_native_state(node).values) == {parameter: expected}

for timeout in (None, 'PT45S'):
    arguments = {'Key': '"%2"'}
    if timeout:
        arguments['Timeout'] = json.dumps(timeout)
    program, node = block_program('TwoN.KeyState', arguments,
                                  forward=('Timeout', 'Duration') if timeout else None)
    assert dict(procedure(program, node, 'get_value').values) == {
        'Timeout': wirely.Duration.parse(timeout or 'PT30S'),
    }
    assert dict(program.instances[node + '.key_event'].initializer_values) == {'Key': '%2'}

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

# A valid sibling cannot suppress checks on another instance of the same type.
with checks.assertRaises(compiler_model.ProgramError) as caught:
    compile_program('''node type Wrapper {
  parameter Edge: String;
  node child: Std.DetectEdge { EdgeType: Edge }
}
node good: Wrapper { Edge: "Falling" }
node bad: Wrapper { Edge: "invalid" }
''')
assert str(caught.exception) == (
    'Compile-time assertion failed: EdgeType must be Rising, Falling, or Both'
)
assert [str(hint) for hint in caught.exception.hints] == ["While instantiating 'bad.child'"]
