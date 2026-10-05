import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.native import native_block_type


registry = {}


@native_block_type(registry, has_native_state=True)
class ParsedProbe(wirely.NativeState):
    pass
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import model as blocks
from wirely.compiler import program as program_compiler


checks = TestCase()
libraries = _standard_library_sources(['std', 'path', 'knx', 'mqtt', 'two_n', 'time'])


def compile_program(source, *, filename='<prepared>', unused=False):
    sources = libraries
    if unused:
        # Constants and assertions still run without initializer consumers.
        sources = [(name, text.replace(
            'uses Type, group_address, value_type_name', ''
        ).replace(
            'uses Type, Topic, parameter_option(Format) as Format, template_parts', ''
        ).replace(
            'uses Type, Topic, parameter_option(Format) as Format', ''
        ).replace('uses Host, Port, TLSA, credentials', ''))
                   for name, text in sources]
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = compile_strs(sources + [(filename, source)])
        assert graph is not None
        return program_compiler.compile_nocatch(graph)


# Struct results and all public KNX mappings pass through typed conversion.
source = '''namespace TestLibrary {
  native node type ParsedProbe {
    parameter Parts: MQTT.TemplateParts = MQTT.ParseTemplate(None);
    parameter Name: String = "";
    native initializer uses Parts, Name;
  }
}
node absent: TestLibrary.ParsedProbe {}
assert MQTT.SupportsType(boolean);
assert !MQTT.SupportsType(Option[uint16]);
assert !MQTT.SupportsType(MonotonicTime);
'''
for ty, name in [('boolean', 'binary'), ('int16', '2byte_signed'),
                 ('uint16', '2byte_unsigned'), ('int32', '4byte_signed'),
                 ('uint32', '4byte_unsigned')]:
    source += f'node name_{ty}: TestLibrary.ParsedProbe {{ Name: KNX.ValueTypeName({ty}) }}\n'
    source += f'assert KNX.SupportsValueType({ty}) == {str(ty in ("boolean", "uint16")).lower()};\n'
for address, raw in [('1/2/3', 2563), ('1/515', 2563), ('2563', 2563),
                     ('0/0/0', 0), ('31/7/255', 65535)]:
    source += f'assert KNX.ParseGroupAddress({json.dumps(address)}) == {raw};\n'
templates = [('%s', '', ''), ('[%% %s %%]', '[% ', ' %]'),
             ('{"quoted":"%s", "value":%s}', '{"quoted":"%s", "value":', '}'),
             (r'{"escaped":"a\"%s", "value":%s}', r'{"escaped":"a\"%s", "value":', '}')]
for index, (template, _, _) in enumerate(templates):
    source += f'node template{index}: TestLibrary.ParsedProbe {{ Parts: MQTT.ParseTemplate(Some({json.dumps(template)})) }}\n'
program = compile_program(source)
assert dict(program.instances['absent'].initializer_values['Parts']) == {'prefix': '', 'suffix': ''}
for ty, name in [('boolean', 'binary'), ('int16', '2byte_signed'),
                 ('uint16', '2byte_unsigned'), ('int32', '4byte_signed'),
                 ('uint32', '4byte_unsigned')]:
    assert program.instances[f'name_{ty}'].initializer_values['Name'] == name
for index, (_, prefix, suffix) in enumerate(templates):
    assert dict(program.instances[f'template{index}'].initializer_values['Parts']) == {
        'prefix': prefix, 'suffix': suffix,
    }


for address in ('invalid', '1/2/3/4', '1/2/256', '32/0/0', '1/2048', '65536'):
    with checks.assertRaises(blocks.ProgramError) as caught:
        compile_program(f'constant Address: uint16 = KNX.ParseGroupAddress({json.dumps(address)});')
    assert 'CouldNotParseAddress' in str(caught.exception)
    assert caught.exception.source_element.source_position.filename == '<prepared>'
    assert caught.exception.source_element.value == address


