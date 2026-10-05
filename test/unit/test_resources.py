import contextlib
import io
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import MagicMock, patch

from wirely.compiler import compile_strs, compile_files, model as blocks
from wirely.compiler import program as program_compiler
from wirely.compiler.function_implementations.test import (
    TEST_RESOURCES_SERVICE, replace_twon_host, replace_twon_port, replace_twon_tlsa,
)
from wirely.compiler.native_functions import native_function_impls
from wirely.test import (
    TestResourceProvider, run_test as runner_run_test, with_maybe_knxd, with_maybe_mqtt,
)


def graph(source, *, services=None):
    result = compile_strs([('<resources>', source)], add_std=True, services=services)
    assert result is not None
    return result


def compile_program(source, *, services=None):
    return program_compiler.compile_nocatch(graph(source, services=services))


class Resources:
    def __init__(self, path):
        self.path = path
        self.calls = 0
        self.mqtt_calls = 0
        self.output_calls = []
        self.twon_calls = 0
        self.twon_info = SimpleNamespace(
            host='127.0.0.1', port=1000 if path == '/first' else 2000,
            server=SimpleNamespace(tlsa=path),
        )

    def get_knx_path(self):
        self.calls += 1
        return self.path

    def get_mqtt_uri(self):
        self.mqtt_calls += 1
        return 'mqtt+unix://' + self.path

    def get_output_path(self, default):
        self.output_calls.append(default)
        return os.path.join(self.path, os.path.basename(default))


    def get_twon_info(self):
        self.twon_calls += 1
        return self.twon_info


# Defaults, nested scopes, and values derived from the selected path.
source = '''node type Wrapper {
  parameter Default: String;
  constant Selected: String = Test.ReplaceKNXPath(Default);
  node bus: KNX.Connection { UnixPath: Path.Join(Selected, "derived") }
}
node wrapped: Wrapper { Default: "/fallback" }
node other: KNX.Connection { UnixPath: Test.ReplaceKNXPath("/other") }
'''


def paths(prog):
    return {instance.name: instance.initializer_values['UnixPath'].value
            for instance in prog.instances.values()
            if instance.type_name == ('KNX', 'Connection')}


assert paths(compile_program(source)) == {
    'wrapped.bus': '/fallback/derived', 'other': '/other',
}
first = Resources('/first')
second = Resources('/second')
registrations = {TEST_RESOURCES_SERVICE: first}
first_graph = graph(source, services=registrations)
registrations[TEST_RESOURCES_SERVICE] = second
second_graph = graph(source, services=registrations)
# Generate in reverse order to catch a shared current-compilation context.
assert paths(program_compiler.compile_nocatch(second_graph)) == {
    'wrapped.bus': '/second/derived', 'other': '/second',
}
assert paths(program_compiler.compile_nocatch(first_graph)) == {
    'wrapped.bus': '/first/derived', 'other': '/first',
}
assert first.calls > 0 and second.calls > 0
assert paths(compile_program(source))['other'] == '/other'
assert not hasattr(program_compiler.compile_nocatch(first_graph), 'services')
runtime_program = replace(program_compiler.compile_nocatch(first_graph), graph=None)
dump = io.StringIO()
runtime_program.dump(file=dump)
assert TEST_RESOURCES_SERVICE not in dump.getvalue()


# MQTT and output replacements use the same compilation service.
other_source = '''node type Wrapper {
  parameter Filename: String;
  constant Selected: String = Test.ReplaceOutputPath(Filename);
  node input: Std.Constant { Type: boolean, Value: false }
  node writer: CSV.ValueWriter {
    Type: boolean,
    Filename: Path.Join(Selected, "derived"),
  }
  connect input -> writer;
}
node wrapped: Wrapper { Filename: "logs/report.csv" }
node broker: MQTT.Connection { URI: Test.ReplaceMQTTURI("mqtt://fallback") }
node other_broker: MQTT.Connection { URI: Test.ReplaceMQTTURI("mqtt://other") }
'''


def settings(prog):
    return {instance.name: dict(instance.initializer_values)
            for instance in prog.instances.values()
            if instance.type_name in (('MQTT', 'Connection'), ('CSV', 'ValueWriter'))}


