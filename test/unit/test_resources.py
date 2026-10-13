import asyncio
import contextlib
import io
import os
from pathlib import Path
import shutil
import socket
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from wirely.compiler.function_implementations.test import (
    TEST_RESOURCES_SERVICE, replace_twon_host, replace_twon_port, replace_twon_tlsa,
)
from wirely.test import (
    TestResourceProvider, run_runtime_test as runner_run_test,
    with_maybe_knxd, with_maybe_mqtt,
)
from wirely.two_n.fake_server import Server
from wirely.runtime.engine import Engine


checks = TestCase()


class LifecycleFailure(Exception):
    pass


def assert_closed(server):
    assert server.socket.fileno() == -1
    server.close()


def check_provider_resources():
    with contextlib.ExitStack() as stack:
        first = TestResourceProvider(stack)
        second = TestResourceProvider(stack)
        paths = [first.get_knx_path(), first.get_output_path('/some/path/report.csv'),
                 first.get_mqtt_uri().removeprefix('mqtt+unix://')]
        assert first.get_knx_path() == paths[0]
        assert first.get_output_path('elsewhere/report.csv') == paths[1]
        assert first.get_output_path('other.csv') != paths[1]
        assert first.get_mqtt_uri() == 'mqtt+unix://' + paths[2]
        other_paths = [second.get_knx_path(), second.get_output_path('report.csv'),
                       second.get_mqtt_uri().removeprefix('mqtt+unix://')]
        assert all(left != right for left, right in zip(paths, other_paths))
        assert all(Path(path).parent.is_dir() for path in paths + other_paths)
        info = first.get_twon_info()
        assert first.get_twon_info() is info
        other_info = second.get_twon_info()
        assert info.port != other_info.port
        context = SimpleNamespace(services={TEST_RESOURCES_SERVICE: first}, arguments={})
        assert replace_twon_host(context=context) == info.host
        assert replace_twon_port(context=context) == info.port
        assert replace_twon_tlsa(context=context) == (info.server.tlsa,)
    assert all(not Path(path).parent.exists() for path in paths + other_paths)
    assert_closed(info.server)
    assert_closed(other_info.server)

    with patch.dict(os.environ, {'WIRELY_TEST_KEEP_TESTDIR': '1'}):
        try:
            with contextlib.ExitStack() as stack:
                path = TestResourceProvider(stack).get_output_path('kept.csv')
            assert Path(path).parent.is_dir()
        finally:
            shutil.rmtree(Path(path).parent)


async def check_daemon_lifecycle():
    for helper, name, get_path in (
        (with_maybe_knxd, 'knxd', lambda resources: resources.get_knx_path()),
        (with_maybe_mqtt, 'mosquitto',
         lambda resources: resources.get_mqtt_uri().removeprefix('mqtt+unix://')),
    ):
        with contextlib.ExitStack() as stack:
            path = get_path(TestResourceProvider(stack))
            with patch('wirely.test.subprocess.Popen') as start:
                async with helper(None):
                    pass
                start.assert_not_called()
            process = MagicMock()
            with patch('wirely.test.subprocess.Popen', return_value=process), \
                    patch('wirely.test.socket.socket'):
                with checks.assertRaises(LifecycleFailure):
                    async with helper(path):
                        raise LifecycleFailure
            process.kill.assert_called_once()
            process.wait.assert_called_once()
            for exit_code, exception in [(3, Exception), (None, TimeoutError)]:
                process = MagicMock()
                process.poll.return_value = exit_code
                with patch('wirely.test.subprocess.Popen', return_value=process), \
                        patch('wirely.test.socket.socket') as connection, \
                        patch('wirely.test.asyncio.sleep'):
                    connection.return_value.__enter__.return_value.connect.side_effect = FileNotFoundError
                    with checks.assertRaisesRegex(exception, name):
                        async with helper(path):
                            raise AssertionError('Unready daemon accepted')
                process.kill.assert_called_once()
                process.wait.assert_called_once()
            with patch('wirely.test.subprocess.Popen', side_effect=FileNotFoundError):
                with checks.assertRaises(FileNotFoundError):
                    async with helper(path):
                        raise AssertionError('Missing executable accepted')
            if name == 'mosquitto':
                broker, monitor = MagicMock(), MagicMock()
                with patch.dict(os.environ, {'WIRELY_TEST_RUN_MQTT_SUB': '1'}), \
                        patch('wirely.test.subprocess.Popen', side_effect=(broker, monitor)), \
                        patch('wirely.test.socket.socket'):
                    async with helper(path):
                        pass
                for process in (broker, monitor):
                    process.kill.assert_called_once()
                    process.wait.assert_called_once()
        assert not Path(path).parent.exists()


