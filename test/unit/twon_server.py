import asyncio
import contextlib
import io
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from wirely.test import TestResourceProvider, run_test as runner_run_test
from wirely.compiler.function_implementations.test import TEST_RESOURCES_SERVICE
from wirely.two_n.fake_server import Server


class LifecycleFailure(Exception):
    pass


def assert_closed(server):
    assert server.socket.fileno() == -1
    server.close()
    server.close()


async def check_server():
    server = Server()
    info = server.info
    try:
        assert info.server is server
        assert info.host == '127.0.0.1'
        assert info.port > 0
        assert info.sockname == server.socket.getsockname()
        assert server.tlsa
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as competitor:
            try:
                competitor.bind(info.sockname)
            except OSError:
                pass
            else:
                raise AssertionError('Reserved port could be bound again')
        async with server.run() as running:
            assert running is info
            async with aiohttp.ClientSession() as client:
                async with client.get(f'https://{info.host}:{info.port}/', ssl=False) as response:
                    assert response.status == 200
                    assert await response.text() == 'Hello, world'
    finally:
        server.close()
    assert_closed(server)

    # The socket is also released if serving never starts.
    server = Server()
    server.close()
    assert_closed(server)

    # Failures at each startup stage must release the reserved socket.
    for target in ('aiohttp.web.AppRunner.setup', 'aiohttp.web.SockSite.start'):
        server = Server()
        with patch(target, new=AsyncMock(side_effect=LifecycleFailure)):
            try:
                async with server.run():
                    raise AssertionError('Startup failure accepted')
            except LifecycleFailure:
                pass
        assert_closed(server)

    server = Server()
    with patch('wirely.two_n.fake_server.tempfile.TemporaryDirectory', side_effect=LifecycleFailure):
        try:
            async with server.run():
                raise AssertionError('Preparation failure accepted')
        except LifecycleFailure:
            pass
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
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert_closed(server)

    server = Server()
    with patch('aiohttp.web.SockSite.start', new=AsyncMock(side_effect=asyncio.CancelledError)):
        try:
            async with server.run():
                raise AssertionError('Startup cancellation accepted')
        except asyncio.CancelledError:
            pass
    assert_closed(server)


async def check_runner():
    servers = []

    def failed_compilation(filenames, *, services):
        info = services[TEST_RESOURCES_SERVICE].get_twon_info()
        servers.append(info.server)
        return None

    with patch('wirely.test.blocks.compiler.compile_files', side_effect=failed_compilation), \
            patch('aiohttp.web.SockSite.start', new=AsyncMock()) as start:
        try:
            await runner_run_test('unit/twon_server')
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError('Compilation failure accepted')
        start.assert_not_called()
    assert_closed(servers[-1])

    def compiled_fixture(filenames, *, services):
        info = services[TEST_RESOURCES_SERVICE].get_twon_info()
        servers.append(info.server)
        return object()

    async def failed_assertions():
        assert servers[-1].socket.fileno() >= 0
        assert created_engine.test_twon_fake_server_info is servers[-1].info
        raise LifecycleFailure

    def load_assertions(module):
        module.run_test = failed_assertions

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=load_assertions))
    created_engine = SimpleNamespace()
    for mode in ('program_failure', 'assertion_failure', 'startup_failure'):
        with contextlib.ExitStack() as mocks:
            mocks.enter_context(patch('wirely.test.blocks.compiler.compile_files', side_effect=compiled_fixture))
            mocks.enter_context(patch('wirely.test.program_compiler.compile',
                                      return_value=None if mode == 'program_failure' else MagicMock()))
            make_engine = mocks.enter_context(patch('wirely.test.blocks.Engine', return_value=created_engine))
            mocks.enter_context(patch('wirely.test.importlib.util.spec_from_file_location', return_value=spec))
            mocks.enter_context(patch('wirely.test.importlib.util.module_from_spec', return_value=SimpleNamespace()))
            if mode == 'startup_failure':
                mocks.enter_context(patch('aiohttp.web.SockSite.start', new=AsyncMock(side_effect=LifecycleFailure)))
            try:
                await runner_run_test('unit/twon_server')
            except SystemExit as error:
                assert mode == 'program_failure' and error.code == 1
            except LifecycleFailure:
                assert mode != 'program_failure'
            else:
                raise AssertionError('Runner failure swallowed')
            if mode != 'assertion_failure':
                make_engine.assert_not_called()
        assert_closed(servers[-1])


async def run_test():
    # Construction failure closes a socket even before info is assigned.
    for method in ('bind', 'getsockname'):
        sock = MagicMock()
        getattr(sock, method).side_effect = LifecycleFailure
        with patch('wirely.two_n.fake_server.socket.socket', return_value=sock):
            try:
                Server()
            except LifecycleFailure:
                pass
            else:
                raise AssertionError('Construction failure accepted')
        sock.close.assert_called_once()

    with contextlib.ExitStack() as stack:
        resources = TestResourceProvider(stack)
        assert resources.twon_server is None
        info = resources.get_twon_info()
        assert resources.get_twon_info() is info
        other = TestResourceProvider(stack)
        assert other.get_twon_info().port != info.port
        servers = (info.server, other.twon_server)
    for server in servers:
        assert_closed(server)

    await check_server()
    with contextlib.redirect_stdout(io.StringIO()):
        await check_runner()
