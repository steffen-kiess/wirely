import asyncio
import collections
import contextlib
import dataclasses
import random
from typing import Any, Literal

from xknx.core.connection_state import XknxConnectionState
from xknx.dpt import DPTArray, DPTBase, DPTBinary
from xknx.exceptions import ConversionError, CouldNotParseTelegram, XKNXException
from xknx.telegram import GroupAddress, Telegram
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

import wirely
from wirely import Some, UInt16
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.generated import knx as contracts
from wirely.knx.connection_adapter import WirelyKNXIPInterface, create_xknx
from wirely.knx.connection_config import connection_config_from_settings
from wirely.knx.settings import check_connection, describe_connection
from wirely.knx.value_types import KNXValueType, get_value_type
from wirely.native.support import check_state_type
from wirely.generated.enums import KNXProtocol
from wirely.runtime.extensions import NativeTypes
from wirely.misc.repeated_errors import RepeatedError
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.util import verbose_print


# Delay before the first reconnection attempt, doubled after each failed attempt up to the maximum. Each delay is
# shortened randomly by up to a fifth, so that devices started together spread out.
RECONNECT_DELAY_MIN = 1.0
RECONNECT_DELAY_MAX = 60.0
# How long a read waits for a write or response on its address, in seconds.
READ_TIMEOUT = 2.0
# How many reads of a connection wait for an answer at the same time.
MAX_OUTSTANDING_READS = 2
# Intervals in seconds, chosen randomly from the range so that devices started together spread out.
# A ValueReceiver reads its address again when no value was seen on it for this long. This recovers from lost
# telegrams and from failed lines between devices that do not notice the failure, so they do not reconnect.
REREAD_INTERVAL = (60 * 60.0, 70 * 60.0)
# A ValueOptionProvider writes its value again when it has not sent it for this long. Like the response to a read, the
# write refreshes every listener, but it needs no read telegram. Shorter than REREAD_INTERVAL, so that receivers of a
# provided address do not read.
RESEND_INTERVAL = (45 * 60.0, 55 * 60.0)
# TODO: Make these intervals per-node options? Then reconsider sharing reads between the receivers of an address.

Payload = DPTArray | DPTBinary


def _protocol_name(option: Some[KNXProtocol] | None) -> str | None:
    if option is None:
        return None
    name = option.value.name
    if name is None:
        raise ValueError(f'Unsupported KNX protocol {int(option.value)}')
    return name


class _Codec:
    """Converts between Wirely values and KNX payloads of one datapoint type."""

    def __init__(self, knx_type: KNXValueType) -> None:
        self.knx_type = knx_type
        # Booleans are carried in the APCI as a single bit.
        self.transcoder = None if knx_type.name == 'binary' else DPTBase.parse_transcoder(knx_type.name)

    def encode(self, value: DynamicValue, native_types: NativeTypes) -> Payload:
        knx_value = self.knx_type.to_knx(value, native_types)
        if self.transcoder is None:
            return DPTBinary(1 if knx_value else 0)
        return self.transcoder.to_knx(knx_value)

    def decode(self, payload: Payload, native_types: NativeTypes) -> DynamicValue:
        """Decode a payload; raise ConversionError or CouldNotParseTelegram for an invalid one."""
        if self.transcoder is None:
            if not isinstance(payload, DPTBinary) or payload.value not in (0, 1):
                raise CouldNotParseTelegram('Invalid payload for a boolean', payload=str(payload))
            value: Any = payload.value == 1
        else:
            value = self.transcoder.from_knx(payload)
        return self.knx_type.from_knx(value, native_types)


class _Address:
    """The nodes of a connection using one group address."""

    def __init__(self, address: GroupAddress) -> None:
        self.address = address
        self.provider: ValueOptionProviderCreate | None = None
        self.value_receivers: list[ValueReceiverCreate] = []
        self.event_receivers: list[EventReceiverCreate] = []
        # Whether a read of this address is queued or waits for an answer.
        self.read_pending = False
        # Set when a value is seen on the address after a read was queued.
        self.answered = asyncio.Event()
        self.reread_timer: asyncio.TimerHandle | None = None


