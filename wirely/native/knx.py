import asyncio
import contextlib
from typing import Any, Literal

import wirely
from wirely import Some, UInt16
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.generated import knx as contracts
from wirely.knx.value_types import get_value_type
from wirely.native.support import check_state_type
from wirely.generated.enums import KNXProtocol
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.util import verbose_print


def _protocol_name(option: Some[KNXProtocol] | None) -> str | None:
    if option is None:
        return None
    name = option.value.name
    if name is None:
        raise ValueError(f'Unsupported KNX protocol {int(option.value)}')
    return name


class ConnectionCreate(contracts.ConnectionCreate):
    def initialize(self, *, services: StateServices[Literal['bus']], host: Some[str] | None,
                   protocol: Some[KNXProtocol] | None, port: Some[UInt16] | None, unix_path: Some[str] | None,
                   ssh_target: Some[str] | None,
                   individual_address: Some[str] | None) -> contracts.ConnectionCreateResult:
        from wirely.knx.connection_config import create_connection_config
        from wirely.knx.connection_adapter import create_xknx

        verbose_print('Create KNX connection', flush=True)

        def parameter(value: Some[str] | None) -> str | None:
            return None if value is None else value.value

        self.connection_config = create_connection_config(
            host=parameter(host),
            protocol=_protocol_name(protocol),
            port=None if port is None else port.value.value,
            unix_path=parameter(unix_path),
            ssh_target=parameter(ssh_target),
            individual_address=parameter(individual_address),
        )
        self.knx = create_xknx(self.connection_config)
        return contracts.ConnectionCreateResult(bus=self)

    # TODO: Do start() later, while everything is already running?
    async def start_tasks_async(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        verbose_print('Connecting to KNX bus', flush=True)
        await async_exit_stack.enter_async_context(self.knx)
        verbose_print('Connecting to KNX bus done', flush=True)


class ValueReceiverCreate(contracts.ValueReceiverCreate):
    def initialize(self, *, services: StateServices[Literal['state']], bus: ConnectionCreate, type: ValueType[Any],
                   group_address: UInt16, value_type_name: str) -> contracts.ValueReceiverCreateResult:
        self.knx_type = get_value_type(type, value_type_name)
        self.current_value: DynamicValue | None = None

        import xknx

        verbose_print('Create KNX ValueReceiver', flush=True)

        verbose_print(f'  Using group address: {group_address.value!r}', flush=True)
        verbose_print(f'  Using type: {type!r}', flush=True)

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address.value)

        self.value_type = type
        self.value_type_knx = value_type_name

        # TODO: Where does this get cleaned up?
        if self.value_type_knx == "binary":
            self.sensor: xknx.devices.BinarySensor | xknx.devices.Sensor = xknx.devices.BinarySensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, device_updated_cb=self.update_callback)
        else:
            self.sensor = xknx.devices.Sensor(self.native_bus.knx, name="Sensor", group_address_state=self.address, value_type=self.value_type_knx, device_updated_cb=self.update_callback)
        self.native_bus.knx.devices.async_add(self.sensor)
        return contracts.ValueReceiverCreateResult(state=self)

    # TODO: Make sure exceptions here are handled somehow?
    def update_callback(self, device: Any) -> None:
        # print('update_callback()', flush=True)
        if self.value_type_knx == "binary":
            val = device.state
        else:
            val = device.sensor_value.value
        # print(f'  got value: {val!r}', flush=True)
        self.current_value = self.knx_type.from_knx(val, self.services.native_types)

        self.services.trigger('state')


class ValueReceiverGetValue(contracts.ValueReceiverGetValue):
    def prepare(self, *, services: PreparationServices, state: 'ValueReceiverCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices,
            state: 'ValueReceiverCreate', type: ValueType[Any]) -> contracts.ValueReceiverGetValueResult:
        # TODO: Add checks whether everything is running?

        res = state.current_value

        if res is None:
            res_opt = None
        else:
            res_opt = wirely.Some(res)
        # print(f'get_value: {res_opt}')

        return contracts.ValueReceiverGetValueResult(output=res_opt)