def knx_source(block, ty, address='1/2/3'):
    incoming = block in ('ValueOptionProvider', 'EventSender')
    source = f'''node bus: KNX.Connection {{ UnixPath: "/tmp/unused-knx" }}
node checked: KNX.{block} {{ Type: {ty}, GroupAddress: {json.dumps(address)} }}
connect bus -> checked.Bus;
'''
    if incoming:
        connector = f'Option[{ty}]' if block == 'ValueOptionProvider' else ty
        debug = 'DebugValueSource' if block == 'ValueOptionProvider' else 'DebugEventSource'
        source += f'node input: Std.{debug} {{ Type: {connector} }}\nconnect input -> checked;\n'
    return source


for block in ('ValueReceiver', 'ValueOptionProvider', 'EventReceiver', 'EventSender'):
    types = ['boolean', 'uint16'] if block.startswith('Value') else ['boolean', 'int16', 'uint16', 'int32', 'uint32']
    for ty in types:
        program = compile_program(knx_source(block, ty))
        values = program.instances['checked'].initializer_values
        assert values['group_address'] == 2563
        assert values['Type'] == getattr(wirely.value_types, ty)
        assert program.instances['checked'].configuration is None
        with contextlib.redirect_stdout(io.StringIO()):
            runtime = wirely.Engine(program)
        state = runtime.get_native_state('checked')
        assert state.address.raw == 2563
        assert state.value_type_knx == values['value_type_name']
    for unused in (False, True):
        with checks.assertRaises(blocks.ProgramError) as caught:
            compile_program(knx_source(block, 'boolean', 'invalid'), unused=unused)
        assert 'CouldNotParseAddress' in str(caught.exception)
        assert caught.exception.source_element.source_position.filename.endswith('knx.wrl')
        assert str(caught.exception.hints[-1]) == "While instantiating 'checked'"
        if block.startswith('Value'):
            for ty in ('int16', 'int32', 'uint32'):
                with checks.assertRaises(blocks.ProgramError) as caught:
                    compile_program(knx_source(block, ty), unused=unused)
                assert 'Unsupported value type for KNX' in str(caught.exception)


def mqtt_source(block, *, ty='boolean', template=None, format=None):
    args = f'Type: {ty}, Topic: "topic"'
    if template is not None:
        args += ', Template: ' + json.dumps(template)
    if format is not None:
        args += ', Format: ' + json.dumps(format)
    source = f'''node broker: MQTT.Connection {{ URI: "mqtt://localhost" }}
node checked: MQTT.{block} {{ {args} }}
connect broker -> checked.Broker;
'''
    if block != 'EventReceiver':
        connector = f'Option[{ty}]' if block == 'ValueOptionProvider' else ty
        debug = 'DebugValueSource' if block == 'ValueOptionProvider' else 'DebugEventSource'
        source += f'node input: Std.{debug} {{ Type: {connector} }}\nconnect input -> checked;\n'
    return source


for block in ('ValueOptionProvider', 'EventSender', 'EventReceiver'):
    for format in (None, 'prepared-format'):
        template = None if block == 'EventReceiver' else '[%s]'
        program = compile_program(mqtt_source(block, template=template, format=format))
        values = program.instances['checked'].initializer_values
        assert values['Format'] == (None if format is None else wirely.Some(format))
        assert program.instances['checked'].configuration is None
        with contextlib.redirect_stdout(io.StringIO()):
            runtime = wirely.Engine(program)
        state = runtime.get_native_state('checked')
        assert state.format == format
        assert state.template_prefix == ('' if block == 'EventReceiver' else '[')
        assert state.template_suffix == ('' if block == 'EventReceiver' else ']')
        if block != 'EventReceiver' and format is None:
            input = runtime.get_native_state('input')
            if block == 'ValueOptionProvider':
                input.set_value(wirely.Some(True))
            else:
                input.send_event(wirely.Some(True))
            runtime.step()
            payload = state.value_to_send if block == 'ValueOptionProvider' else state.values_to_send[-1]
            assert payload == b'[true]'
    if block != 'EventReceiver':
        program = compile_program(mqtt_source(block))
        assert dict(program.instances['checked'].initializer_values['template_parts']) == {'prefix': '', 'suffix': ''}
        for template, message in [('', 'without %s'), ('"%s"', 'without %s'),
                                  ('%s%s', 'multiple %s'), ('%x', 'Unexpected sequence'),
                                  ('%s%', 'lone %')]:
            for unused in (False, True):
                with checks.assertRaises(blocks.ProgramError) as caught:
                    compile_program(mqtt_source(block, template=template), unused=unused)
                assert message in str(caught.exception)
                assert caught.exception.source_element.source_position.filename.endswith('mqtt.wrl')
                assert str(caught.exception.hints[-1]) == "While instantiating 'checked'"
    for unused in (False, True):
        with checks.assertRaises(blocks.ProgramError) as caught:
            compile_program(mqtt_source(block, ty='MonotonicTime'), unused=unused)
        assert 'Unsupported type for MQTT' in str(caught.exception)


