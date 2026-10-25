import asyncio
import contextlib

import wirely.native.mqtt
from tests.infra.support import send_event_text

# mosquitto_sub -v -t '#'


async def run_test():
    # TODO: Add timeouts?
    # TODO: Move more of this functionality to test.py?
    # TODO: Make sure that exceptions in KNX callbacks cause the test to fail?
    # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        aiomqtt = wirely.native.mqtt.get_aiomqtt()

        print('a', flush=True)
        client = engine.get_native_state('server').create_client()
        async with client:
            await client.subscribe('#')
            print('b', flush=True)

            async def expect_msgs(*expected):
                pos = 0
                try:
                    # TODO: Increase timeout when waiting for a message?
                    async with asyncio.timeout(0.02):
                        async for message in client.messages:
                            if pos >= len(expected):
                                raise Exception(f'Got unexpected message: {message.topic!r} / {message.payload!r}')
                            exp_topic, exp_payload = expected[pos]
                            if exp_topic != message.topic.value:
                                raise Exception(f'Got unexpected message: {message.topic!r} / {message.payload!r}, expected topic {exp_topic!r}')
                            if exp_payload != message.payload:
                                raise Exception(f'Got unexpected message: {message.topic!r} / {message.payload!r}, expected payload {exp_payload!r}')
                            pos += 1
                    raise Exception('Should not be reached')
                except asyncio.TimeoutError:
                    pass
                if pos < len(expected):
                    raise Exception(f'Missing expected messages: {expected[pos:]!r}')

            await expect_msgs()

            # Test ValueProvider

            engine.get_native_state('provider_input').set_value(False)
            engine.get_native_state('template_provider_input').set_value(False)
            await expect_msgs()

            engine.step()
            # TODO: This should not depend on ordering
            await expect_msgs(('test/t2', b''), ('test/t1', b'false'), ('test/t1t', b'{"value": false, "other": "%s"}'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('provider_input').set_value(False)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('provider_input').set_value(True)
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t1', b'true'))

            engine.step()
            await expect_msgs()

            # Test TemplateValueProvider

            engine.get_native_state('template_provider_input').set_value(False)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('template_provider_input').set_value(False)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('template_provider_input').set_value(True)
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t1t', b'{"value": true, "other": "%s"}'))

            engine.step()
            await expect_msgs()

            # Test ValueOptionProvider

            engine.get_native_state('option_provider_input').set_value(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('option_provider_input').set_value(wirely.Some(True))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t2', b'true'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('option_provider_input').set_value(wirely.Some(True))
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('option_provider_input').set_value(wirely.Some(False))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t2', b'false'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('option_provider_input').set_value(None)
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t2', b''))

            engine.step()
            await expect_msgs()

            # Test EventSender

            engine.get_native_state('event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('event_source').send_event(wirely.Some(True))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t3', b'true'))

            engine.get_native_state('event_source').send_event(wirely.Some(True))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t3', b'true'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('event_source').send_event(wirely.Some(False))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t3', b'false'))

            engine.get_native_state('event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test NumberEventSender

            engine.get_native_state('number_event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('number_event_source').send_event(wirely.Some(17))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t4', b'17'))

            engine.get_native_state('number_event_source').send_event(wirely.Some(17))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t4', b'17'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('number_event_source').send_event(wirely.Some(23))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t4', b'23'))

            engine.get_native_state('number_event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test EnumEventSender

            engine.get_native_state('enum_event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            send_event_text(engine.get_native_state('enum_event_source'), 'Off')
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'"off"'))

            send_event_text(engine.get_native_state('enum_event_source'), 'Off')
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'"off"'))

            engine.step()
            await expect_msgs()

            send_event_text(engine.get_native_state('enum_event_source'), 'EmergencyHeat')
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'"emergency_heat"'))

            engine.get_native_state('enum_event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test TemplateEventSender

            engine.get_native_state('template_event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            send_event_text(engine.get_native_state('template_event_source'), 'Off')
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'{"value": "off", "other": "%s"}'))

            send_event_text(engine.get_native_state('template_event_source'), 'Off')
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'{"value": "off", "other": "%s"}'))

            engine.step()
            await expect_msgs()

            send_event_text(engine.get_native_state('template_event_source'), 'EmergencyHeat')
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'{"value": "emergency_heat", "other": "%s"}'))

            engine.get_native_state('template_event_source').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test EventReceiver

            engine.step()
            engine.assert_value_is('event_receiver', None)
            await expect_msgs()

            await client.publish('test/t6', payload=b'true')
            await expect_msgs(('test/t6', b'true'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('event_receiver', wirely.Some(True))
            await expect_msgs()

            engine.step()
            engine.assert_value_is('event_receiver', None)
            await expect_msgs()

            # Note: This should be ignored
            await client.publish('test/t6', payload=b'3')
            await expect_msgs(('test/t6', b'3'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('event_receiver', None)
            await expect_msgs()

            # Test TemplateEventReceiver

            engine.step()
            engine.assert_value_is('template_event_receiver', None)
            await expect_msgs()

            await client.publish('test/t7', payload=b'true')
            await expect_msgs(('test/t7', b'true'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('template_event_receiver', wirely.Some(True))
            await expect_msgs()

            engine.step()
            engine.assert_value_is('template_event_receiver', None)
            await expect_msgs()

            # Note: This should be ignored
            await client.publish('test/t7', payload=b'3')
            await expect_msgs(('test/t7', b'3'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('template_event_receiver', None)
            await expect_msgs()

            print('c', flush=True)
        print('d', flush=True)
