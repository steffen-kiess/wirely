import asyncio
import contextlib
import os
from unittest import mock

import aiomqtt

import wirely
import wirely.native.mqtt


class Proxy:
    """Forward connections at a Unix socket path to the broker, until they are cut."""

    def __init__(self, path, target):
        self.path = path
        self.target = target
        self.server = None
        self.writers = set()

    async def start(self):
        self.server = await asyncio.start_unix_server(self.handle, path=self.path)

    def cut(self):
        for writer in self.writers:
            writer.transport.abort()
        self.writers.clear()

    def close(self):
        self.cut()
        if self.server is not None:
            self.server.close()

    async def handle(self, client_reader, client_writer):
        broker_reader, broker_writer = await asyncio.open_unix_connection(self.target)
        self.writers.update((client_writer, broker_writer))

        async def forward(reader, writer):
            with contextlib.suppress(OSError):
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            writer.transport.abort()

        await asyncio.gather(forward(client_reader, broker_writer), forward(broker_reader, client_writer))


async def wait_until(condition):
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.001)


async def run_test():
    server = engine.get_native_state('server.create')
    # Move the broker's socket, so that the program can only reach it through the proxy.
    path = server.parsed_uri.hostname
    os.rename(path, path + '.broker')
    proxy = Proxy(path, path + '.broker')

    observer = aiomqtt.Client(transport='unix', hostname=path + '.broker', port=1)

    async def expect_msgs(*expected):
        received = []
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(0.05):
                async for message in observer.messages:
                    received.append((message.topic.value, message.payload))
        if received != list(expected):
            raise Exception(f'Expected messages {list(expected)!r}, got {received!r}')

    with (mock.patch.object(wirely.native.mqtt, 'RECONNECT_DELAY_MIN', 0.01),
          mock.patch.object(wirely.native.mqtt, 'RECONNECT_DELAY_MAX', 0.01)):
        async with (observer, asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack):
            async_exit_stack.callback(proxy.close)
            await observer.subscribe('test/#')
            await engine.activate(tg, async_exit_stack)

            # The broker cannot be reached: values stay pending and events are dropped.
            engine.get_native_state('provider_input.create').set_value(True)
            engine.get_native_state('event_source.create').send_event(wirely.Some(True))
            engine.step()
            engine.assert_value_is('server.Connected', False)
            await expect_msgs()

            # After connecting, the current value is published.
            await proxy.start()
            await wait_until(lambda: server.connected)
            engine.step()
            engine.assert_value_is('server.Connected', True)
            await expect_msgs(('test/value', b'true'))

            # After a reconnect, Connected is false for one step, the value is published again, and the
            # subscription is restored.
            proxy.cut()
            await wait_until(lambda: server.disconnect_pending and server.connected)
            await expect_msgs(('test/value', b'true'))
            engine.step()
            engine.assert_value_is('server.Connected', False)
            engine.step()
            engine.assert_value_is('server.Connected', True)
            await observer.publish('test/received', payload=b'false')
            await expect_msgs(('test/received', b'false'))
            engine.step()
            engine.assert_value_is('receiver', wirely.Some(False))
