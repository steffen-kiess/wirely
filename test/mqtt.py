import asyncio
import contextlib

import wirely.native.mqtt

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
        client = engine.get_native_state('Server').create_client()
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

            engine.get_native_state('ProviderInput').set_value(False)
            engine.get_native_state('TemplateProviderInput').set_value(False)
            await expect_msgs()

            engine.step()
            # TODO: This should not depend on ordering
            await expect_msgs(('test/t2', b''), ('test/t1', b'false'), ('test/t1t', b'{"value": false, "other": "%s"}'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('ProviderInput').set_value(False)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('ProviderInput').set_value(True)
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t1', b'true'))

            engine.step()
            await expect_msgs()

            # Test TemplateValueProvider

            engine.get_native_state('TemplateProviderInput').set_value(False)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('TemplateProviderInput').set_value(False)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('TemplateProviderInput').set_value(True)
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t1t', b'{"value": true, "other": "%s"}'))

            engine.step()
            await expect_msgs()

            # Test ValueOptionProvider

            engine.get_native_state('OptionProviderInput').set_value(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('OptionProviderInput').set_value((True,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t2', b'true'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('OptionProviderInput').set_value((True,))
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('OptionProviderInput').set_value((False,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t2', b'false'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('OptionProviderInput').set_value(None)
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t2', b''))

            engine.step()
            await expect_msgs()

            # Test EventEmitter

            engine.get_native_state('EventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('EventSource').send_event((True,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t3', b'true'))

            engine.get_native_state('EventSource').send_event((True,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t3', b'true'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('EventSource').send_event((False,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t3', b'false'))

            engine.get_native_state('EventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test StringEventEmitter

            engine.get_native_state('StringEventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('StringEventSource').send_event(('X1',))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t4', b'"X1"'))

            engine.get_native_state('StringEventSource').send_event(('X1',))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t4', b'"X1"'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('StringEventSource').send_event(('X2',))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t4', b'"X2"'))

            engine.get_native_state('StringEventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test EnumEventEmitter

            engine.get_native_state('EnumEventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('EnumEventSource').send_event((wirely.enum.ThermostatSystemMode.Off,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'"off"'))

            engine.get_native_state('EnumEventSource').send_event((wirely.enum.ThermostatSystemMode.Off,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'"off"'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('EnumEventSource').send_event((wirely.enum.ThermostatSystemMode.EmergencyHeat,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'"emergency_heat"'))

            engine.get_native_state('EnumEventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test TemplateEventEmitter

            engine.get_native_state('TemplateEventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            engine.get_native_state('TemplateEventSource').send_event((wirely.enum.ThermostatSystemMode.Off,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'{"value": "off", "other": "%s"}'))

            engine.get_native_state('TemplateEventSource').send_event((wirely.enum.ThermostatSystemMode.Off,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'{"value": "off", "other": "%s"}'))

            engine.step()
            await expect_msgs()

            engine.get_native_state('TemplateEventSource').send_event((wirely.enum.ThermostatSystemMode.EmergencyHeat,))
            await expect_msgs()

            engine.step()
            await expect_msgs(('test/t5', b'{"value": "emergency_heat", "other": "%s"}'))

            engine.get_native_state('TemplateEventSource').send_event(None)
            await expect_msgs()

            engine.step()
            await expect_msgs()

            # Test EventReceiver

            engine.step()
            engine.assert_value_is('EventReceiver', None)
            await expect_msgs()

            await client.publish('test/t6', payload=b'true')
            await expect_msgs(('test/t6', b'true'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('EventReceiver', (True,))
            await expect_msgs()

            engine.step()
            engine.assert_value_is('EventReceiver', None)
            await expect_msgs()

            # Note: This should be ignored
            await client.publish('test/t6', payload=b'3')
            await expect_msgs(('test/t6', b'3'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('EventReceiver', None)
            await expect_msgs()

            # Test TemplateEventReceiver

            engine.step()
            engine.assert_value_is('TemplateEventReceiver', None)
            await expect_msgs()

            await client.publish('test/t7', payload=b'true')
            await expect_msgs(('test/t7', b'true'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('TemplateEventReceiver', (True,))
            await expect_msgs()

            engine.step()
            engine.assert_value_is('TemplateEventReceiver', None)
            await expect_msgs()

            # Note: This should be ignored
            await client.publish('test/t7', payload=b'3')
            await expect_msgs(('test/t7', b'3'))

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('TemplateEventReceiver', None)
            await expect_msgs()

            print('c', flush=True)
        print('d', flush=True)
