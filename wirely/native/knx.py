import asyncio
import contextlib

import wirely
from wirely.native import native_node_type, native_resource_type
from wirely.runtime.contexts import NativeState

native_node_types = {}
native_resource_types = {}


@native_resource_type(native_resource_types, 'Bus')
@native_node_type(native_node_types, has_native_state=True)
class Connection(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('bus', self)

        from wirely.knx.connection_config import create_connection_config
        from wirely.knx.connection_adapter import create_xknx

        print('Create KNX connection', flush=True)

        def parameter(name):
            value = context.values[name]
            return None if value is None else value.value

        protocol = parameter('Protocol')
        self.connection_config = create_connection_config(
            host=parameter('Host'),
            protocol=None if protocol is None else protocol.native.name,
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


@native_resource_type(native_resource_types, 'ValueReceiverState')
@native_node_type(native_node_types, has_native_state=True)
class ValueReceiver(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)
        self.current_value = None

        import xknx

        print('Create KNX ValueReceiver', flush=True)

        group_address = context.values['group_address']
        ty = context.values['Type']
        print(f'  Using group address: {group_address!r}', flush=True)
        print(f'  Using type: {ty!r}', flush=True)

        self.native_bus = context.inputs['Bus'].object
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address)

        self.value_type = ty
        self.value_type_knx = context.values['value_type_name']

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

        self.trigger('state')

    @staticmethod
    def get_value(*, context):
        state = context.inputs['state'].object
        # TODO: Add checks whether everything is running?

        res = state.current_value

        if res is None:
            res_opt = None
        else:
            res_opt = wirely.Some(res)
        # print(f'get_value: {res_opt}')

        context.set_output('output', res_opt)


@native_resource_type(native_resource_types, 'ValueOptionProviderState')
@native_node_type(native_node_types, has_native_state=True)
class ValueOptionProvider(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

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

        group_address = context.values['group_address']
        ty = context.values['Type']
        print(f'  Using group address: {group_address!r}', flush=True)
        print(f'  Using type: {ty!r}', flush=True)

        self.native_bus = context.inputs['Bus'].object
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address)

        self.value_type = ty
        self.value_type_knx = context.values['value_type_name']

        # TODO: Where does this get cleaned up?
        self.expose = xknx.devices.ExposeSensor(self.native_bus.knx, name="Expose", group_address=self.address, value_type=self.value_type_knx)
        self.native_bus.knx.devices.async_add(self.expose)  # TODO: Is this needed?

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @staticmethod
    def set_value(*, context):
        state = context.inputs['state'].object
        # TODO: Add checks whether everything is running?

        value_opt = context.inputs['input']

        if value_opt is None:
            # TODO: Somehow reset the state.expose value in this case?
            value = None
        else:
            value = value_opt.value
            # TODO: Any checks needed here?

        print(f'Setting output {state.address} to {value}, old value_to_send is {state.value_to_send}', flush=True)

        # This does not work because this function is not async
        # Also this would have to handle None values
        # await state.expose.set(value)
        # Note: This can overwrite a non-None value if sending is too slow. In this case, the overwritten value will be discarded and not sent at all.
        # TODO: Provide some notification if this is happening?
        state.value_to_send = value
        state.event.set()

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


@native_resource_type(native_resource_types, 'EventReceiverState')
@native_node_type(native_node_types, has_native_state=True)
class EventReceiver(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

        self.task = None
        self.received_values = []

        self.value_type = context.values['Type']
        self.value_type_knx = context.values['value_type_name']

        print('Create KNX EventReceiver', flush=True)

        import xknx

        print(f"  Using group address: {context.values['group_address']!r}", flush=True)
        print(f'  Using type: {self.value_type!r} / {self.value_type_knx}', flush=True)

        self.native_bus = context.inputs['Bus'].object
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(context.values['group_address'])

        # TODO: Where does this get cleaned up?
        # TODO: Use RemoteValue directly?
        # TODO: Try to make sure that GroupValueResponses are ignored and only GroupValueWrites are used?
        if self.value_type_knx == "binary":
            self.sensor = xknx.devices.BinarySensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, device_updated_cb=self.event_callback, sync_state=False, ignore_internal_state=True)
        else:
            self.sensor = xknx.devices.Sensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, value_type=self.value_type_knx, device_updated_cb=self.event_callback, sync_state=False, always_callback=True)
        self.native_bus.knx.devices.async_add(self.sensor)

    # TODO: Make sure exceptions here are handled somehow?
    def event_callback(self, device):
        # print('update_callback()', flush=True)
        if self.value_type_knx == "binary":
            val = device.state
        else:
            val = device.sensor_value.value
        print(f'KNX.EventReceiver: got value: {val!r}', flush=True)
        value = self.values['Type'].validate(val)

        self.received_values.append(value)
        self.trigger('state')

    @staticmethod
    def get_value(*, context):
        state = context.inputs['state'].object
        # TODO: Add checks whether everything is running?

        if len(state.received_values) == 0:
            context.set_output('output', None)
            return
        value = state.received_values.pop(0)

        res_opt = wirely.Some(value)
        # print(f'get_value: {res_opt}')

        context.set_output('output', res_opt)

        context.trigger_self()


@native_resource_type(native_resource_types, 'EventSenderState')
@native_node_type(native_node_types, has_native_state=True)
class EventSender(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

        # Task is needed for sending out values
        self.task = None
        self.values_to_send = []
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        import xknx

        print('Create KNX ValueOptionProvider', flush=True)

        self.value_type = context.values['Type']
        self.value_type_knx = context.values['value_type_name']

        self.native_bus = context.inputs['Bus'].object
        print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(context.values['group_address'])

        # TODO: Where does this get cleaned up?
        # Use ExposeSensor instead of Sensor because Sensor does not support binary and BinarySensor does not support writing values
        self.sensor = xknx.devices.ExposeSensor(self.native_bus.knx, name="EventSender", group_address=self.address, respond_to_read=False, value_type=self.value_type_knx)
        self.native_bus.knx.devices.async_add(self.sensor)  # TODO: Is this needed?

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @staticmethod
    def send_value(*, context):
        state = context.inputs['state'].object
        # TODO: Add checks whether everything is running?

        value_opt = context.inputs['input']

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if value_opt is None:
            return
        else:
            value = value_opt.value
            # TODO: Any checks needed here?

        print(f'Sending event {value} to {state.address}, old values_to_send is {state.values_to_send}', flush=True)

        # Note: If sending is too slow, there might be multiple events queued up.
        # TODO: Provide a notification in this case?
        state.values_to_send.append(value)
        state.event.set()

        # This makes sure that the value is sent again if the input does not change
        context.trigger_self()

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