async def check_server_lifecycle():
    server = Server()
    info = server.info
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as competitor:
            with checks.assertRaises(OSError):
                competitor.bind(info.sockname)
        async with server.run():
            async with aiohttp.ClientSession() as client:
                async with client.get(f'https://{info.host}:{info.port}/', ssl=False) as response:
                    assert response.status == 200
                    assert await response.text() == 'Hello, world'
    finally:
        server.close()
    assert_closed(server)

    # A reserved socket is released even if serving never begins.
    server = Server()
    server.close()
    assert_closed(server)
    for failure in (LifecycleFailure, asyncio.CancelledError):
        server = Server()
        with patch('aiohttp.web.SockSite.start', new=AsyncMock(side_effect=failure)):
            with checks.assertRaises(failure):
                async with server.run():
                    raise AssertionError('Startup failure accepted')
        assert_closed(server)

    server = Server()
    started = asyncio.Event()

    async def serving():
        async with server.run():
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(serving())
    await started.wait()
    task.cancel()
    with checks.assertRaises(asyncio.CancelledError):
        await task
    assert_closed(server)


async def check_runner_cleanup():
    for mode in ('compilation', 'program', 'assertions', 'startup'):
        paths = []
        servers = []
        started = []
        stopped = []

        def compile_fixture(filenames, *, services, on_diagnostic):
            resources = services[TEST_RESOURCES_SERVICE]
            paths.extend((resources.get_knx_path(), resources.get_output_path('failed.csv'),
                          resources.get_mqtt_uri().removeprefix('mqtt+unix://')))
            servers.append(resources.get_twon_info().server)
            return None if mode == 'compilation' else object()

        @contextlib.asynccontextmanager
        async def daemon(path):
            started.append(path)
            try:
                yield
            finally:
                stopped.append(path)

        async def fail_assertions():
            assert all(Path(path).parent.is_dir() for path in paths)
            assert servers[0].socket.fileno() >= 0
            raise LifecycleFailure

        spec = SimpleNamespace(loader=SimpleNamespace(
            exec_module=lambda module: setattr(module, 'run_test', fail_assertions),
        ))
        with contextlib.ExitStack() as mocks:
            mocks.enter_context(patch('wirely.test.model.compiler.compile_files', side_effect=compile_fixture))
            mocks.enter_context(patch('wirely.test.program_compiler.compile',
                                      return_value=None if mode == 'program' else MagicMock()))
            construct = mocks.enter_context(patch('wirely.test.Engine', return_value=SimpleNamespace()))
            mocks.enter_context(patch('wirely.test.with_maybe_knxd', daemon))
            mocks.enter_context(patch('wirely.test.with_maybe_mqtt', daemon))
            mocks.enter_context(patch('wirely.test.importlib.util.spec_from_file_location', return_value=spec))
            mocks.enter_context(patch('wirely.test.importlib.util.module_from_spec', return_value=SimpleNamespace()))
            if mode == 'startup':
                mocks.enter_context(patch('aiohttp.web.SockSite.start', new=AsyncMock(side_effect=LifecycleFailure)))
            expected = SystemExit if mode in ('compilation', 'program') else LifecycleFailure
            with checks.assertRaises(expected):
                await runner_run_test('wrl/std')
            if mode != 'assertions':
                construct.assert_not_called()
        assert sorted(started) == sorted(stopped)
        if mode in ('compilation', 'program'):
            assert not started
        else:
            assert len(started) == 2
        assert all(not Path(path).parent.exists() for path in paths)
        assert_closed(servers[0])


async def run_test():
    with contextlib.redirect_stdout(io.StringIO()), \
            patch.dict(os.environ, {'WIRELY_TEST_KEEP_TESTDIR': '0', 'WIRELY_TEST_RUN_MQTT_SUB': '0'}):
        check_provider_resources()
        await check_daemon_lifecycle()
        await check_server_lifecycle()
        await check_runner_cleanup()
