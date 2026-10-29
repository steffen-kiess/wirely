import asyncio
import contextlib
import io
import socket
from unittest import mock

import wirely
import wirely.native.two_n


async def wait_until(condition):
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


def assert_states(connected, input1, switch1, key):
    engine.assert_value_is('device.Connected', connected)
    engine.assert_value_is('input1', input1)
    engine.assert_value_is('switch1', switch1)
    engine.assert_value_is('key', key)
    # The device has no such port.
    engine.assert_value_is('missing', None)


async def run_steps():
    server = engine.test_twon_fake_server_info.server
    device = engine.get_native_state('device.create')
    server.set_io_state('ext3.input1', True)
    server.available = False

    with (mock.patch.object(wirely.native.two_n, 'RECONNECT_DELAY_MIN', 0.01),
          mock.patch.object(wirely.native.two_n, 'RECONNECT_DELAY_MAX', 0.01)):
        async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
            # Activation does not wait for the device; values to send are dropped while it cannot be reached.
            await engine.activate(tg, async_exit_stack)
            engine.step()
            assert_states(False, None, None, None)
            engine.get_native_state('output_debug.create').send_event(wirely.Some(True))
            engine.step()
            engine.step()
            await asyncio.sleep(0.1)
            assert server.ports['led_secured']['state'] == 0

            # After connecting, the states are read, with one request for all ports.
            status_requests = server.requests['/api/io/status']
            server.available = True
            await wait_until(lambda: device.connected)
            assert server.requests['/api/io/status'] == status_requests + 1
            engine.step()
            assert_states(True, wirely.Some(True), wirely.Some(False), wirely.Some(False))

            # While the device restarts, the states are unknown; they are read again after reconnecting.
            server.available = False
            server.drop_subscriptions()
            await wait_until(lambda: not device.connected)
            server.set_io_state('ext3.input1', False)
            engine.step()
            assert_states(False, None, None, None)
            server.available = True
            await wait_until(lambda: device.connected)
            engine.step()
            assert_states(True, wirely.Some(False), wirely.Some(False), wirely.Some(False))

            # A reconnect between two steps is reported for one step.
            server.drop_subscriptions()
            await wait_until(lambda: device.disconnect_pending and device.connected)
            engine.step()
            assert_states(False, None, None, None)
            engine.step()
            assert_states(True, wirely.Some(False), wirely.Some(False), wirely.Some(False))
            engine.assert_is_idle()

            # An event that cannot be handled ends the connection; the states are read again after reconnecting.
            number = device.connection_number
            server.emit_event_raw({'event': 'KeyPressed'})
            await wait_until(lambda: device.connection_number == number + 1)
            engine.step()
            assert_states(False, None, None, None)
            engine.step()
            assert_states(True, wirely.Some(False), wirely.Some(False), wirely.Some(False))

            # A 401 is answered by the digest middleware; a 401 to its resend is retried. Neither loses the connection.
            number = device.connection_number
            server.unauthorized['/api/log/pull'] = 1
            # The event ends the pending pull, so that the next pull is refused.
            server.set_io_state('ext3.input1', True)
            await wait_until(lambda: server.unauthorized['/api/log/pull'] == 0)
            server.unauthorized['/api/log/pull'] = 2
            server.set_io_state('ext3.input1', False)
            await wait_until(lambda: server.unauthorized['/api/log/pull'] == 0)
            server.set_io_state('ext3.input1', True)
            server.set_switch_state(1, True)
            await wait_until(lambda: device.switch_states[0].current_value)
            engine.step()
            assert_states(True, wirely.Some(True), wirely.Some(True), wirely.Some(False))
            assert device.connection_number == number

            # A repeated 401 when reading a state ends the connection instead of only affecting the node.
            server.unauthorized['/api/io/status'] = 4
            server.drop_subscriptions()
            await wait_until(lambda: device.connection_number == number + 1)
            assert server.unauthorized['/api/io/status'] == 0
            engine.step()
            assert_states(False, None, None, None)
            engine.step()
            assert_states(True, wirely.Some(True), wirely.Some(True), wirely.Some(False))

            # A refused connection is retried. The socket is bound but does not listen, so it refuses connections.
            number = device.connection_number
            port = device.client.port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as refusing:
                refusing.bind(('127.0.0.1', 0))
                device.client.port = refusing.getsockname()[1]
                server.drop_subscriptions()
                await wait_until(lambda: 'Cannot connect to host' in (device.errors._error or ''))
                engine.step()
                assert_states(False, None, None, None)
                device.client.port = port
            await wait_until(lambda: device.connection_number == number + 1)
            engine.step()
            assert_states(True, wirely.Some(True), wirely.Some(True), wirely.Some(False))

            # Stopping while a state read hangs closes the session without waiting for the read.
            server.hanging = {'/api/io/status'}
            server.drop_subscriptions()
            await wait_until(lambda: len(server.hanging_requests) == 1 and not device.connected)
            stopping = asyncio.get_running_loop().time()
    # The subscription is ended in less than its timeout, since only the read hangs.
    assert asyncio.get_running_loop().time() - stopping < 0.5
    assert device.client.client_session is None
    assert device.task.done()


async def run_test():
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            async with asyncio.timeout(30):
                await run_steps()
    finally:
        engine.test_twon_fake_server_info.server.release_hanging()
        print(output.getvalue(), end='', flush=True)
    assert "Warning: Failed to handle TwoN event {'event': 'KeyPressed', '_type': 'new'}:" in output.getvalue()
    # Only 401s to the resend by the digest middleware are logged, not those it answers itself.
    retried = [line for line in output.getvalue().splitlines() if line.endswith('; retrying')]
    assert len(retried) == 2, retried
    assert 'During POST request to /api/log/pull got status code 401' in retried[0], retried
    assert 'During GET request to /api/io/status got status code 401' in retried[1], retried