@dataclasses.dataclass
class _Entry:
    """An entry of the send queue."""
    # The telegram to send, or None to send the current value of provider.
    telegram: Telegram | None
    # The provider of a value or read response, None for events and reads.
    provider: 'ValueOptionProviderCreate | None' = None
    # The address of a read, which is not sent if a value was seen on it since the read was queued.
    read: _Address | None = None
    # Resolved with whether the telegram was sent, for reads.
    sent: 'asyncio.Future[bool] | None' = None


class ConnectionCreate(contracts.ConnectionCreate):
    def initialize(self, *, services: StateServices[Literal['bus']], host: Some[str] | None,
                   protocol: Some[KNXProtocol] | None, port: Some[UInt16] | None, unix_path: Some[str] | None,
                   ssh_target: Some[str] | None,
                   individual_address: Some[str] | None) -> contracts.ConnectionCreateResult:
        verbose_print('Create KNX connection', flush=True)

        def parameter(value: Some[str] | None) -> str | None:
            return None if value is None else value.value

        settings = check_connection(
            host=parameter(host),
            protocol=_protocol_name(protocol),
            port=None if port is None else port.value.value,
            unix_path=parameter(unix_path),
            ssh_target=parameter(ssh_target),
            individual_address=parameter(individual_address),
        )
        self.settings = settings
        self.description = describe_connection(settings)
        self.connection_config = connection_config_from_settings(settings)
        # Reads and devices are handled here instead of by xknx, and connection_task() connects.
        self.knx = create_xknx(self.connection_config, state_updater=False, connect_later=True)
        assert isinstance(self.knx.knxip_interface, WirelyKNXIPInterface)
        self.interface = self.knx.knxip_interface

        # Clients register themselves while being initialized.
        self.addresses: dict[int, _Address] = {}

        self.connected = False
        # Set on a disconnect until get_connected() has reported it.
        self.disconnect_pending = False
        # Set while not connected.
        self.lost = asyncio.Event()
        self.lost.set()
        self.errors = RepeatedError(f'KNX connection to {self.description}')
        # Counts the connections, so that a task can tell whether it still uses the same one.
        self.connection_number = 0

        # Telegrams to send, in order. Each provider has at most one value entry.
        self.send_queue: collections.deque[_Entry] = collections.deque()
        # Note: Use an event instead of a condition because setting an event does not require await
        self.send_event = asyncio.Event()
        # Addresses to read, each at most once.
        self.read_queue: collections.deque[_Address] = collections.deque()
        self.read_event = asyncio.Event()

        self.telegram_callback = self.knx.telegram_queue.register_telegram_received_cb(
            self.telegram_received, match_for_outgoing=True)
        self.knx.connection_manager.register_connection_state_changed_cb(self.connection_state_changed)
        self.registered = True
        return contracts.ConnectionCreateResult(bus=self)

    def address(self, group_address: UInt16) -> _Address:
        entry = self.addresses.get(group_address.value)
        if entry is None:
            entry = _Address(GroupAddress(group_address.value))
            self.addresses[group_address.value] = entry
        return entry

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        self.task = self.start_task(task_group, async_exit_stack, self.connection_task())
        self.sender = self.start_task(task_group, async_exit_stack, self.sender_task())
        self.readers = [self.start_task(task_group, async_exit_stack, self.reader_task())
                        for _ in range(MAX_OUTSTANDING_READS)]
        async_exit_stack.callback(self.unregister)

    async def connection_task(self) -> None:
        # xknx is started once, also while not connected; only the interface connects again.
        async with self.knx:
            delay = RECONNECT_DELAY_MIN
            while True:
                verbose_print(f'Connecting to KNX bus at {self.description}', flush=True)
                try:
                    await self.interface.connect()
                except (XKNXException, OSError) as e:
                    error = str(e) or type(e).__name__
                    if e.__cause__ is not None:
                        error += f': {e.__cause__}'
                    self.errors.report(error)
                    await self.interface.stop()
                else:
                    self.errors.end()
                    print(f'Connected to KNX bus at {self.description}', flush=True)
                    delay = RECONNECT_DELAY_MIN
                    await self.lost.wait()
                    self.errors.report('Connection lost')
                    await self.interface.stop()
                await asyncio.sleep(delay * random.uniform(0.8, 1.0))
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    def unregister(self) -> None:
        if not self.registered:
            return
        self.registered = False
        self.knx.telegram_queue.unregister_telegram_received_cb(self.telegram_callback)
        self.knx.connection_manager.unregister_connection_state_changed_cb(self.connection_state_changed)
        self.set_disconnected()

    def connection_state_changed(self, state: XknxConnectionState) -> None:
        if state == XknxConnectionState.CONNECTED:
            self.set_connected()
        elif state == XknxConnectionState.DISCONNECTED:
            self.set_disconnected()

    def set_connected(self) -> None:
        if self.connected:
            return
        self.connected = True
        self.lost.clear()
        self.connection_number += 1
        self.services.trigger('bus')
        # Send the current values for listeners that started while disconnected, then read the values that might
        # have changed meanwhile.
        # TODO: Should sending and reading after connecting be paced, for example with a random delay, so that many
        # devices starting at the same time (after a power failure) do not flood the bus?
        for entry in self.addresses.values():
            if entry.provider is not None:
                entry.provider.sent_payload = None
                self.enqueue_value(entry.provider)
        for entry in self.addresses.values():
            self.queue_read(entry)

    def set_disconnected(self) -> None:
        if not self.connected:
            return
        self.connected = False
        self.lost.set()
        self.disconnect_pending = True
        self.services.trigger('bus')
        # Telegrams are dropped instead of being sent when they might be outdated. Values are sent again after
        # connecting.
        for queued in self.send_queue:
            if queued.sent is not None:
                queued.sent.set_result(False)
        self.send_queue.clear()
        self.read_queue.clear()
        for entry in self.addresses.values():
            for value_receiver in entry.value_receivers:
                value_receiver.disconnected()
            entry.read_pending = False
            if entry.reread_timer is not None:
                entry.reread_timer.cancel()
                entry.reread_timer = None
            if entry.provider is not None:
                entry.provider.queued = False
                entry.provider.cancel_resend()

    # TODO: Make sure exceptions here are handled somehow?
    def telegram_received(self, telegram: Telegram) -> None:
        """Dispatch a telegram on the bus, including those sent by this program."""
        address = telegram.destination_address
        if not isinstance(address, GroupAddress):
            return
        entry = self.addresses.get(address.raw)
        if entry is None:
            return
        payload = telegram.payload
        if isinstance(payload, GroupValueRead):
            if entry.provider is not None:
                entry.provider.respond()
            return
        if not isinstance(payload, GroupValueWrite | GroupValueResponse):
            return
        entry.answered.set()
        self.arm_reread(entry)
        for value_receiver in entry.value_receivers:
            value_receiver.value_received(telegram, payload.value)
        # Only writes are events; read responses report the current value.
        if isinstance(payload, GroupValueWrite):
            for event_receiver in entry.event_receivers:
                event_receiver.event_received(telegram, payload.value)

    def enqueue(self, entry: _Entry) -> None:
        if not self.connected:
            if entry.sent is not None:
                entry.sent.set_result(False)
            return
        self.send_queue.append(entry)
        self.send_event.set()

    def enqueue_value(self, provider: 'ValueOptionProviderCreate', resend: bool = False) -> None:
        """Queue the current value of a provider unless it is already queued."""
        if not self.connected or provider.current_payload is None:
            return
        if resend:
            provider.resend_pending = True
        if provider.queued:
            return
        provider.queued = True
        self.enqueue(_Entry(None, provider))

    async def sender_task(self) -> None:
        while True:
            while len(self.send_queue) == 0:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.send_event.clear()
                await self.send_event.wait()
            entry = self.send_queue.popleft()
            if entry.read is not None and entry.read.answered.is_set():
                # The value arrived meanwhile, for example from a provider of this program after connecting.
                assert entry.sent is not None
                entry.sent.set_result(False)
                continue
            provider = entry.provider
            telegram = entry.telegram
            payload = None
            if telegram is None:
                assert provider is not None
                provider.queued = False
                # The value is read now, so that a provider that falls behind sends only its newest value.
                payload = provider.current_payload
                if payload is None or (payload == provider.sent_payload and not provider.resend_pending):
                    continue
                provider.resend_pending = False
                telegram = Telegram(destination_address=provider.address.address, payload=GroupValueWrite(payload))

            number = self.connection_number
            verbose_print(f'Sending {telegram}', flush=True)
            sent = False
            try:
                # This waits for the interface to confirm the telegram, which paces sending. The confirmation does
                # not prove that the telegram reached the bus: xknx ignores its confirm flag, and knxd and routing
                # confirm locally. Receivers of this program see the telegram after the confirmation, so it can
                # appear slightly out of order relative to telegrams of other devices.
                await self.knx.telegram_queue.process_telegram_outgoing(telegram)
                sent = True
            except XKNXException as e:
                # The telegram is dropped instead of being sent again when it might be outdated.
                verbose_print(f'Sending {telegram} failed: {e}', flush=True)
            if entry.sent is not None and not entry.sent.done():
                entry.sent.set_result(sent)
            if provider is not None and self.connected and self.connection_number == number:
                if payload is not None:
                    provider.sent_payload = payload if sent else None
                # Also after a failure, so that the value is sent again later.
                provider.arm_resend()

    def queue_read(self, entry: _Address) -> None:
        if not self.connected or entry.read_pending or len(entry.value_receivers) == 0:
            return
        if entry.reread_timer is not None:
            entry.reread_timer.cancel()
            entry.reread_timer = None
        entry.read_pending = True
        entry.answered.clear()
        self.read_queue.append(entry)
        self.read_event.set()

    def arm_reread(self, entry: _Address) -> None:
        if entry.reread_timer is not None:
            entry.reread_timer.cancel()
            entry.reread_timer = None
        if self.connected and len(entry.value_receivers) > 0:
            entry.reread_timer = asyncio.get_running_loop().call_later(
                random.uniform(*REREAD_INTERVAL), self.queue_read, entry)

    async def reader_task(self) -> None:
        """Read queued addresses one at a time; MAX_OUTSTANDING_READS of these run per connection."""
        while True:
            while len(self.read_queue) == 0:
                self.read_event.clear()
                await self.read_event.wait()
            entry = self.read_queue.popleft()
            number = self.connection_number
            sent: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self.enqueue(_Entry(Telegram(destination_address=entry.address, payload=GroupValueRead()),
                                read=entry, sent=sent))
            if await sent:
                try:
                    async with asyncio.timeout(READ_TIMEOUT):
                        await entry.answered.wait()
                except TimeoutError:
                    verbose_print(f'No answer to reading {entry.address}', flush=True)
            answered = entry.answered.is_set()
            if self.connection_number == number:
                entry.read_pending = False
                # An answer armed the timer already.
                if not answered:
                    self.arm_reread(entry)