fallback_settings = settings(compile_program(other_source))
assert fallback_settings['wrapped.writer']['Filename'] == 'logs/report.csv/derived'
assert fallback_settings['broker']['URI'] == 'mqtt://fallback'
assert fallback_settings['other_broker']['URI'] == 'mqtt://other'
first_other_graph = graph(other_source, services={TEST_RESOURCES_SERVICE: first})
second_other_graph = graph(other_source, services={TEST_RESOURCES_SERVICE: second})
for compiled, provider in ((second_other_graph, second), (first_other_graph, first)):
    selected = settings(program_compiler.compile_nocatch(compiled))
    assert selected['wrapped.writer']['Filename'] == provider.path + '/report.csv/derived'
    assert selected['broker']['URI'] == selected['other_broker']['URI'] == 'mqtt+unix://' + provider.path
    assert 'logs/report.csv' in provider.output_calls
    assert provider.mqtt_calls > 0


# All TwoN settings are extracted from the same cached server information.
credentials_path = os.path.abspath('test/wrl/username_password.txt')
twon_source = f'''constant Host: String = Test.ReplaceTwoNHost("fallback");
constant Port: uint16 = Test.ReplaceTwoNPort(443);
constant TLSA: List[String] = Test.ReplaceTwoNTLSA(["fallback-tlsa"]);
node device: TwoN.Connection {{
  Host: Host,
  Port: Port,
  TLSA: TLSA,
  UsernamePasswordFile: "{credentials_path}",
}}
'''


def device_settings(prog):
    return next(dict(instance.initializer_values) for instance in prog.instances.values()
                if instance.type_name == ('TwoN', 'Connection'))


fallback_device = device_settings(compile_program(twon_source))
assert fallback_device['Host'] == 'fallback'
assert fallback_device['Port'] == 443
assert fallback_device['TLSA'] == ('fallback-tlsa',)
first_twon_graph = graph(twon_source, services={TEST_RESOURCES_SERVICE: first})
second_twon_graph = graph(twon_source, services={TEST_RESOURCES_SERVICE: second})
for compiled, provider in ((second_twon_graph, second), (first_twon_graph, first)):
    selected = device_settings(program_compiler.compile_nocatch(compiled))
    assert selected['Host'] == provider.twon_info.host
    assert selected['Port'] == provider.twon_info.port
    assert selected['TLSA'] == (provider.twon_info.server.tlsa,)
    assert provider.twon_calls > 0


# A fresh ordinary compilation must not import the test server dependencies.
subprocess.run([sys.executable, '-c', '''import sys
from wirely.compiler import compile_files
from wirely.compiler.program import compile_nocatch
compiled = compile_files(['test/wrl/two_n.wrl'])
assert compiled is not None
compile_nocatch(compiled)
assert 'wirely.two_n.fake_server' not in sys.modules
assert 'aiohttp' not in sys.modules
assert 'OpenSSL' not in sys.modules
'''], check=True, capture_output=True, text=True)


# Whichever setting is evaluated first requests the same provider-owned server.
functions = (replace_twon_host, replace_twon_port, replace_twon_tlsa)
for first_function in functions:
    server = MagicMock()
    server.info = SimpleNamespace(host='127.0.0.1', port=1234, server=server)
    server.tlsa = 'fixture-tlsa'
    with contextlib.ExitStack() as stack, \
            patch('wirely.two_n.fake_server.Server', return_value=server) as construct:
        resources = TestResourceProvider(stack)
        context = SimpleNamespace(services={TEST_RESOURCES_SERVICE: resources}, arguments={})
        first_function(context=context)
        assert replace_twon_host(context=context) == '127.0.0.1'
        assert replace_twon_port(context=context) == 1234
        assert replace_twon_tlsa(context=context) == ('fixture-tlsa',)
        construct.assert_called_once()
    server.close.assert_called_once()


# Snapshot registrations before reading source iterables.
registrations = {TEST_RESOURCES_SERVICE: first}


def changing_sources():
    registrations[TEST_RESOURCES_SERVICE] = second
    yield '<resources>', source


