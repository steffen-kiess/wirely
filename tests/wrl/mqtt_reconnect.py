import asyncio
import contextlib
import json
import os
from unittest import mock

import aiomqtt

import wirely
import wirely.native.mqtt
from tests.infra.runner.services import MQTT_ADMIN_PASSWORD, MQTT_ADMIN_USERNAME, MQTT_DENIED_TOPIC
from tests.infra.support import Proxy


async def wait_until(condition):
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.001)


async def collect(client):
    received = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(0.05):
            async for message in client.messages:
                received.append(message)
    return received


def assert_subscribed(receiver, denied):
    engine.assert_value_is('receiver.Subscribed', receiver)
    engine.assert_value_is('value_receiver.Subscribed', receiver)
    engine.assert_value_is('denied.Subscribed', denied)


async def allow_denied_topic(path):
    """Allow anonymous clients to subscribe to MQTT_DENIED_TOPIC."""
    async with aiomqtt.Client(transport='unix', hostname=path, port=1, username=MQTT_ADMIN_USERNAME,
                              password=MQTT_ADMIN_PASSWORD) as admin:
        await admin.subscribe('$CONTROL/dynamic-security/v1/response')
        await admin.publish('$CONTROL/dynamic-security/v1', json.dumps({'commands': [{
            'command': 'removeRoleACL', 'rolename': 'anonymous', 'acltype': 'subscribePattern',
            'topic': MQTT_DENIED_TOPIC}]}))
        async with asyncio.timeout(5):
            async for message in admin.messages:
                response = json.loads(message.payload)
                if 'error' in response['responses'][0]:
                    raise Exception(f'Changing the access rules failed: {response}')
                break


def set_value(name, value):
    engine.get_native_state(f'{name}_input.create').set_value(value)
    engine.step()


