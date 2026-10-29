import asyncio
import collections
import sys
import json
import contextlib
import random
from typing import Any, Literal

import aiohttp

import wirely
import wirely.two_n.client
from wirely import Int32, Some, UInt16
from wirely.generated import two_n as contracts
from wirely.misc.repeated_errors import RepeatedError
from wirely.runtime.native_api import (
    CommandFailed, InvalidCommandArgument, ProcedureServices, StateServices, control_command,
)
from wirely.generated.structs import TwoNCredentials
from wirely.two_n.client import TwoNAPIError, TwoNError
from wirely.two_n.settings import check_code
from wirely.util import verbose_print


# Delay before the first reconnection attempt, doubled after each failed attempt up to the maximum. Each delay is
# shortened randomly by up to a fifth, so that devices started together spread out.
RECONNECT_DELAY_MIN = 1.0
RECONNECT_DELAY_MAX = 60.0

# All states are read again after connecting and then about this often (in seconds), since an event might have been
# lost; a state that changed without an event is logged.
REREAD_INTERVAL = (60 * 60.0, 70 * 60.0)

# The number of events a KeyEvent or CodeEnteredEvent keeps until they are delivered, one per step; when more arrive,
# the oldest one is dropped.
MAX_PENDING_EVENTS = 10

# Errors of a request that leave the connection unusable or show that the device does not support a request.
REQUEST_ERRORS = (aiohttp.ClientError, TimeoutError, TwoNError)