snapshot = compile_strs(changing_sources(), add_std=True, services=registrations)
assert paths(program_compiler.compile_nocatch(snapshot))['other'] == '/first'


# Native functions receive the same read-only registration mapping in namespaces.
observed = []


def inspect_service(*, context):
    assert context.services['example'] is first
    try:
        context.services['example'] = second
    except TypeError:
        pass
    else:
        raise AssertionError('Mutable services mapping')
    observed.append(context.services)
    return context.services['example'].get_knx_path()


with patch.dict(native_function_impls, {('Outer', 'Inner', 'Inspect'): inspect_service}):
    prog = compile_program('''namespace Outer.Inner {
  native function Inspect(): String;
  node type Probe {
    constant Selected: String = Inspect();
    node bus: KNX.Connection { UnixPath: Selected }
  }
}
node probe: Outer.Inner.Probe {}
''', services={'example': first})
    assert paths(prog) == {'probe.bus': '/first'}
assert observed and all(mapping is observed[0] for mapping in observed)


unused = Resources('/unused')
compile_program('''node type Unused {
  constant Selected: String = Test.ReplaceKNXPath("/unused");
  constant Broker: String = Test.ReplaceMQTTURI("mqtt://unused");
  constant Output: String = Test.ReplaceOutputPath("unused.csv");
  constant Host: String = Test.ReplaceTwoNHost("unused");
  constant Port: uint16 = Test.ReplaceTwoNPort(443);
  constant TLSA: List[String] = Test.ReplaceTwoNTLSA([]);
}
node bus: KNX.Connection {
  UnixPath: if true { "/fallback" } else { Test.ReplaceKNXPath("/unused") },
}
''', services={TEST_RESOURCES_SERVICE: unused})
assert unused.calls == unused.mqtt_calls == 0
assert unused.output_calls == []
assert unused.twon_calls == 0


class FailingResources:
    def get_knx_path(self):
        raise OSError('Fixture allocation failed')

    def get_mqtt_uri(self):
        raise OSError('Broker allocation failed')

    def get_output_path(self, default):
        raise OSError('Output allocation failed')

    def get_twon_info(self):
        raise OSError('TwoN allocation failed')


failure_graph = graph('node bus: KNX.Connection { UnixPath: Test.ReplaceKNXPath("/default") }',
                      services={TEST_RESOURCES_SERVICE: FailingResources()})
try:
    program_compiler.compile_nocatch(failure_graph)
except blocks.ProgramError as error:
    assert str(error) == 'Fixture allocation failed'
    assert error.source_element.source_position.filename == '<resources>'
else:
    raise AssertionError('Expected a fixture diagnostic')


for function, message in (
    ('ReplaceMQTTURI', 'Broker allocation failed'),
    ('ReplaceOutputPath', 'Output allocation failed'),
):
    compiled = graph(f'node broker: MQTT.Connection {{ URI: Test.{function}("/default") }}',
                     services={TEST_RESOURCES_SERVICE: FailingResources()})
    try:
        program_compiler.compile_nocatch(compiled)
    except blocks.ProgramError as error:
        assert str(error) == message
        assert error.source_element.source_position.filename == '<resources>'
    else:
        raise AssertionError('Expected a fixture diagnostic')


for function, value_type, fallback in (
    ('ReplaceTwoNHost', 'String', '"fallback"'),
    ('ReplaceTwoNPort', 'uint16', '443'),
    ('ReplaceTwoNTLSA', 'List[String]', '[]'),
):
    compiled = graph(f'constant Selected: {value_type} = Test.{function}({fallback});',
                     services={TEST_RESOURCES_SERVICE: FailingResources()})
    try:
        program_compiler.compile_nocatch(compiled)
    except blocks.ProgramError as error:
        assert str(error) == 'TwoN allocation failed'
        assert error.source_element.source_position.filename == '<resources>'
    else:
        raise AssertionError('Expected a TwoN fixture diagnostic')


