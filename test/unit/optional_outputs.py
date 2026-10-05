import datetime
from pathlib import Path
from unittest import TestCase

import wirely
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import model as compiler_model
from wirely.compiler import program as program_compiler


checks = TestCase()
message = 'Compile-time assertion failed: OutputValue is required when OutputType is not unit'


def compile_program(source, *, unused_values=False):
    libraries = _standard_library_sources(['std', 'time', 'memory'])
    if unused_values:
        # Assertions must run even with no procedure consuming the parameters.
        libraries = [(filename, text.replace(
            'uses parameter_option(OutputValue) as OutputValue, MinimumDelay', ''
        ).replace('uses parameter_option(OutputValue) as OutputValue', ''))
                     for filename, text in libraries]
    graph = compile_strs(libraries + [('<optional-outputs>', source)])
    assert graph is not None
    return program_compiler.compile_nocatch(graph)


def block_program(block, output_type, output_value, *, forward=False,
                  unused_values=False):
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
    return compile_program(source, unused_values=unused_values), target


def procedure(program, node, name):
    return next(proc for schedule in program.procs.values() for proc in schedule
                if proc.node_id == node and proc.reference[1] == name)


cases = [
    (None, None, wirely.Unit()),
    ('unit', None, wirely.Unit()),
    ('unit', '()', wirely.Unit()),
    ('boolean', 'false', False),
    ('uint16', '0', 0),
    ('uint16', '42', 42),
    ('Option[boolean]', 'None', None),
    ('Option[boolean]', 'Some(false)', wirely.Some(False)),
]
for block in ('Time.TriggerAt', 'Memory.ToggleFlipFlopTrigger'):
    for forward in (False, True):
        for output_type, output_value, expected in cases:
            program, target = block_program(block, output_type, output_value, forward=forward)
            name = 'run' if block == 'Time.TriggerAt' else 'update'
            values = {'OutputValue': None if output_value is None else wirely.Some(expected)}
            if block == 'Memory.ToggleFlipFlopTrigger':
                values['MinimumDelay'] = wirely.Duration.parse('PT2S')
                assert not procedure(program, target, 'read_time').values
            assert dict(procedure(program, target, name).values) == values
            runtime = wirely.Engine(program)
            if block == 'Time.TriggerAt':
                clock = runtime.get_native_state_singleton('Time.TimeOfDayMinute')
                for time, event in [('18:00', None), ('11:59', None),
                                    ('12:00', wirely.Some(expected)), ('12:01', None)]:
                    clock.set_fake_time(datetime.time.fromisoformat(time))
                    runtime.step()
                    runtime.assert_value_is(target, event)
                    runtime.step()
                    runtime.assert_value_is(target, None)
                    runtime.assert_is_idle()
            else:
                feedback = runtime.get_native_state('feedback')
                inp = runtime.get_native_state('input')
                feedback.set_value(wirely.Some(False))
                runtime.step()
                inp.send_event(wirely.Some(True))
                runtime.step()
                runtime.assert_value_is(target, wirely.Some(expected))
                runtime.step()
                runtime.assert_value_is(target, None)
                # A pending change waits for the bound two-second delay.
                runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
                inp.send_event(wirely.Some(True))
                runtime.step()
                runtime.assert_value_is(target, None)
                runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1.1))
                runtime.step()
                runtime.assert_value_is(target, wirely.Some(expected))
                runtime.step()
                runtime.assert_value_is(target, None)
                runtime.assert_is_idle()

    for forward in (False, True):
        for unused_values in (False, True):
            with checks.assertRaises(compiler_model.ProgramError) as caught:
                block_program(block, 'boolean', None, forward=forward,
                              unused_values=unused_values)
            error = caught.exception
            assert type(error) is compiler_model.ProgramError
            assert str(error) == message
            position = error.source_element.source_position
            assert Path(position.filename).name == block.split('.')[0].lower() + '.wrl'
            assert 'assert ' in position.input_data.splitlines()[position.start_line - 1]
            target = 'wrapper.child' if forward else 'checked'
            assert any(str(hint) == f"While instantiating '{target}'" for hint in error.hints)
