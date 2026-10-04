import asyncio
import contextlib
import dataclasses
import typing

import wirely as blocks
from wirely.native import native_block_type

native_block_types = {}


@dataclasses.dataclass(frozen=True)
class KnxData:
    group_address_raw: int
    value_type: blocks.ValueType
    # TODO: Should this be here or should this be created later?
    value_type_knx: str


    @property
    def group_address(self):
        import xknx
        return xknx.telegram.GroupAddress(self.group_address_raw)

    def dump(self):
        print(f'  Using group address: {self.group_address!r}', flush=True)
        print(f'  Using type: {self.value_type!r} / {self.value_type_knx}', flush=True)


@native_block_type(native_block_types, has_native_state=True)
class Connection(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)

        from wirely.knx.connection_config import create_connection_config
        from wirely.knx.connection_adapter import create_xknx

        print('Create KNX connection', flush=True)

        def parameter(name):
            value = context.values[name]
            return None if value is None else value.value

        self.connection_config = create_connection_config(
            host=parameter('Host'),
            protocol=parameter('Protocol'),
            port=parameter('Port'),
            unix_path=parameter('UnixPath'),
            ssh_target=parameter('SSHTarget'),
            individual_address=parameter('IndividualAddress'),
        )
        self.knx = create_xknx(self.connection_config)

    # TODO: Do start() later, while everything is already running?
    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        print('Connecting to KNX bus', flush=True)
        await async_exit_stack.enter_async_context(self.knx)
        print('Connecting to KNX bus done', flush=True)


@native_block_type(native_block_types, has_native_state=True)
class ValueReceiver(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        self.current_value = None

        import xknx

        print('Create KNX ValueReceiver', flush=True)

        group_address = context.values['GroupAddress']
        ty = context.values['Type']
        print(f'  Using group address: {group_address!r}', flush=True)
        print(f'  Using type: {ty!r}', flush=True)

        self.native_bus = context.inputs['Bus']
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address)

        self.value_type = ty
        if ty == blocks.value_types.boolean:
            self.value_type_knx = "binary"
        elif ty == blocks.value_types.uint16:
            self.value_type_knx = "2byte_unsigned"
        else:
            raise Exception(f'TODO: Unsupported type for KNX.ValueReceiver: {ty}')

        # TODO: Where does this get cleaned up?
        if self.value_type_knx == "binary":
            self.sensor = xknx.devices.BinarySensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, device_updated_cb=self.update_callback)
        else:
            self.sensor = xknx.devices.Sensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, value_type=self.value_type_knx, device_updated_cb=self.update_callback)
        self.native_bus.knx.devices.async_add(self.sensor)

    # TODO: Make sure exceptions here are handled somehow?
    def update_callback(self, device):
        # print('update_callback()', flush=True)
        if self.value_type_knx == "binary":
            val = device.state
        else:
            val = device.sensor_value.value
        # print(f'  got value: {val!r}', flush=True)
        self.values['Type'].validate(val)

        self.current_value = val

        self.trigger('get_value')

    def get_value(self, *, context):
        # TODO: Add checks whether everything is running?

        res = self.current_value

        if res is None:
            res_opt = None
        else:
            res_opt = blocks.Some(res)
        # print(f'get_value: {res_opt}')

        context.set_result('output', res_opt)


@native_block_type(native_block_types, has_native_state=True)
class ValueOptionProvider(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)

        # TODO: Is this needed?
        self.current_value = None

        # Task is needed for sending out values
        self.task = None
        self.value_to_send = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        import xknx

        print('Create KNX ValueOptionProvider', flush=True)

        group_address = context.values['GroupAddress']
        ty = context.values['Type']
        print(f'  Using group address: {group_address!r}', flush=True)
        print(f'  Using type: {ty!r}', flush=True)

        self.native_bus = context.inputs['Bus']
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address)

        self.value_type = ty
        if ty == blocks.value_types.boolean:
            self.value_type_knx = "binary"
        elif ty == blocks.value_types.uint16:
            self.value_type_knx = "2byte_unsigned"
        else:
            raise Exception(f'TODO: Unsupported type for KNX.ValueOptionProvider: {ty}')

        # TODO: Where does this get cleaned up?
        self.expose = xknx.devices.ExposeSensor(self.native_bus.knx, name="Expose", group_address=self.address, value_type=self.value_type_knx)
        self.native_bus.knx.devices.async_add(self.expose)  # TODO: Is this needed?

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    def set_value(self, *, context):
        # TODO: Add checks whether everything is running?

        value_opt = context.inputs['input']

        if value_opt is None:
            # TODO: Somehow reset the self.expose value in this case?
            value = None
        else:
            value = value_opt.value
            # TODO: Any checks needed here?

        print(f'Setting output {self.address} to {value}, old value_to_send is {self.value_to_send}', flush=True)

        # This does not work because this function is not async
        # Also this would have to handle None values
        # await self.expose.set(value)
        # Note: This can overwrite a non-None value if sending is too slow. In this case, the overwritten value will be discarded and not sent at all.
        # TODO: Provide some notification if this is happening?
        self.value_to_send = value
        self.event.set()

    async def run_task(self):
        while True:
            while self.value_to_send is None:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.value_to_send
            self.value_to_send = None

            print(f'Sending {value} to {self.address}', flush=True)
            await self.expose.set(value)
            print(f'Sending {value} to {self.address} done', flush=True)