# The file API also passes services, without requiring a daemon for compilation.
with contextlib.ExitStack() as stack:
    resources = TestResourceProvider(stack)
    assert resources.knx_path is None
    socket_path = resources.get_knx_path()
    assert os.path.isabs(socket_path)
    assert socket_path == resources.get_knx_path()
    directory = os.path.dirname(socket_path)
    assert os.path.isdir(directory)
    compiled = compile_files(['test/wrl/knx_time.wrl'],
                             services={TEST_RESOURCES_SERVICE: resources})
    assert paths(program_compiler.compile_nocatch(compiled))['bus'] == socket_path
assert not os.path.exists(directory)


# Allocate output and MQTT resources lazily, sharing only within a provider.
with patch.dict(os.environ, {'WIRELY_TEST_KEEP_TESTDIR': '0'}), \
        contextlib.ExitStack() as stack:
    resources = TestResourceProvider(stack)
    assert resources.output_directory is None
    assert resources.mqtt_socket_path is None
    output = resources.get_output_path('/some/path/report.csv')
    assert output == resources.get_output_path('report.csv')
    assert output == resources.get_output_path('elsewhere/report.csv')
    other_output = resources.get_output_path('other.csv')
    assert other_output != output
    output_directory = resources.output_directory
    assert os.path.dirname(other_output) == output_directory
    assert os.path.isdir(output_directory)
    assert not os.path.exists(output)
    uri = resources.get_mqtt_uri()
    assert uri == resources.get_mqtt_uri()
    assert uri == 'mqtt+unix://' + resources.mqtt_socket_path
    mqtt_directory = os.path.dirname(resources.mqtt_socket_path)
    assert os.path.isdir(mqtt_directory)
    assert not os.path.exists(resources.mqtt_socket_path)
    other = TestResourceProvider(stack)
    assert other.get_mqtt_uri() != uri
    assert other.get_output_path('report.csv') != output
    other_output_directory = other.output_directory
    other_mqtt_directory = os.path.dirname(other.mqtt_socket_path)
assert all(not os.path.exists(path) for path in (
    output_directory, mqtt_directory, other_output_directory, other_mqtt_directory,
))


with patch.dict(os.environ, {'WIRELY_TEST_KEEP_TESTDIR': '1'}):
    try:
        with contextlib.ExitStack() as stack:
            resources = TestResourceProvider(stack)
            resources.get_output_path('kept.csv')
            kept_directory = resources.output_directory
        assert os.path.isdir(kept_directory)
    finally:
        shutil.rmtree(kept_directory)


async def check_daemon_lifecycle():
    for helper, daemon, get_path in (
        (with_maybe_knxd, 'knxd', lambda resources: resources.get_knx_path()),
        (with_maybe_mqtt, 'mosquitto',
         lambda resources: resources.get_mqtt_uri().removeprefix('mqtt+unix://')),
    ):
        with contextlib.ExitStack() as stack:
            resources = TestResourceProvider(stack)
            socket_path = get_path(resources)
            directory = os.path.dirname(socket_path)
            process = MagicMock()
            with patch('wirely.test.subprocess.Popen', return_value=process) as start:
                async with helper(None):
                    pass
                start.assert_not_called()

            # Successful startup and an assertion failure both reap the daemon.
            with patch('wirely.test.subprocess.Popen', return_value=process), \
                    patch('wirely.test.socket.socket') as socket:
                try:
                    async with helper(socket_path):
                        assert os.path.isdir(directory)
                        socket.return_value.__enter__.return_value.connect.assert_called_with(socket_path)
                        raise AssertionError('Test failed')
                except AssertionError as error:
                    assert str(error) == 'Test failed'
            process.kill.assert_called_once()
            process.wait.assert_called_once()

            # No ready socket: distinguish a daemon exit from a readiness timeout.
            for exit_code, exception, message in (
                (3, Exception, f'{daemon} has exited with exit code 3'),
                (None, TimeoutError, f'Timed out waiting for {daemon} to start'),
            ):
                process = MagicMock()
                process.poll.return_value = exit_code
                with patch('wirely.test.subprocess.Popen', return_value=process), \
                        patch('wirely.test.socket.socket') as socket, \
                        patch('wirely.test.asyncio.sleep'):
                    socket.return_value.__enter__.return_value.connect.side_effect = FileNotFoundError
                    try:
                        async with helper(socket_path):
                            raise AssertionError('Unready daemon accepted')
                    except exception as error:
                        assert str(error) == message
                process.kill.assert_called_once()
                process.wait.assert_called_once()

            with patch('wirely.test.subprocess.Popen', side_effect=FileNotFoundError(f'Missing {daemon}')):
                try:
                    async with helper(socket_path):
                        raise AssertionError('Missing executable accepted')
                except FileNotFoundError:
                    pass

            if daemon == 'mosquitto':
                broker = MagicMock()
                monitor = MagicMock()
                with patch.dict(os.environ, {'WIRELY_TEST_RUN_MQTT_SUB': '1'}), \
                        patch('wirely.test.subprocess.Popen', side_effect=(broker, monitor)), \
                        patch('wirely.test.socket.socket'), \
                        patch('wirely.test.asyncio.sleep'):
                    try:
                        async with helper(socket_path):
                            raise AssertionError('Monitored test failed')
                    except AssertionError as error:
                        assert str(error) == 'Monitored test failed'
                for process in (broker, monitor):
                    process.kill.assert_called_once()
                    process.wait.assert_called_once()
        assert not os.path.exists(directory)


