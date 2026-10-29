import asyncio
import contextlib
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


async def run_test():
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

            # After connecting, the states are read.
            server.available = True
            await wait_until(lambda: device.connected)
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