def credentials_source(expression):
    return f'''node checked: TwoN.Connection {{
  Host: "localhost", TLSA: [], UsernamePasswordFile: {expression},
}}
'''


with tempfile.TemporaryDirectory() as directory:
    filename = Path(directory) / 'credentials.txt'
    main = str(Path(directory) / 'main.wrl')
    absolute = json.dumps(str(filename))
    constructed = 'Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt")'
    for expression, source_filename in [(absolute, '<prepared>'), (constructed, main)]:
        filename.write_text(' username \n password \nignored\n')
        program = compile_program(credentials_source(expression), filename=source_filename)
        values = program.instances['checked'].initializer_values
        assert dict(values['credentials']) == {'username': 'username', 'password': 'password'}
        assert program.instances['checked'].configuration is None
        filename.unlink()
        output = io.StringIO()
        with patch('wirely.two_n.client.Client') as client, contextlib.redirect_stdout(output):
            runtime = wirely.Engine(program)
        client.assert_called_once_with(hostname='localhost', port=443, tlsa=(),
                                       http_api_username_password=('username', 'password'))
        assert 'username' not in output.getvalue() and 'password' not in output.getvalue()
        for unused in (False, True):
            with checks.assertRaises(blocks.ProgramError) as caught:
                compile_program(credentials_source(expression), filename=source_filename, unused=unused)
            assert 'No such file' in str(caught.exception)
            assert caught.exception.source_element.source_position.filename.endswith('two_n.wrl')
            assert str(caught.exception.hints[-1]) == "While instantiating 'checked'"
        for contents in ('', 'username\n', '\npassword\n'):
            filename.write_text(contents)
            with checks.assertRaises(blocks.ProgramError) as caught:
                compile_program(credentials_source(expression), filename=source_filename)
            assert 'Failed to read username and password' in str(caught.exception)
    filename.write_text('forwarded_user\nforwarded_password\n')
    forwarded = f'''node type Wrapper {{
  parameter File: String;
  node child: TwoN.Connection {{
    Host: "localhost", TLSA: [], UsernamePasswordFile: File,
  }}
}}
node wrapper: Wrapper {{ File: {constructed} }}
'''
    program = compile_program(forwarded, filename=main)
    assert program.instances['wrapper.child'].initializer_values['credentials']['username'] == 'forwarded_user'
    filename.unlink()
    with checks.assertRaises(blocks.ProgramError) as caught:
        compile_program(forwarded, filename=main)
    assert str(caught.exception.hints[-1]) == "While instantiating 'wrapper.child'"
    with patch('builtins.open', side_effect=AssertionError('Unexpected file read')):
        compile_program('''node type Unused {
  node device: TwoN.Connection {
    Host: "localhost", TLSA: [], UsernamePasswordFile: "/missing/credentials",
  }
}
''')
    for relative in ('credentials.txt', '../credentials.txt', ''):
        with checks.assertRaises(blocks.ProgramError) as caught:
            compile_program(credentials_source(json.dumps(relative)))
        assert 'must be absolute' in str(caught.exception)
        assert 'Path.Join' in str(caught.exception)
