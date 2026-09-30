import asyncio
import contextlib

import wirely as blocks


async def run_test():
    engine.test_twon_fake_server_info.server.set_io_state('ext3.input1', True)
    engine.test_twon_fake_server_info.server.set_switch_state(2, True)

    def assert_port_is(port_name, expected_state):
        actual_state = engine.test_twon_fake_server_info.server.ports[port_name]['state']
        if actual_state != expected_state:
            raise Exception(f'Expected state for port {port_name} is {expected_state}, but got {actual_state}')

    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_value_is('tamper_input', False)
        engine.assert_is_idle()

        # Note: Wait for the subscription to be ready
        await asyncio.sleep(0.1)

        engine.test_twon_fake_server_info.server.emit_key_press('%2')
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('key2_event', (True,))
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.emit_key_release('%2')
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('key2_event', (False,))
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.emit_key_press('%3')
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', True)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', True)
        engine.assert_is_idle()

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(29))
        engine.assert_is_idle()
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', True)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', True)
        engine.assert_is_idle()

        # Timeout
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.emit_key_press('%2')
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('key2_event', (True,))
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(29))
        engine.assert_is_idle()
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        # Still no timeout
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(15))
        engine.assert_is_idle()
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', True)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        # Timeout
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        # IOState

        engine.assert_value_is('tamper_input', False)
        engine.assert_value_is('input1', True)
        engine.assert_value_is('relay1_value', False)

        engine.test_twon_fake_server_info.server.set_io_state('ext3.tamper', True)
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', True)
        engine.assert_value_is('relay1_value', False)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.set_io_state('ext3.relay1', True)
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', True)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.set_io_state('ext3.input1', False)
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        # SwitchState

        engine.assert_value_is('switch1', False)
        engine.assert_value_is('switch2', True)

        engine.test_twon_fake_server_info.server.set_switch_state(1, True)
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', True)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.set_switch_state(2, False)
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', False)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        # CodeEnteredEvent

        engine.assert_value_is('code_1', None)
        engine.assert_value_is('code_2', None)
        engine.assert_value_is('code_3', None)

        engine.test_twon_fake_server_info.server.emit_code_entered('1234')
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('code_1', (None,))
        engine.assert_value_is('code_2', None)
        engine.assert_value_is('code_3', (None,))
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', False)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('code_1', None)
        engine.assert_value_is('code_2', None)
        engine.assert_value_is('code_3', None)
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', False)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.emit_code_entered('44')
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('code_1', None)
        engine.assert_value_is('code_2', None)
        engine.assert_value_is('code_3', None)
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', False)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('code_1', None)
        engine.assert_value_is('code_2', None)
        engine.assert_value_is('code_3', None)
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', False)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        engine.test_twon_fake_server_info.server.emit_code_entered('01')
        # Wait for the event to be read
        await asyncio.sleep(0.1)
        engine.step()
        engine.assert_value_is('code_1', None)
        engine.assert_value_is('code_2', (None,))
        engine.assert_value_is('code_3', None)
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', False)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.step()
        engine.assert_value_is('code_1', None)
        engine.assert_value_is('code_2', None)
        engine.assert_value_is('code_3', None)
        engine.assert_value_is('switch1', True)
        engine.assert_value_is('switch2', False)
        engine.assert_value_is('tamper_input', True)
        engine.assert_value_is('input1', False)
        engine.assert_value_is('relay1_value', True)
        engine.assert_value_is('key2_event', None)
        engine.assert_value_is('key2', False)
        engine.assert_value_is('key3', False)
        engine.assert_is_idle()

        # OutputControl

        assert_port_is('led_secured', False)
        engine.assert_value_is('led_feedback', False)

        engine.get_native_state('output_debug').send_event((True,))

        engine.step()
        # Run send_value() again
        engine.step()
        engine.assert_is_idle()
        # Wait for the value to be delivered
        await asyncio.sleep(0.1)
        assert_port_is('led_secured', True)
        # Note: Because inputs are read before outputs are send this cannot yet be True
        engine.assert_value_is('led_feedback', False)

        # Process events
        engine.step()
        engine.assert_is_idle()
        assert_port_is('led_secured', True)
        engine.assert_value_is('led_feedback', True)

        engine.get_native_state('output_debug').send_event((False,))

        engine.step()
        # Run send_value() again
        engine.step()
        engine.assert_is_idle()
        # Wait for the value to be delivered
        await asyncio.sleep(0.1)
        assert_port_is('led_secured', False)
        # Note: Because inputs are read before outputs are send this cannot yet be False
        engine.assert_value_is('led_feedback', True)

        # Process events
        engine.step()
        engine.assert_is_idle()
        assert_port_is('led_secured', False)
        engine.assert_value_is('led_feedback', False)