class ValueOptionProviderCreate(contracts.ValueOptionProviderCreate):
    def initialize(self, *, services: StateServices[Literal['state']], bus: ConnectionCreate, type: ValueType[Any],
                   group_address: UInt16,
                   value_type_name: str) -> contracts.ValueOptionProviderCreateResult:
        self.knx_type = get_value_type(type, value_type_name)
        # TODO: Is this needed?
        self.current_value = None

        # Task is needed for sending out values
        self.task: asyncio.Task | None = None
        self.value_to_send: Any = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        import xknx

        verbose_print('Create KNX ValueOptionProvider', flush=True)

        verbose_print(f'  Using group address: {group_address.value!r}', flush=True)
        verbose_print(f'  Using type: {type!r}', flush=True)

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address.value)

        self.value_type = type
        self.value_type_knx = value_type_name

        # TODO: Where does this get cleaned up?
        self.expose = xknx.devices.ExposeSensor(self.native_bus.knx, name="Expose", group_address=self.address, value_type=self.value_type_knx)
        self.native_bus.knx.devices.async_add(self.expose)  # TODO: Is this needed?
        return contracts.ValueOptionProviderCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    async def run_task(self) -> None:
        while True:
            while self.value_to_send is None:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.value_to_send
            self.value_to_send = None

            verbose_print(f'Sending {value} to {self.address}', flush=True)
            await self.expose.set(value)
            verbose_print(f'Sending {value} to {self.address} done', flush=True)


class ValueOptionProviderSetValue(contracts.ValueOptionProviderSetValue):
    def prepare(self, *, services: PreparationServices, state: 'ValueOptionProviderCreate',
                type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'ValueOptionProviderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.ValueOptionProviderSetValueResult:
        # TODO: Add checks whether everything is running?

        if input is None:
            # TODO: Somehow reset the state.expose value in this case?
            value = None
        else:
            value = state.knx_type.to_knx(input.value, state.services.native_types)

        verbose_print(f'Setting output {state.address} to {value}, old value_to_send is {state.value_to_send}', flush=True)

        # This does not work because this function is not async
        # Also this would have to handle None values
        # await state.expose.set(value)
        # Note: This can overwrite a non-None value if sending is too slow. In this case, the overwritten value will be discarded and not sent at all.
        # TODO: Provide some notification if this is happening?
        state.value_to_send = value
        state.event.set()
        return contracts.ValueOptionProviderSetValueResult()


class EventReceiverCreate(contracts.EventReceiverCreate):
    def initialize(self, *, services: StateServices[Literal['state']], bus: ConnectionCreate, type: ValueType[Any],
                   group_address: UInt16, value_type_name: str) -> contracts.EventReceiverCreateResult:
        self.knx_type = get_value_type(type, value_type_name)
        self.received_values: list[DynamicValue] = []

        self.value_type = type
        self.value_type_knx = value_type_name

        verbose_print('Create KNX EventReceiver', flush=True)

        import xknx
        from xknx.dpt import DPTBase

        verbose_print(f'  Using group address: {group_address.value!r}', flush=True)
        verbose_print(f'  Using type: {self.value_type!r} / {self.value_type_knx}', flush=True)

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address.value)
        # Booleans are decoded in telegram_received().
        self.transcoder = None if value_type_name == 'binary' else DPTBase.parse_transcoder(value_type_name)

        # The telegrams themselves are used rather than an xknx device, which
        # also reports read responses. Outgoing telegrams are included, so that
        # an EventSender for the same address in this program is received too.
        # TODO: Where does this get cleaned up?
        self.native_bus.knx.telegram_queue.register_telegram_received_cb(
            self.telegram_received, group_addresses=[self.address], match_for_outgoing=True)
        return contracts.EventReceiverCreateResult(state=self)

    # TODO: Make sure exceptions here are handled somehow?
    def telegram_received(self, telegram: Any) -> None:
        from xknx.dpt import DPTBinary
        from xknx.exceptions import ConversionError, CouldNotParseTelegram
        from xknx.telegram.apci import GroupValueWrite

        # Only writes are events; read responses report the current value.
        if not isinstance(telegram.payload, GroupValueWrite):
            return
        payload = telegram.payload.value
        try:
            if self.transcoder is None:
                # DPT 1 values are carried in the APCI as a single bit.
                if not isinstance(payload, DPTBinary) or payload.value not in (0, 1):
                    raise CouldNotParseTelegram('Invalid payload for a boolean', payload=str(payload))
                val: Any = payload.value == 1
            else:
                val = self.transcoder.from_knx(payload)
        except (ConversionError, CouldNotParseTelegram) as error:
            print(f'Warning: KNX.EventReceiver: Ignoring {telegram}: {error}', flush=True)
            return
        verbose_print(f'KNX.EventReceiver: got value: {val!r}', flush=True)
        value = self.knx_type.from_knx(val, self.services.native_types)

        self.received_values.append(value)
        self.services.trigger('state')


