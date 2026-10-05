import datetime

import wirely
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import program as program_compiler


def compile_program(source):
    libraries = _standard_library_sources(['std', 'time', 'memory'])
    graph = compile_strs(libraries + [('<optional-outputs>', source)])
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
