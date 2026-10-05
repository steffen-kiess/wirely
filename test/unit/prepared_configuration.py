import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import wirely
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import program as program_compiler


libraries = _standard_library_sources(['std', 'path', 'knx', 'mqtt', 'two_n', 'time'])


def compile_program(source, *, filename='<prepared>'):
    graph = compile_strs(libraries + [(filename, source)])
    assert graph is not None
    return program_compiler.compile_nocatch(graph)


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
        with contextlib.redirect_stdout(io.StringIO()):
            runtime = wirely.Engine(program)
        state = runtime.get_native_state('checked')
        assert state.address.raw == 2563
        assert state.value_type_knx == {
            'boolean': 'binary', 'int16': '2byte_signed',
            'uint16': '2byte_unsigned', 'int32': '4byte_signed',
            'uint32': '4byte_unsigned',
        }[ty]


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


def credentials_source(expression):
    return f'''node checked: TwoN.Connection {{
  Host: "localhost", TLSA: [], UsernamePasswordFile: {expression},
}}
'''


# Engines consume prepared credentials without reading the file again.
with tempfile.TemporaryDirectory() as directory:
    filename = Path(directory) / 'credentials.txt'
    main = str(Path(directory) / 'main.wrl')
    absolute = json.dumps(str(filename))
    constructed = 'Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt")'
    for expression, source_filename in [(absolute, '<prepared>'), (constructed, main)]:
        filename.write_text(' username \n password \nignored\n')
        program = compile_program(credentials_source(expression), filename=source_filename)
        filename.unlink()
        output = io.StringIO()
        with patch('wirely.two_n.client.Client') as client, contextlib.redirect_stdout(output):
            wirely.Engine(program)
        client.assert_called_once_with(hostname='localhost', port=443, tlsa=(),
                                       http_api_username_password=('username', 'password'))
        assert 'username' not in output.getvalue() and 'password' not in output.getvalue()