async def run_test():
    server = engine.get_native_state('server.create')
    # Move the broker's socket, so that the program can only reach it through the proxy.
    path = server.parsed_uri.hostname
    broker_path = path + '.broker'
    os.rename(path, broker_path)
    proxy = Proxy(path, broker_path)

    # The observer logs in, so that it is not disconnected when the access rules of anonymous clients change.
    observer = aiomqtt.Client(transport='unix', hostname=broker_path, port=1, username=MQTT_ADMIN_USERNAME,
                              password=MQTT_ADMIN_PASSWORD)

    async def expect_msgs(*expected):
        received = [(message.topic.value, message.payload) for message in await collect(observer)]
        if received != list(expected):
            raise Exception(f'Expected messages {list(expected)!r}, got {received!r}')

    async def expect_retained(expected):
        async with aiomqtt.Client(transport='unix', hostname=broker_path, port=1) as client:
            await client.subscribe('test/#')
            retained = {message.topic.value: message.payload for message in await collect(client) if message.retain}
        if retained != expected:
            raise Exception(f'Expected retained messages {expected!r}, got {retained!r}')

    with (mock.patch.object(wirely.native.mqtt, 'RECONNECT_DELAY_MIN', 0.01),
          mock.patch.object(wirely.native.mqtt, 'RECONNECT_DELAY_MAX', 0.01),
          mock.patch.object(wirely.native.mqtt, 'SUBSCRIBE_RETRY_INTERVAL', 0.01)):
        async with observer:
            await observer.subscribe('test/#')
            # Close the proxy only after the engine's tasks have finished, so that they can disconnect cleanly.
            with contextlib.closing(proxy):
                async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
                    engine.activate(tg, async_exit_stack)

                    # The broker cannot be reached: values stay pending and events are dropped.
                    engine.get_native_state('event_source.create').send_event(wirely.Some(True))
                    engine.step()
                    engine.assert_value_is('server.Connected', False)
                    assert_subscribed(False, False)
                    engine.assert_value_is('value_receiver', None)
                    await expect_msgs()

                    # After connecting, the current values are published, the will provider's value last. A retained
                    # None removes the retained value.
                    await proxy.start()
                    await wait_until(lambda: server.connected)
                    engine.step()
                    engine.assert_value_is('server.Connected', True)
                    # The broker rejects subscribing to the denied topic.
                    assert_subscribed(True, False)
                    await expect_msgs(('test/a', b''), ('test/b', b'false'), ('test/status', b'true'))
                    await expect_retained({'test/status': b'true'})

                    set_value('a', wirely.Some(True))
                    await expect_msgs(('test/a', b'true'))
                    await expect_retained({'test/a': b'true', 'test/status': b'true'})

                    # Without awaiting, the values and events of several steps queue up. They are sent in order, and a
                    # value set while another value of its provider is queued is sent after that one.
                    set_value('a', wirely.Some(False))
                    engine.get_native_state('event_source.create').send_event(wirely.Some(True))
                    engine.step()
                    set_value('b', True)
                    set_value('a', wirely.Some(True))
                    await expect_msgs(('test/a', b'false'), ('test/event', b'true'), ('test/b', b'true'),
                                      ('test/a', b'true'))

                    # Values in between are skipped.
                    set_value('a', None)
                    set_value('b', False)
                    set_value('a', wirely.Some(False))
                    set_value('b', True)
                    set_value('a', wirely.Some(True))
                    await expect_msgs(('test/a', b''), ('test/b', b'false'), ('test/a', b'true'), ('test/b', b'true'))

                    # The will provider's value stays after the other values.
                    set_value('status', wirely.Some(False))
                    set_value('a', wirely.Some(False))
                    await expect_msgs(('test/a', b'false'), ('test/status', b'false'))
                    set_value('status', wirely.Some(True))
                    await expect_msgs(('test/status', b'true'))

                    # A live message arrives as an event and as a value, which stays.
                    await observer.publish('test/received', payload=b'true', retain=True)
                    await expect_msgs(('test/received', b'true'))
                    engine.step()
                    engine.assert_value_is('receiver', wirely.Some(True))
                    engine.assert_value_is('value_receiver', wirely.Some(True))
                    engine.step()
                    engine.assert_value_is('receiver', None)
                    engine.assert_value_is('value_receiver', wirely.Some(True))

                    # After a reconnect, Connected is false for one step and the values are published again. The broker
                    # publishes the will first.
                    proxy.cut()
                    await wait_until(lambda: server.disconnect_pending and server.connected)
                    await expect_msgs(('test/status', b''), ('test/a', b'false'), ('test/b', b'true'),
                                      ('test/status', b'true'))
                    engine.step()
                    engine.assert_value_is('server.Connected', False)
                    assert_subscribed(False, False)
                    engine.assert_value_is('value_receiver', None)
                    engine.step()
                    engine.assert_value_is('server.Connected', True)
                    assert_subscribed(True, False)

                    # The retained message that the broker sends when subscribing again is not an event, but it is
                    # the value again. The subscription is restored.
                    engine.assert_value_is('receiver', None)
                    engine.assert_value_is('value_receiver', wirely.Some(True))
                    await observer.publish('test/received', payload=b'false')
                    await expect_msgs(('test/received', b'false'))
                    engine.step()
                    engine.assert_value_is('receiver', wirely.Some(False))
                    engine.assert_value_is('value_receiver', wirely.Some(False))

                    # An empty payload and a message that cannot be decoded mean that there is no value.
                    for payload in (b'', b'3', b'true'):
                        await observer.publish('test/received', payload=payload)
                        await expect_msgs(('test/received', payload))
                        engine.step()
                        engine.assert_value_is('value_receiver', wirely.Some(True) if payload == b'true' else None)

                    # When the access rules change, the plugin disconnects the affected clients. After reconnecting,
                    # subscribing to the topic succeeds and its messages arrive.
                    number = server.connection_number
                    await allow_denied_topic(broker_path)
                    await wait_until(lambda: server.connection_number > number)
                    engine.step()
                    engine.assert_value_is('server.Connected', False)
                    assert_subscribed(False, False)
                    engine.step()
                    engine.assert_value_is('server.Connected', True)
                    assert_subscribed(True, True)
                    # The plugin disconnects without the will.
                    await expect_msgs(('test/a', b'false'), ('test/b', b'true'), ('test/status', b'true'))
                    await observer.publish(MQTT_DENIED_TOPIC, payload=b'true')
                    await expect_msgs((MQTT_DENIED_TOPIC, b'true'))
                    engine.step()
                    engine.assert_value_is('denied', wirely.Some(True))

            # Stopping publishes the will.
            await expect_msgs(('test/status', b''))
