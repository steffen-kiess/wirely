import asyncio
import contextlib

import xknx
import xknx.tools
import wirely
from wirely.knx.connection_adapter import StreamConnectionConfig, create_xknx
from wirely.knx.connection_config import ConnectionConfigError, create_connection_config


async def check_connection_parameters():
    tcp = create_connection_config(
        host='localhost', protocol='TCP', individual_address='1.1.1'
    )
    assert tcp.connection_type == xknx.io.ConnectionType.TUNNELING_TCP
    assert tcp.gateway_ip == 'localhost'
    assert tcp.gateway_port == 3671
    assert str(tcp.individual_address) == '1.1.1'

    udp = create_connection_config(host='localhost', protocol='UDP', port=1234)
    assert udp.connection_type == xknx.io.ConnectionType.TUNNELING
    assert udp.gateway_port == 1234

    local = create_connection_config(unix_path='/run/knxnet')
    local_default = create_connection_config(protocol='Unix')
    local_explicit = create_connection_config(
        protocol='Unix', unix_path='/run/knxnet'
    )
    remote = create_connection_config(
        protocol='Unix', ssh_target='user@example.com'
    )
    remote_custom = create_connection_config(
        ssh_target='user@example.com', unix_path='/tmp/knxnet'
    )
    assert isinstance(local, StreamConnectionConfig)
    assert isinstance(local_default, StreamConnectionConfig)
    assert isinstance(local_explicit, StreamConnectionConfig)
    assert isinstance(remote, StreamConnectionConfig)
    assert isinstance(remote_custom, StreamConnectionConfig)

    class StubLoop:
        async def create_unix_connection(self, factory, *, path):
            return path

    assert await local_default.connect_cb(StubLoop(), None) == '/run/knxnet'

    automatic = create_connection_config(protocol='Auto')
    assert automatic.connection_type == xknx.io.ConnectionType.AUTOMATIC

    invalid = (
        {},
        {'host': 'localhost'},
        {'host': 'localhost', 'protocol': 'tcp'},
        {'protocol': 'TCP'},
        {'port': 3671},
        {'host': 'localhost', 'protocol': 'UDP', 'individual_address': '1.1.1'},
        {'host': 'localhost', 'protocol': 'TCP', 'unix_path': '/run/knxnet'},
        {'protocol': 'Auto', 'unix_path': '/run/knxnet'},
        {'protocol': 'Auto', 'host': 'localhost'},
        {'protocol': 'Unix', 'host': 'localhost'},
        {'protocol': 'Unix', 'port': 3671},
        {'protocol': 'TCP', 'unix_path': '/run/knxnet'},
        {'ssh_target': 'user@example.com'},
        {'unix_path': 'relative'},
        {'ssh_target': '-invalid'},
        {'host': 'localhost', 'protocol': 'TCP', 'port': 0},
    )
    for parameters in invalid:
        try:
            create_connection_config(**parameters)
        except ConnectionConfigError:
            continue
        raise AssertionError(f'Accepted invalid KNX connection: {parameters!r}')


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
    await check_connection_parameters()

    # TODO: Add timeouts?
    # TODO: Move more of this functionality to test.py?
    # TODO: Make sure that exceptions in KNX callbacks cause the test to fail?
    # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        print('a', flush=True)
        knx = create_xknx(engine.get_native_state('Bus').connection_config)
        async with knx:
            print('b', flush=True)
            print(engine.get_native_state('Bus').knx.current_address, knx.current_address)

            engine.get_native_state('ProviderInput').set_value(False)

            # status = xknx.devices.Sensor(knx, name="Sensor", group_address_state="0/6/7", value_type="binary")
            status = xknx.devices.BinarySensor(knx, name="Sensor", group_address_state="0/6/7")
            knx.devices.async_add(status)
            def assert_state(expected):
                # print(f'state: {status.state}')
                actual = status.state
                if actual != expected:
                    raise Exception(f'Expected state {expected}, got state {actual} for 0/6/7')

            assert_state(None)

            engine.step()
            engine.assert_value_is('Receiver', None)
            engine.assert_value_is('ReceiverValue', False)
            engine.assert_value_is('ReceiverValue2', True)

            # TODO: Should this assert be here?
            assert_state(None)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            assert_state(False)

            expose = xknx.devices.ExposeSensor(knx, name="Expose", group_address="0/4/5", value_type="binary")
            knx.devices.async_add(expose)  # TODO: Is this needed?
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            assert_state(False)

            engine.step()
            engine.assert_value_is('Receiver', None)
            engine.assert_value_is('ReceiverValue', False)
            engine.assert_value_is('ReceiverValue2', True)

            assert_state(False)

            await expose.set(True)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.01)

            engine.step()
            engine.assert_value_is('Receiver', wirely.Some(True))
            engine.assert_value_is('ReceiverValue', True)
            engine.assert_value_is('ReceiverValue2', True)

            await expose.set(False)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.01)

            engine.step()
            engine.assert_value_is('Receiver', wirely.Some(False))
            engine.assert_value_is('ReceiverValue', False)
            engine.assert_value_is('ReceiverValue2', False)

            assert_state(False)

            engine.get_native_state('ProviderInput').set_value(True)
            engine.step()
            # TODO: How long should this sleep?
            await asyncio.sleep(0.01)
            assert_state(True)

            engine.step()
            # TODO: How long should this sleep?
            await asyncio.sleep(0.01)
            assert_state(True)

            engine.get_native_state('ProviderInput').set_value(False)
            engine.step()
            # TODO: How long should this sleep?
            await asyncio.sleep(0.01)
            assert_state(False)


            status_sensors = [
                # Note: xknx by default limits the number of outstanding read requests to 2 (StateUpdater ctor parallel_reads parameter), and 0/7/11 and 0/7/13 will not return a value, so if they are allowed to be synced then 0/7/14 will not be read until after 2s (the timeout). Prevent this by disabling sync_state for 0/7/11 and 0/7/13.
                xknx.devices.Sensor(knx, name="Sensor", group_address_state="0/7/11", value_type="2byte_unsigned", sync_state=False),
                xknx.devices.Sensor(knx, name="Sensor", group_address_state="0/7/12", value_type="2byte_unsigned"),
                xknx.devices.BinarySensor(knx, name="Sensor", group_address_state="0/7/13", sync_state=False),
                xknx.devices.BinarySensor(knx, name="Sensor", group_address_state="0/7/14"),
            ]
            for sensor in status_sensors:
                knx.devices.async_add(sensor)
            def assert_status_state(*expected):
                # print(f'state: {status.state}')
                if len(expected) != len(status_sensors):
                    raise Exception('len(expected) != len(status_sensors)')
                for exp, sensor in zip(expected, status_sensors):
                    if isinstance(sensor, xknx.devices.BinarySensor):
                        actual = sensor.state
                        address = sensor.remote_value.group_address_state
                    else:
                        actual = sensor.sensor_value.value
                        address = sensor.sensor_value.group_address_state
                    if actual != exp:
                        raise Exception(f'Expected state {exp}, got state {actual} for {address}')

            engine.assert_value_is('JraStatus', None)
            engine.assert_value_is('Status.DriveInMotion', None)
            engine.assert_value_is('StatusNonOption.DriveInMotion', False)
            print('KNX Test: Waiting after initialization', flush=True)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            print('KNX Test: After initialization', flush=True)
            assert_status_state(None, 0, None, False)

            # TODO: Do this synchronization?
            for sensor in status_sensors:
                await sensor.sync()
            print('KNX Test: Waiting after sync', flush=True)
            # TODO: Why is this sleep needed?
            await asyncio.sleep(0.1)
            print('KNX Test: After sync', flush=True)
            assert_status_state(None, 0, None, False)

            jra_status_expose = xknx.devices.ExposeSensor(knx, name="Expose", group_address="0/7/10", value_type="2byte_unsigned")
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            engine.step()
            engine.assert_value_is('JraStatus', None)
            engine.assert_value_is('Status.DriveInMotion', None)
            engine.assert_value_is('StatusNonOption.DriveInMotion', False)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            assert_status_state(None, 0, None, False)

            await jra_status_expose.set(0x1234)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            engine.step()
            engine.assert_value_is('JraStatus', wirely.Some(0x1234))
            engine.assert_value_is('Status.DriveInMotion', wirely.Some(True))
            engine.assert_value_is('StatusNonOption.DriveInMotion', True)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            assert_status_state(0x1234, 0x1234, True, True)

            await jra_status_expose.set(0x4567)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            engine.step()
            engine.assert_value_is('JraStatus', wirely.Some(0x4567))
            engine.assert_value_is('Status.DriveInMotion', wirely.Some(False))
            engine.assert_value_is('StatusNonOption.DriveInMotion', False)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            assert_status_state(0x4567, 0x4567, False, False)

            # TODO: Actually reset the expose sensor here
            # await jra_status_expose.set(None)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            engine.step()
            engine.assert_value_is('JraStatus', wirely.Some(0x4567))
            engine.assert_value_is('Status.DriveInMotion', wirely.Some(False))
            engine.assert_value_is('StatusNonOption.DriveInMotion', False)
            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)
            assert_status_state(0x4567, 0x4567, False, False)

            # Test EventReceiver

            engine.step()
            engine.assert_value_is('EventReceiver', None)
            engine.assert_value_is('IntEventReceiver', None)

            xknx.tools.group_value_write(knx, "0/3/3", True)

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('EventReceiver', wirely.Some(True))
            engine.assert_value_is('IntEventReceiver', None)

            engine.step()
            engine.assert_value_is('EventReceiver', None)
            engine.assert_value_is('IntEventReceiver', None)

            xknx.tools.group_value_write(knx, "0/3/3", True)

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('EventReceiver', wirely.Some(True))
            engine.assert_value_is('IntEventReceiver', None)

            # Test IntEventReceiver

            engine.step()
            engine.assert_value_is('IntEventReceiver', None)
            engine.assert_value_is('EventReceiver', None)

            xknx.tools.group_value_write(knx, "0/3/4", 100, "4byte_signed")

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('IntEventReceiver', wirely.Some(100))
            engine.assert_value_is('EventReceiver', None)

            engine.step()
            engine.assert_value_is('IntEventReceiver', None)
            engine.assert_value_is('EventReceiver', None)

            xknx.tools.group_value_write(knx, "0/3/4", 100, "4byte_signed")

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('IntEventReceiver', wirely.Some(100))
            engine.assert_value_is('EventReceiver', None)

            xknx.tools.group_value_write(knx, "0/3/4", 100, "4byte_signed")
            xknx.tools.group_value_write(knx, "0/3/4", -10, "4byte_signed")
            xknx.tools.group_value_write(knx, "0/3/4", -10, "4byte_signed")
            xknx.tools.group_value_write(knx, "0/3/4", 1234567, "4byte_signed")

            # TODO: How long should this sleep?
            await asyncio.sleep(0.1)

            engine.step()
            engine.assert_value_is('IntEventReceiver', wirely.Some(100))
            engine.assert_value_is('EventReceiver', None)

            engine.step()
            engine.assert_value_is('IntEventReceiver', wirely.Some(-10))
            engine.assert_value_is('EventReceiver', None)

            engine.step()
            engine.assert_value_is('IntEventReceiver', wirely.Some(-10))
            engine.assert_value_is('EventReceiver', None)

            engine.step()
            engine.assert_value_is('IntEventReceiver', wirely.Some(1234567))
            engine.assert_value_is('EventReceiver', None)

            engine.step()
            engine.assert_value_is('IntEventReceiver', None)
            engine.assert_value_is('EventReceiver', None)

            # Test EventEmitter

            event_sensor_recv = MessageReceiver(knx, 'event_sensor', "0/3/1", "binary")

            engine.get_native_state('EventEmitterInput').send_event(None)
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect()

            engine.get_native_state('EventEmitterInput').send_event(wirely.Some(True))
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect(True)

            engine.get_native_state('EventEmitterInput').send_event(wirely.Some(True))
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect(True)

            engine.step()
            await event_sensor_recv.expect()

            engine.get_native_state('EventEmitterInput').send_event(wirely.Some(False))
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect(False)

            engine.get_native_state('EventEmitterInput').send_event(None)
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect()

            # Test IntEventEmitter

            event_sensor_recv = MessageReceiver(knx, 'event_sensor', "0/3/2", "2byte_signed")

            engine.get_native_state('IntEventEmitterInput').send_event(None)
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect()

            engine.get_native_state('IntEventEmitterInput').send_event(wirely.Some(55))
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect(55)

            engine.get_native_state('IntEventEmitterInput').send_event(wirely.Some(55))
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect(55)

            engine.step()
            await event_sensor_recv.expect()

            engine.get_native_state('IntEventEmitterInput').send_event(wirely.Some(-10000))
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect(-10000)

            engine.get_native_state('IntEventEmitterInput').send_event(None)
            await event_sensor_recv.expect()

            engine.step()
            await event_sensor_recv.expect()

            print('c', flush=True)
        print('d', flush=True)