class EventReceiverGetValue(contracts.EventReceiverGetValue):
    def prepare(self, *, services: PreparationServices, state: 'EventReceiverCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices,
            state: 'EventReceiverCreate', type: ValueType[Any]) -> contracts.EventReceiverGetValueResult:
        # TODO: Add checks whether everything is running?

        if len(state.received_values) == 0:
            return contracts.EventReceiverGetValueResult(output=None)
        value = state.received_values.pop(0)

        res_opt = wirely.Some(value)
        # print(f'get_value: {res_opt}')

        services.trigger_self()

        return contracts.EventReceiverGetValueResult(output=res_opt)


class EventSenderCreate(contracts.EventSenderCreate):
    def initialize(self, *, services: StateServices[Literal['state']], bus: ConnectionCreate, type: ValueType[Any],
                   group_address: UInt16, value_type_name: str) -> contracts.EventSenderCreateResult:
        self.knx_type = get_value_type(type, value_type_name)
        # Task is needed for sending out values
        self.task: asyncio.Task | None = None
        self.values_to_send: list[object] = []
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        import xknx

        verbose_print('Create KNX ValueOptionProvider', flush=True)

        self.value_type = type
        self.value_type_knx = value_type_name

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = xknx.telegram.GroupAddress(group_address.value)

        # TODO: Where does this get cleaned up?
        # Use ExposeSensor instead of Sensor because Sensor does not support binary and BinarySensor does not support writing values
        self.sensor = xknx.devices.ExposeSensor(self.native_bus.knx, name="EventSenderCreate", group_address=self.address, respond_to_read=False, value_type=self.value_type_knx)
        self.native_bus.knx.devices.async_add(self.sensor)  # TODO: Is this needed?
        return contracts.EventSenderCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    async def run_task(self) -> None:
        while True:
            while len(self.values_to_send) == 0:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.values_to_send.pop(0)

            verbose_print(f'Sending {value} to {self.address}', flush=True)
            # TODO: Call self.sensor.sensor_value.set(value) directly instead?
            await self.sensor.set(value)
            verbose_print(f'Sending {value} to {self.address} done', flush=True)


class EventSenderSendValue(contracts.EventSenderSendValue):
    def prepare(self, *, services: PreparationServices, state: 'EventSenderCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'EventSenderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.EventSenderSendValueResult:
        # TODO: Add checks whether everything is running?

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if input is None:
            return contracts.EventSenderSendValueResult()
        else:
            value = state.knx_type.to_knx(input.value, state.services.native_types)

        verbose_print(f'Sending event {value} to {state.address}, old values_to_send is {state.values_to_send}', flush=True)

        # Note: If sending is too slow, there might be multiple events queued up.
        # TODO: Provide a notification in this case?
        state.values_to_send.append(value)
        state.event.set()

        # This makes sure that the value is sent again if the input does not change
        services.trigger_self()
        return contracts.EventSenderSendValueResult()


Bus = ConnectionCreate  # Used as a resource type.
ValueReceiverState = ValueReceiverCreate  # Used as a resource type.
ValueOptionProviderState = ValueOptionProviderCreate  # Used as a resource type.
EventReceiverState = EventReceiverCreate  # Used as a resource type.
EventSenderState = EventSenderCreate  # Used as a resource type.