@native_block_type(native_block_types, has_native_state=True, data_type=KnxData)
class EventReceiver(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)

        self.task = None
        self.received_values = []

        self.data = context.configuration

        print('Create KNX EventReceiver', flush=True)

        import xknx

        self.data.dump()

        self.native_bus = context.inputs['Bus']
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(self.data.group_address)

        # TODO: Where does this get cleaned up?
        # TODO: Use RemoteValue directly?
        # TODO: Try to make sure that GroupValueResponses are ignored and only GroupValueWrites are used?
        if self.data.value_type_knx == "binary":
            self.sensor = xknx.devices.BinarySensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, device_updated_cb=self.event_callback, sync_state=False, ignore_internal_state=True)
        else:
            self.sensor = xknx.devices.Sensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, value_type=self.data.value_type_knx, device_updated_cb=self.event_callback, sync_state=False, always_callback=True)
        self.native_bus.knx.devices.async_add(self.sensor)

    # TODO: Make sure exceptions here are handled somehow?
    def event_callback(self, device):
        # print('update_callback()', flush=True)
        if self.data.value_type_knx == "binary":
            val = device.state
        else:
            val = device.sensor_value.value
        print(f'KNX.EventReceiver: got value: {val!r}', flush=True)
        value = self.values['Type'].validate(val)

        self.received_values.append(value)
        self.trigger('get_value')

    def get_value(self, *, context):
        # TODO: Add checks whether everything is running?

        if len(self.received_values) == 0:
            context.set_result('output', None)
            return
        value = self.received_values.pop(0)

        res_opt = blocks.Some(value)
        # print(f'get_value: {res_opt}')

        context.set_result('output', res_opt)

        self.trigger('get_value')


@native_block_type(native_block_types, has_native_state=True, data_type=KnxData)
class EventSender(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)

        # Task is needed for sending out values
        self.task = None
        self.values_to_send = []
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        import xknx

        print('Create KNX ValueOptionProvider', flush=True)

        self.data = context.configuration

        self.native_bus = context.inputs['Bus']
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(self.data.group_address)

        # TODO: Where does this get cleaned up?
        # Use ExposeSensor instead of Sensor because Sensor does not support binary and BinarySensor does not support writing values
        self.sensor = xknx.devices.ExposeSensor(self.native_bus.knx, name="EventSender", group_address=self.address, respond_to_read=False, value_type=self.data.value_type_knx)
        self.native_bus.knx.devices.async_add(self.sensor)  # TODO: Is this needed?

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    def send_value(self, *, context):
        # TODO: Add checks whether everything is running?

        data = context.configuration

        value_opt = context.inputs['input']

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if value_opt is None:
            return
        else:
            value = value_opt.value
            # TODO: Any checks needed here?

        print(f'Sending event {value} to {data.group_address}, old values_to_send is {self.values_to_send}', flush=True)

        # Note: If sending is too slow, there might be multiple events queued up.
        # TODO: Provide a notification in this case?
        self.values_to_send.append(value)
        self.event.set()

        # This makes sure that the value is sent again if the input does not change
        self.trigger('send_value')

    async def run_task(self):
        while True:
            while len(self.values_to_send) == 0:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.values_to_send.pop(0)

            print(f'Sending {value} to {self.address}', flush=True)
            # TODO: Call self.sensor.sensor_value.set(value) directly instead?
            await self.sensor.set(value)
            print(f'Sending {value} to {self.address} done', flush=True)
