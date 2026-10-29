import asyncio
import contextlib
import os
from unittest import mock

import xknx.tools
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

import wirely
import wirely.native.knx
from tests.infra.support import Proxy
from wirely.knx.connection_adapter import create_xknx
from wirely.knx.connection_config import create_connection_config


async def wait_until(condition):
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


async def run_test():
    bus = engine.get_native_state('bus.create')
    # Move knxd's socket, so that the program can only reach it through the proxy.
    path = bus.settings.unix_path
    knxd_path = path + '.knxd'
    os.rename(path, knxd_path)
    proxy = Proxy(path, knxd_path)

    # The observer records the telegrams of the program and answers reads of the receiver's address.
    observer = create_xknx(create_connection_config(unix_path=knxd_path))
    telegrams = []
    answer = [True]

    def observe(telegram):
        payload = telegram.payload
        if isinstance(payload, GroupValueRead):
            telegrams.append((str(telegram.destination_address), 'read', None))
            if str(telegram.destination_address) == '0/0/2':
                xknx.tools.group_value_response(observer, '0/0/2', answer[0])
        elif isinstance(payload, GroupValueWrite | GroupValueResponse):
            telegrams.append((str(telegram.destination_address), type(payload).__name__, payload.value.value == 1))

    observer.telegram_queue.register_telegram_received_cb(observe)

    async def expect_telegrams(*expected):
        await wait_until(lambda: len(telegrams) >= len(expected))
        await asyncio.sleep(0.05)
        if telegrams != list(expected):
            raise Exception(f'Expected telegrams {list(expected)!r}, got {telegrams!r}')
        telegrams.clear()

    receiver = engine.get_native_state('receiver.create')

    with (mock.patch.object(wirely.native.knx, 'RECONNECT_DELAY_MIN', 0.01),
          mock.patch.object(wirely.native.knx, 'RECONNECT_DELAY_MAX', 0.01)):
        async with observer:
            with contextlib.closing(proxy):
                async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
                    # Activation does not wait for the interface; telegrams are dropped while it cannot be reached.
                    engine.activate(tg, async_exit_stack)
                    engine.step()
                    engine.assert_value_is('bus.Connected', False)
                    engine.get_native_state('event_input.create').send_event(wirely.Some(True))
                    engine.step()
                    await asyncio.sleep(0.1)
                    assert telegrams == [], telegrams

                    # After connecting, the provider sends its value and the receiver reads its address.
                    await proxy.start()
                    await wait_until(lambda: bus.connected)
                    engine.step()
                    engine.assert_value_is('bus.Connected', True)
                    await expect_telegrams(('0/0/1', 'GroupValueWrite', True), ('0/0/2', 'read', None))
                    await wait_until(lambda: receiver.current_value is not None)
                    engine.step()
                    engine.assert_value_is('receiver', wirely.Some(True))

                    # After a reconnect, Connected is false and the receiver is None for one step, the value is sent
                    # again, and the receiver reads again.
                    answer[0] = False
                    proxy.cut()
                    await wait_until(lambda: bus.disconnect_pending and bus.connected)
                    await expect_telegrams(('0/0/1', 'GroupValueWrite', True), ('0/0/2', 'read', None))
                    engine.step()
                    engine.assert_value_is('bus.Connected', False)
                    engine.assert_value_is('receiver', None)
                    engine.step()
                    engine.assert_value_is('bus.Connected', True)
                    await wait_until(lambda: receiver.current_value is not None)
                    engine.step()
                    engine.assert_value_is('receiver', wirely.Some(False))

                    # Stopping while connecting closes the connection.
                    proxy.forward = False
                    proxy.cut()
                    await wait_until(lambda: proxy.clients == 1 and not bus.connected)
                await wait_until(lambda: proxy.clients == 0)