class ConnectionGetConnected(contracts.ConnectionGetConnected):
    def run(self, *, services: ProcedureServices,
            bus: ConnectionCreate) -> contracts.ConnectionGetConnectedResult:
        if bus.disconnect_pending:
            # Report each disconnect for at least one step, even if the connection was restored since.
            bus.disconnect_pending = False
            services.trigger_self()
            return contracts.ConnectionGetConnectedResult(connected=False)
        return contracts.ConnectionGetConnectedResult(connected=bus.connected)


class ValueReceiverCreate(contracts.ValueReceiverCreate):
    def initialize(self, *, services: StateServices[Literal['state']], bus: ConnectionCreate, type: ValueType[Any],
                   group_address: UInt16, value_type_name: str) -> contracts.ValueReceiverCreateResult:
        self.knx_type = get_value_type(type, value_type_name)
        self.codec = _Codec(self.knx_type)
        # None while disconnected, until a value arrives after connecting.
        self.current_value: DynamicValue | None = None
        # Set on a disconnect until get_value() has reported it.
        self.disconnect_pending = False

        verbose_print('Create KNX ValueReceiver', flush=True)

        verbose_print(f'  Using group address: {group_address.value!r}', flush=True)
        verbose_print(f'  Using type: {type!r}', flush=True)

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.value_type = type
        self.value_type_knx = value_type_name

        self.address = bus.address(group_address)
        self.address.value_receivers.append(self)
        return contracts.ValueReceiverCreateResult(state=self)

    def value_received(self, telegram: Telegram, payload: Payload) -> None:
        try:
            value = self.codec.decode(payload, self.services.native_types)
        except (ConversionError, CouldNotParseTelegram) as error:
            print(f'Warning: KNX.ValueReceiver: Ignoring {telegram}: {error}', flush=True)
            return
        verbose_print(f'KNX.ValueReceiver: got value: {value!r}', flush=True)
        if value == self.current_value:
            return
        self.current_value = value

        self.services.trigger('state')

    def disconnected(self) -> None:
        # The value may change unnoticed while disconnected.
        self.current_value = None
        self.disconnect_pending = True
        self.services.trigger('state')