class ConnectionCreate(contracts.ConnectionCreate):
    def initialize(self, *, services: StateServices[Literal['device']], host: str, port: UInt16,
                   tlsa: tuple[str, ...], credentials: TwoNCredentials) -> contracts.ConnectionCreateResult:
        verbose_print('Create TwoN Connection', flush=True)

        hostname = host
        verbose_print(f'  Connecting to: {hostname!r}:{port.value}', flush=True)
        verbose_print(f'  Using TLSA: {tlsa!r}', flush=True)

        self.client = wirely.two_n.client.Client(hostname=hostname, port=port.value, tlsa=tlsa, http_api_username_password=(credentials.username, credentials.password))
        self.log_receiver = wirely.two_n.client.LogReceiver(self.client)
        self.description = f'{hostname}:{port.value}'

        # Clients register themselves while being initialized.
        self.io_states: list[IOStateCreate] = []
        self.switch_states: list[SwitchStateCreate] = []
        # Notified when connecting and disconnecting.
        self.listeners: list[ConnectionStateCreate | IOStateCreate | SwitchStateCreate | OutputControlCreate] = []

        self.connected = False
        # Set on a disconnect until get_connected() has reported it.
        self.disconnect_pending = False
        self.errors = RepeatedError(f'TwoN connection to {self.description}')
        self.io_errors = RepeatedError(f'Reading TwoN ports from {self.description}')
        self.switch_errors = RepeatedError(f'Reading TwoN switches from {self.description}')
        # Counts the connections, so that a task can tell whether it still uses the same one.
        self.connection_number = 0
        # When to read all states again (event loop time), None while disconnected.
        self.reread_at: float | None = None
        # Set after reading the states again, until the next pull has delivered the events queued meanwhile.
        self.reread_pending = False
        return contracts.ConnectionCreateResult(device=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        self.task = self.start_task(task_group, async_exit_stack, self.connection_task())
        async_exit_stack.callback(self.set_disconnected)

    async def connection_task(self) -> None:
        delay = RECONNECT_DELAY_MIN
        while True:
            verbose_print(f'Connecting to TwoN device at {self.description}', flush=True)
            number = self.connection_number
            try:
                async with self.client:
                    try:
                        await self.log_receiver.receive(self.subscribed, self.between_pulls)
                    finally:
                        self.set_disconnected()
            except REQUEST_ERRORS as e:
                self.errors.report(str(e) or type(e).__name__)
            if self.connection_number != number:
                delay = RECONNECT_DELAY_MIN
            await asyncio.sleep(delay * random.uniform(0.8, 1.0))
            delay = min(delay * 2, RECONNECT_DELAY_MAX)

    async def subscribed(self) -> None:
        """Read the current states after subscribing, then report the connection."""
        await self.read_states(reread=False)
        self.errors.end()
        print(f'Connected to TwoN device at {self.description}', flush=True)
        self.set_connected()

    def read_states_nodes(self) -> 'list[_ReadState]':
        return [*self.io_states, *self.switch_states]

    async def read_states(self, *, reread: bool) -> None:
        await _read_states(self.client, '/api/io/status', 'ports', 'port', self.io_states, self.io_errors, reread)
        await _read_states(self.client, '/api/switch/status', 'switches', 'switch', self.switch_states,
                           self.switch_errors, reread)

    async def between_pulls(self) -> bool:
        """Read all states again when due; return whether the next pull should return immediately.

        Events queued while reading come with the next pull, so a state the read finds changed is only logged as
        changed without an event if that pull has no event for it.
        """
        if self.reread_pending:
            self.reread_pending = False
            for state in self.read_states_nodes():
                state.check_reread()
        loop = asyncio.get_running_loop()
        if self.reread_at is None or loop.time() < self.reread_at:
            return False
        self.reread_at = loop.time() + random.uniform(*REREAD_INTERVAL)
        verbose_print(f'Reading the states of TwoN device at {self.description} again', flush=True)
        await self.read_states(reread=True)
        self.reread_pending = True
        return True

    @control_command(0)
    def reread_command(self, argument: object) -> dict[int, object]:
        """Read all states again after the current pull, and restart the interval."""
        if argument is not None and argument != {}:
            raise InvalidCommandArgument('Expected an empty map')
        if not self.connected:
            # Reason 1: not connected; the states are read after connecting.
            raise CommandFailed('Not connected', reason=1)
        self.reread_at = asyncio.get_running_loop().time()
        return {}

    def set_connected(self) -> None:
        if self.connected:
            return
        self.connected = True
        self.reread_at = asyncio.get_running_loop().time() + random.uniform(*REREAD_INTERVAL)
        self.connection_number += 1
        self.services.trigger('device')
        for listener in self.listeners:
            listener.connected()

    def set_disconnected(self) -> None:
        if not self.connected:
            return
        self.connected = False
        self.reread_at = None
        self.reread_pending = False
        for state in self.read_states_nodes():
            state.reread_check = None
        self.disconnect_pending = True
        self.services.trigger('device')
        for listener in self.listeners:
            listener.disconnected()


class ConnectionGetConnected(contracts.ConnectionGetConnected):
    def run(self, *, services: ProcedureServices,
            device: ConnectionCreate) -> contracts.ConnectionGetConnectedResult:
        if device.disconnect_pending:
            # Report each disconnect for at least one step, even if the connection was restored since.
            device.disconnect_pending = False
            services.trigger_self()
            return contracts.ConnectionGetConnectedResult(connected=False)
        return contracts.ConnectionGetConnectedResult(connected=device.connected)


class ConnectionStateCreate(contracts.ConnectionStateCreate):
    def initialize(self, *, services: StateServices[Literal['state']],
                   device: ConnectionCreate) -> contracts.ConnectionStateCreateResult:
        self.native_device = device
        # Set on a disconnect until get_value() has reported it.
        self.disconnect_pending = False
        device.listeners.append(self)
        return contracts.ConnectionStateCreateResult(state=self)

    def connected(self) -> None:
        self.services.trigger('state')

    def disconnected(self) -> None:
        self.disconnect_pending = True
        self.services.trigger('state')


class ConnectionStateGetValue(contracts.ConnectionStateGetValue):
    def run(self, *, services: ProcedureServices,
            state: ConnectionStateCreate) -> contracts.ConnectionStateGetValueResult:
        if state.disconnect_pending:
            # Report each disconnect for at least one step, even if the connection was restored since.
            state.disconnect_pending = False
            services.trigger_self()
            return contracts.ConnectionStateGetValueResult(output=False)
        return contracts.ConnectionStateGetValueResult(output=state.native_device.connected)


class KeyEventCreate(contracts.KeyEventCreate):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   key: str) -> contracts.KeyEventCreateResult:
        self.key = key

        self.pending_values: collections.deque[bool] = collections.deque()

        verbose_print('Create TwoN KeyEvent', flush=True)

        if wirely.util.getEnvironmentBool('WIRELY_TWON_TRACE_KEYS', False):
            print(f'  Key: {self.key!r}', flush=True)

        self.native_device = device
        verbose_print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['KeyPressed', 'KeyReleased'], self.handler)
        return contracts.KeyEventCreateResult(state=self)

    def handler(self, event: dict[str, Any]) -> None:
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] == 'KeyPressed':
            new_value = True
        elif event['event'] == 'KeyReleased':
            new_value = False
        else:
            print(f'Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['key'] != self.key:
            return

        if len(self.pending_values) == MAX_PENDING_EVENTS:
            dropped = self.pending_values.popleft()
            print(f'Warning: TwoN.KeyEvent: Too many pending events, dropping the oldest one ({dropped})', flush=True)

        # print(f'New value: {new_value}')
        self.services.trigger('state')
        self.pending_values.append(new_value)


class KeyEventGetValue(contracts.KeyEventGetValue):
    def run(self, *, services: ProcedureServices, state: 'KeyEventCreate') -> contracts.KeyEventGetValueResult:
        if len(state.pending_values) == 0:
            return contracts.KeyEventGetValueResult(output=None)
        value = state.pending_values.popleft()
        # Trigger to deliver the next event or to set value to None again
        services.trigger_self()
        return contracts.KeyEventGetValueResult(output=wirely.Some(value))


class CodeEnteredEventCreate(contracts.CodeEnteredEventCreate):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   code: str) -> contracts.CodeEnteredEventCreateResult:
        check_code(code)
        self.code = code

        self.pending_count = 0

        verbose_print('Create TwoN CodeEnteredEvent', flush=True)

        if wirely.util.getEnvironmentBool('WIRELY_TWON_TRACE_KEYS', False):
            print(f'  Code: {self.code!r}', flush=True)

        self.native_device = device
        verbose_print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['CodeEntered'], self.handler)
        return contracts.CodeEnteredEventCreateResult(state=self)

    def handler(self, event: dict[str, Any]) -> None:
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] != 'CodeEntered':
            print(f'Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['code'] != self.code:
            return

        if self.pending_count == MAX_PENDING_EVENTS:
            print('Warning: TwoN.CodeEnteredEvent: Too many pending events, dropping the oldest one', flush=True)
        else:
            self.pending_count += 1

        # print(f'New value: {new_value}')
        self.services.trigger('state')


class CodeEnteredEventGetValue(contracts.CodeEnteredEventGetValue):
    def run(self, *, services: ProcedureServices,
            state: 'CodeEnteredEventCreate') -> contracts.CodeEnteredEventGetValueResult:
        if state.pending_count == 0:
            return contracts.CodeEnteredEventGetValueResult(output=None)
        state.pending_count -= 1
        # Trigger to deliver the next event or to set value to None again
        services.trigger_self()
        return contracts.CodeEnteredEventGetValueResult(output=wirely.Some(wirely.Unit()))


async def _read_states(client: wirely.two_n.client.Client, url: str, list_key: str, id_key: str,
                       nodes: 'list[IOStateCreate] | list[SwitchStateCreate]', errors: RepeatedError,
                       reread: bool) -> None:
    """Read the states of all ports or switches with one request and pass each node its entry.

    An error the device reports, or an invalid response, leaves the nodes None; other errors of the connection
    propagate.
    """
    if len(nodes) == 0:
        return
    try:
        response = await client.json_request('GET', url)
    except TwoNAPIError as e:
        errors.report(str(e))
        return
    try:
        listed = response[list_key]
        if not isinstance(listed, list) or not all(isinstance(entry, dict) for entry in listed):
            raise TypeError(f'{list_key} is not a list of objects')
        entries = {entry[id_key]: entry for entry in listed}
    except (KeyError, TypeError):
        errors.report(f'Got invalid response {response!r}')
        return
    errors.end()
    for node in nodes:
        node.set_status(entries.get(node.status_id), reread=reread)


class _ReadState:
    """The handling of the states IOState and SwitchState read from the device."""

    current_value: bool | None
    errors: RepeatedError
    # Describes the port or switch in warnings.
    subject: str
    # Counts the events, so that a state read again can tell whether an event explains a change.
    event_count: int
    # After reading the state again: the event count and value before the read, and the value read; until the events
    # queued meanwhile are handled.
    reread_check: tuple[int, bool | None, bool] | None

    def set_value(self, value: bool) -> None:
        raise NotImplementedError

    def set_unknown(self) -> None:
        raise NotImplementedError

    def set_read_value(self, value: bool | None, error: str | None, *, reread: bool) -> None:
        if value is None:
            # The device might not have this port or switch; the other nodes keep working.
            assert error is not None
            self.errors.report(error)
            self.set_unknown()
            return
        self.errors.end()
        if reread:
            self.reread_check = (self.event_count, self.current_value, value)
        self.set_value(value)

    def check_reread(self) -> None:
        """Log a state read again that changed without an event since; called once the queued events are handled."""
        if self.reread_check is None:
            return
        event_count, previous, value = self.reread_check
        self.reread_check = None
        if event_count == self.event_count and previous is not None and previous != value:
            print(f'Warning: {self.subject} changed to {value} without an event', flush=True)


class IOStateCreate(contracts.IOStateCreate, _ReadState):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   port: str) -> contracts.IOStateCreateResult:
        self.port = port
        # The value of the port's entry in /api/io/status.
        self.status_id = port

        # None while disconnected, until the state has been read after connecting.
        self.current_value: bool | None = None
        # Set on a disconnect until get_value() has reported it.
        self.disconnect_pending = False
        self.errors = RepeatedError(f'Reading TwoN port {port!r}')
        self.subject = f'TwoN.IOState: Port {port!r}'
        self.event_count = 0
        self.reread_check = None

        verbose_print('Create TwoN IOState', flush=True)

        verbose_print(f'  Port: {self.port!r}', flush=True)

        self.native_device = device
        verbose_print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['InputChanged', 'OutputChanged'], self.handler)
        device.io_states.append(self)
        device.listeners.append(self)
        return contracts.IOStateCreateResult(state=self)

    def set_status(self, entry: dict[str, Any] | None, *, reread: bool) -> None:
        """Set the state from the port's entry in /api/io/status, read after subscribing and again later."""
        if entry is None:
            self.set_read_value(None, 'The device has no such port', reread=reread)
            return
        state = entry.get('state')
        if type(state) != int or state not in (0, 1):
            self.set_read_value(None, f'Got invalid state {state!r}', reread=reread)
            return
        self.set_read_value(bool(state), None, reread=reread)

    def set_value(self, value: bool) -> None:
        if value == self.current_value:
            return
        self.current_value = value
        self.services.trigger('state')

    def set_unknown(self) -> None:
        if self.current_value is None:
            return
        self.current_value = None
        self.services.trigger('state')

    def connected(self) -> None:
        pass

    def disconnected(self) -> None:
        # The state may change unnoticed while disconnected.
        self.current_value = None
        self.disconnect_pending = True
        self.services.trigger('state')

    def handler(self, event: dict[str, Any]) -> None:
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] != 'InputChanged' and event['event'] != 'OutputChanged':
            print(f'Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['port'] != self.port:
            return

        new_value = event['params']['state']
        if type(new_value) != bool:
            print(f'Warning: Got unexpected non-boolean value: {new_value!r}', flush=True)
            return

        # print(f'New value: {new_value}')
        self.event_count += 1
        self.set_value(new_value)


class IOStateGetValue(contracts.IOStateGetValue):
    def run(self, *, services: ProcedureServices, state: 'IOStateCreate') -> contracts.IOStateGetValueResult:
        if state.disconnect_pending:
            # Report each disconnect for at least one step, even if the state was read again since.
            state.disconnect_pending = False
            services.trigger_self()
            return contracts.IOStateGetValueResult(output=None)
        value = state.current_value
        if value is None:
            return contracts.IOStateGetValueResult(output=None)
        return contracts.IOStateGetValueResult(output=Some(value))


class SwitchStateCreate(contracts.SwitchStateCreate, _ReadState):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   switch_number: Int32) -> contracts.SwitchStateCreateResult:
        self.switch_number = switch_number.value
        # The value of the switch's entry in /api/switch/status.
        self.status_id = self.switch_number

        # None while disconnected, until the state has been read after connecting.
        self.current_value: bool | None = None
        # Set on a disconnect until get_value() has reported it.
        self.disconnect_pending = False
        self.errors = RepeatedError(f'Reading TwoN switch {self.switch_number}')
        self.subject = f'TwoN.SwitchState: Switch {self.switch_number}'
        self.event_count = 0
        self.reread_check = None

        verbose_print('Create TwoN SwitchState', flush=True)

        verbose_print(f'  SwitchNumber: {self.switch_number!r}', flush=True)

        self.native_device = device
        verbose_print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['SwitchStateChanged'], self.handler)
        device.switch_states.append(self)
        device.listeners.append(self)
        return contracts.SwitchStateCreateResult(state=self)

    def set_status(self, entry: dict[str, Any] | None, *, reread: bool) -> None:
        """Set the state from the switch's entry in /api/switch/status, read after subscribing and again later."""
        if entry is None:
            self.set_read_value(None, 'The device has no such switch', reread=reread)
            return
        active = entry.get('active')
        if type(active) != bool:
            self.set_read_value(None, f'Got invalid state {active!r}', reread=reread)
            return
        self.set_read_value(active, None, reread=reread)

    def set_value(self, value: bool) -> None:
        if value == self.current_value:
            return
        self.current_value = value
        self.services.trigger('state')

    def set_unknown(self) -> None:
        if self.current_value is None:
            return
        self.current_value = None
        self.services.trigger('state')

    def connected(self) -> None:
        pass

    def disconnected(self) -> None:
        # The state may change unnoticed while disconnected.
        self.current_value = None
        self.disconnect_pending = True
        self.services.trigger('state')

    def handler(self, event: dict[str, Any]) -> None:
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] != 'SwitchStateChanged':
            print(f'Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['switch'] != self.switch_number:
            return

        new_value = event['params']['state']
        if type(new_value) != bool:
            print(f'Warning: Got unexpected non-boolean value: {new_value!r}', flush=True)
            return

        # print(f'New value: {new_value}')
        self.event_count += 1
        self.set_value(new_value)


class SwitchStateGetValue(contracts.SwitchStateGetValue):
    def run(self, *, services: ProcedureServices, state: 'SwitchStateCreate') -> contracts.SwitchStateGetValueResult:
        if state.disconnect_pending:
            # Report each disconnect for at least one step, even if the state was read again since.
            state.disconnect_pending = False
            services.trigger_self()
            return contracts.SwitchStateGetValueResult(output=None)
        value = state.current_value
        if value is None:
            return contracts.SwitchStateGetValueResult(output=None)
        return contracts.SwitchStateGetValueResult(output=Some(value))


class OutputControlCreate(contracts.OutputControlCreate):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   port: str) -> contracts.OutputControlCreateResult:
        self.port = port

        # Task is needed for sending out values
        self.task: asyncio.Task | None = None
        self.value_to_send: bool | None = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        verbose_print('Create TwoN OutputControl', flush=True)

        verbose_print(f'  Port: {self.port!r}', flush=True)

        self.native_device = device
        verbose_print(f'  Using native device object: {self.native_device}', flush=True)
        self.errors = RepeatedError(f'Setting TwoN port {port!r}')
        device.listeners.append(self)
        return contracts.OutputControlCreateResult(state=self)

    def connected(self) -> None:
        pass

    def disconnected(self) -> None:
        # Values are dropped instead of being sent when they might be outdated.
        self.value_to_send = None

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

            if value == False:
                action = 'off'
            elif value == True:
                action = 'on'
            else:
                print(f'Warning: TwoN.OutputControl: Unexpected value: {value}', flush=True)
                continue

            if not self.native_device.connected:
                verbose_print(f'Dropping {value} for port {self.port} while disconnected', flush=True)
                continue

            verbose_print(f"Sending {value} to port {self.port}", flush=True)
            try:
                # response = await self.native_device.client.json_request("GET", "/api/io/ctrl", params={
                # response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={}, params={
                response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={
                    'port': self.port,
                    'action': action,
                })
            except REQUEST_ERRORS as e:
                # The value is dropped instead of being sent again when it might be outdated.
                self.errors.report(str(e) or type(e).__name__)
                continue
            self.errors.end()
            verbose_print(f"Sending {value} to port {self.port} done with result {response}", flush=True)


class OutputControlSendValue(contracts.OutputControlSendValue):
    def run(self, *, services: ProcedureServices, state: 'OutputControlCreate',
            input: Some[bool] | None) -> contracts.OutputControlSendValueResult:
        if input is None:
            return contracts.OutputControlSendValueResult()
        value = input.value

        if state.value_to_send is not None:
            print('Warning: TwoN.OutputControl: Got new event to send while old event is pending', flush=True)
        state.value_to_send = value
        state.event.set()

        # This makes sure that the value is sent again if the input does not change
        services.trigger_self()
        return contracts.OutputControlSendValueResult()


Device = ConnectionCreate  # Used as a resource type.
KeyEventState = KeyEventCreate  # Used as a resource type.
CodeEnteredEventState = CodeEnteredEventCreate  # Used as a resource type.
IOStateState = IOStateCreate  # Used as a resource type.
SwitchStateState = SwitchStateCreate  # Used as a resource type.
OutputControlState = OutputControlCreate  # Used as a resource type.
ConnectionStateState = ConnectionStateCreate  # Used as a resource type.
