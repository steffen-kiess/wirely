import contextlib
import io
import os
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from wirely.compiler import compile_strs
from wirely.compiler.function_implementations.test import (
    TEST_RESOURCES_SERVICE, replace_twon_host, replace_twon_port, replace_twon_tlsa,
)
from wirely.test import (
    TestResourceProvider, compile_test, run_test as runner_run_test,
    with_maybe_knxd, with_maybe_mqtt,
)


def graph(source, *, services=None):
    result = compile_strs([('<resources>', source)], add_std=True, services=services)
    assert result is not None
    return result

# Compiler fixtures run their assertions without initializing an engine.
with contextlib.redirect_stdout(io.StringIO()), patch('wirely.test.blocks.Engine') as construct:
    compile_test('test/compiler/literals.wrl')
    construct.assert_not_called()


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


# Provider-owned socket directories are allocated once and cleaned up.
with contextlib.ExitStack() as stack:
    resources = TestResourceProvider(stack)
    assert resources.knx_path is None
    socket_path = resources.get_knx_path()
    assert os.path.isabs(socket_path)
    assert socket_path == resources.get_knx_path()
    directory = os.path.dirname(socket_path)
    assert os.path.isdir(directory)
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
        return graph('''node bus: KNX.Connection {
  UnixPath: Test.ReplaceKNXPath("/default"),
}
node broker: MQTT.Connection { URI: Test.ReplaceMQTTURI("mqtt://default") }
node input: Std.Constant { Type: boolean, Value: false }
node writer: CSV.ValueWriter {
  Type: boolean, Filename: Test.ReplaceOutputPath("output.csv"),
}
connect input -> writer;
''', services=services)

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
