import asyncio
import contextlib

import xknx
import xknx.tools
import wirely
from wirely.knx.connection_adapter import create_xknx


# TODO: Remove duplicates with knx.py
class MessageReceiver:
    def __init__(self, knx, name, address, value_type):
        self.name = name
        self.value_type = value_type

        self.values = []

        if value_type == 'binary':
            self.sensor = xknx.devices.BinarySensor(knx, name="Sensor_" + name, group_address_state=address, device_updated_cb=self.update, ignore_internal_state=True)
        else:
            self.sensor = xknx.devices.Sensor(knx, name="Sensor_" + name, group_address_state=address, device_updated_cb=self.update, value_type=value_type, always_callback=True)
        knx.devices.async_add(self.sensor)

    def update(self, sensor):
        if self.value_type == 'binary':
            value = sensor.remote_value.value
        else:
            value = sensor.sensor_value.value
        self.values.append(value)
        print(f'{self.name}: Got {value}')

    async def expect(self, *expected):
        # TODO: How long should this sleep?
        # TODO: Increase timeout when waiting for a message?
        await asyncio.sleep(0.1)
        # TODO: Use "async with asyncio.timeout(0.02):" instead?

        for msg in expected:
            if len(self.values) == 0:
                raise Exception(f'{self.name}: Expected message {msg!r}, got no message')
            actual = self.values.pop(0)
            if msg != actual:
                raise Exception(f'{self.name}: Expected message {msg!r}, got {actual!r}')

        if len(self.values) != 0:
            raise Exception(f'Got unexpected messages: {self.values!r}')


async def run_test():
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        knx = create_xknx(engine.get_native_state('bus').connection_config)
        async with knx:
            print(engine.get_native_state('bus').knx.current_address, knx.current_address)

            todm = engine.get_native_state_singleton('Schedule.TimeOfDayMinute')

            todm.set_fake_time(wirely.TimeOfDay.parse('07:00'))

            event_sensor_recv = MessageReceiver(knx, 'event_sensor', "0/7/1", "binary")

            await event_sensor_recv.expect()

            engine.step()
            engine.step()
            engine.assert_is_idle()
            await event_sensor_recv.expect()

            todm.set_fake_time(wirely.TimeOfDay.parse('09:00'))
            engine.step()
            engine.step()
            engine.assert_is_idle()
            await event_sensor_recv.expect(True)