async def check_runner_cleanup():
    allocated = []

    def failed_compilation(filenames, *, services):
        resources = services[TEST_RESOURCES_SERVICE]
        allocated.extend((resources.get_knx_path(), resources.get_output_path('failed.csv'),
                          resources.get_mqtt_uri().removeprefix('mqtt+unix://')))
        return None

    with patch('wirely.test.blocks.compiler.compile_files', side_effect=failed_compilation), \
            patch('wirely.test.subprocess.Popen') as start:
        try:
            await runner_run_test('unit/test_resources')
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError('Compilation failure accepted')
        start.assert_not_called()
    assert all(not os.path.exists(os.path.dirname(path)) for path in allocated)

    def fixture_compilation(filenames, *, services):
        resources = services[TEST_RESOURCES_SERVICE]
        allocated.extend((resources.get_knx_path(), resources.get_output_path('failed.csv'),
                          resources.get_mqtt_uri().removeprefix('mqtt+unix://')))
        return graph(source + other_source.replace('Wrapper', 'OutputWrapper').replace('wrapped', 'output_wrapped'),
                     services=services)

    with patch('wirely.test.blocks.compiler.compile_files', side_effect=fixture_compilation), \
            patch('wirely.test.program_compiler.compile', return_value=None), \
            patch('wirely.test.subprocess.Popen') as start:
        try:
            await runner_run_test('unit/test_resources')
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError('Program generation failure accepted')
        start.assert_not_called()
    assert all(not os.path.exists(os.path.dirname(path)) for path in allocated)

    async def fail_assertions():
        assert all(os.path.isdir(os.path.dirname(path)) for path in allocated[-3:])
        raise AssertionError('Assertions failed')

    def load_assertions(module):
        module.run_test = fail_assertions

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=load_assertions))
    process = MagicMock()
    with patch('wirely.test.blocks.compiler.compile_files', side_effect=fixture_compilation), \
            patch('wirely.test.subprocess.Popen', return_value=process), \
            patch('wirely.test.socket.socket'), \
            patch('wirely.test.blocks.Engine'), \
            patch('wirely.test.importlib.util.spec_from_file_location', return_value=spec), \
            patch('wirely.test.importlib.util.module_from_spec', return_value=SimpleNamespace()):
        try:
            await runner_run_test('unit/test_resources')
        except AssertionError as error:
            assert str(error) == 'Assertions failed'
        else:
            raise AssertionError('Assertion failure swallowed')
    assert process.kill.call_count == process.wait.call_count == 2
    assert all(not os.path.exists(os.path.dirname(path)) for path in allocated)


async def run_test():
    with contextlib.redirect_stdout(io.StringIO()), \
            patch.dict(os.environ, {'WIRELY_TEST_KEEP_TESTDIR': '0', 'WIRELY_TEST_RUN_MQTT_SUB': '0'}):
        await check_daemon_lifecycle()
        await check_runner_cleanup()