class ValueReceiverGetValue(contracts.ValueReceiverGetValue):
    def prepare(self, *, services: PreparationServices, state: 'ValueReceiverCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices,
            state: 'ValueReceiverCreate', type: ValueType[Any]) -> contracts.ValueReceiverGetValueResult:
        # TODO: Add checks whether everything is running?

        if state.disconnect_pending:
            # TODO: Reconsider reporting None for at least one step after each disconnect, even when a value is
            # available again by the next step. KNX.ValueReceiver and MQTT.ValueReceiver should behave the same.
            # Report each disconnect for at least one step, even if a value arrived since.
            state.disconnect_pending = False
            services.trigger_self()
            return contracts.ValueReceiverGetValueResult(output=None)

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
        self.codec = _Codec(self.knx_type)

        # The payload of the current value, None while the value is None.
        self.current_payload: Payload | None = None
        # The payload last sent while connected, None if unknown.
        self.sent_payload: Payload | None = None
        # Whether the send queue contains a value of this provider.
        self.queued = False
        # Whether the queued value is sent even if it was sent already.
        self.resend_pending = False
        self.resend_timer: asyncio.TimerHandle | None = None

        verbose_print('Create KNX ValueOptionProvider', flush=True)

        verbose_print(f'  Using group address: {group_address.value!r}', flush=True)
        verbose_print(f'  Using type: {type!r}', flush=True)

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.value_type = type
        self.value_type_knx = value_type_name

        self.address = bus.address(group_address)
        if self.address.provider is not None:
            raise ValueError(f'KNX connection already has a ValueOptionProvider for group address {self.address.address}')
        self.address.provider = self
        return contracts.ValueOptionProviderCreateResult(state=self)

    def respond(self) -> None:
        """Answer a read with the current value, if there is one."""
        if self.current_payload is None:
            return
        self.native_bus.enqueue(_Entry(
            Telegram(destination_address=self.address.address, payload=GroupValueResponse(self.current_payload)),
            self))

    def arm_resend(self) -> None:
        self.cancel_resend()
        if self.current_payload is not None:
            self.resend_timer = asyncio.get_running_loop().call_later(
                random.uniform(*RESEND_INTERVAL), self.native_bus.enqueue_value, self, True)

    def cancel_resend(self) -> None:
        if self.resend_timer is not None:
            self.resend_timer.cancel()
            self.resend_timer = None


class ValueOptionProviderSetValue(contracts.ValueOptionProviderSetValue):
    def prepare(self, *, services: PreparationServices, state: 'ValueOptionProviderCreate',
                type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'ValueOptionProviderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.ValueOptionProviderSetValueResult:
        # TODO: Add checks whether everything is running?

        verbose_print(f'Setting output {state.address.address} to {input}', flush=True)

        if input is None:
            # Behave as if there never was a value: reads are not answered, and the next value is sent.
            state.current_payload = None
            state.sent_payload = None
            state.cancel_resend()
            return contracts.ValueOptionProviderSetValueResult()

        # Note: If sending is too slow, a value set while another value of this provider is queued replaces it.
        # Values in between are not sent at all.
        state.current_payload = state.codec.encode(input.value, state.services.native_types)
        state.native_bus.enqueue_value(state)
        return contracts.ValueOptionProviderSetValueResult()


class EventReceiverCreate(contracts.EventReceiverCreate):
    def initialize(self, *, services: StateServices[Literal['state']], bus: ConnectionCreate, type: ValueType[Any],
                   group_address: UInt16, value_type_name: str) -> contracts.EventReceiverCreateResult:
        self.knx_type = get_value_type(type, value_type_name)
        self.codec = _Codec(self.knx_type)
        self.received_values: list[DynamicValue] = []

        self.value_type = type
        self.value_type_knx = value_type_name

        verbose_print('Create KNX EventReceiver', flush=True)

        verbose_print(f'  Using group address: {group_address.value!r}', flush=True)
        verbose_print(f'  Using type: {self.value_type!r} / {self.value_type_knx}', flush=True)

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        # Outgoing telegrams are included, so that an EventSender for the same address in this program is received
        # too.
        self.address = bus.address(group_address)
        self.address.event_receivers.append(self)
        return contracts.EventReceiverCreateResult(state=self)

    def event_received(self, telegram: Telegram, payload: Payload) -> None:
        try:
            value = self.codec.decode(payload, self.services.native_types)
        except (ConversionError, CouldNotParseTelegram) as error:
            print(f'Warning: KNX.EventReceiver: Ignoring {telegram}: {error}', flush=True)
            return
        verbose_print(f'KNX.EventReceiver: got value: {value!r}', flush=True)

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
        self.codec = _Codec(self.knx_type)

        verbose_print('Create KNX EventSender', flush=True)

        self.value_type = type
        self.value_type_knx = value_type_name

        self.native_bus = bus
        verbose_print(f'  Using native bus object: {self.native_bus}', flush=True)

        self.address = GroupAddress(group_address.value)
        return contracts.EventSenderCreateResult(state=self)


class EventSenderSendValue(contracts.EventSenderSendValue):
    def prepare(self, *, services: PreparationServices, state: 'EventSenderCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'EventSenderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.EventSenderSendValueResult:
        # TODO: Add checks whether everything is running?

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if input is None:
            return contracts.EventSenderSendValueResult()
        payload = state.codec.encode(input.value, state.services.native_types)

        verbose_print(f'Sending event {payload} to {state.address}, {len(state.native_bus.send_queue)} telegrams queued', flush=True)

        # Note: If sending is too slow, there might be multiple events queued up.
        # TODO: Provide a notification in this case?
        state.native_bus.enqueue(_Entry(Telegram(destination_address=state.address, payload=GroupValueWrite(payload))))

        # This makes sure that the value is sent again if the input does not change
        services.trigger_self()
        return contracts.EventSenderSendValueResult()


Bus = ConnectionCreate  # Used as a resource type.
ValueReceiverState = ValueReceiverCreate  # Used as a resource type.
ValueOptionProviderState = ValueOptionProviderCreate  # Used as a resource type.
EventReceiverState = EventReceiverCreate  # Used as a resource type.
EventSenderState = EventSenderCreate  # Used as a resource type.
