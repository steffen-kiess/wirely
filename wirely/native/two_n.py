import asyncio
import sys
import json
import contextlib
from typing import Any, Literal

import wirely
from wirely import Int32, Some, UInt16
from wirely.generated import two_n as contracts
from wirely.runtime.native_api import ProcedureServices, StateServices
from wirely.generated.structs import TwoNCredentials
from wirely.util import verbose_print


class ConnectionCreate(contracts.ConnectionCreate):
    def initialize(self, *, services: StateServices[Literal['device']], host: str, port: UInt16,
                   tlsa: tuple[str, ...], credentials: TwoNCredentials) -> contracts.ConnectionCreateResult:
        import wirely.two_n.client

        verbose_print('Create TwoN Connection', flush=True)

        hostname = host
        verbose_print(f'  Connecting to: {hostname!r}:{port.value}', flush=True)
        verbose_print(f'  Using TLSA: {tlsa!r}', flush=True)

        self.client = wirely.two_n.client.Client(hostname=hostname, port=port.value, tlsa=tlsa, http_api_username_password=(credentials.username, credentials.password))
        self.log_receiver = wirely.two_n.client.LogReceiver(self.client)
        return contracts.ConnectionCreateResult(device=self)

    async def start_tasks_async(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        verbose_print('Starting TwoN client', flush=True)
        await async_exit_stack.enter_async_context(self.client)
        verbose_print('Starting log receiver', flush=True)
        # TODO: Should this try to unsubscribe on shutdown and if yes, how long should it wait?
        await self.log_receiver.start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)
        verbose_print('Starting TwoN client done', flush=True)


class KeyEventCreate(contracts.KeyEventCreate):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   key: str) -> contracts.KeyEventCreateResult:
        self.key = key

        self.received_value: bool | None = None

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

        # Note: This will override older events which generally should not be a problem.
        if self.received_value is not None:
            print(f'Warning: TwoN.KeyEvent: Got event while old event is pending: {self.received_value} -> {new_value}', flush=True)

        # print(f'New value: {new_value}')
        self.services.trigger('state')
        self.received_value = new_value


class KeyEventGetValue(contracts.KeyEventGetValue):
    def run(self, *, services: ProcedureServices, state: 'KeyEventCreate') -> contracts.KeyEventGetValueResult:
        value = state.received_value
        state.received_value = None

        if value is None:
            return contracts.KeyEventGetValueResult(output=None)
        # Trigger to set value to None again
        services.trigger_self()
        return contracts.KeyEventGetValueResult(output=wirely.Some(value))


class CodeEnteredEventCreate(contracts.CodeEnteredEventCreate):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   code: str) -> contracts.CodeEnteredEventCreateResult:
        self.code = code

        self.pending = False

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

        # Note: This will override older events which generally should not be a problem.
        if self.pending:
            print('Warning: TwoN.CodeEnteredEvent: Got event while old event is pending', flush=True)

        # print(f'New value: {new_value}')
        self.services.trigger('state')
        self.pending = True


class CodeEnteredEventGetValue(contracts.CodeEnteredEventGetValue):
    def run(self, *, services: ProcedureServices,
            state: 'CodeEnteredEventCreate') -> contracts.CodeEnteredEventGetValueResult:
        value = state.pending
        state.pending = False

        if not value:
            return contracts.CodeEnteredEventGetValueResult(output=None)
        # Trigger to set value to None again
        services.trigger_self()
        return contracts.CodeEnteredEventGetValueResult(output=wirely.Some(wirely.Unit()))


class IOStateCreate(contracts.IOStateCreate):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   port: str) -> contracts.IOStateCreateResult:
        self.port = port

        # TODO: What value should this provide while there is no connection?
        self.current_value: bool | None = None

        # TODO: Read current value on start-up in a race-free way

        verbose_print('Create TwoN IOState', flush=True)

        verbose_print(f'  Port: {self.port!r}', flush=True)

        self.native_device = device
        verbose_print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['InputChanged', 'OutputChanged'], self.handler)
        return contracts.IOStateCreateResult(state=self)

    async def start_tasks_async(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        if not self.native_device.log_receiver.is_subscribed:
            raise Exception('Attempting to start up IOStateCreate without event subscription')

        response = await self.native_device.client.json_request("GET", "/api/io/status", params={
            'port': self.port,
        })
        # print('RESPONSE', response)
        ports = response['ports']
        if len(ports) != 1:
            raise Exception('TwoN.IOStateCreate: len(ports) != 1')
        port = ports[0]
        if port['port'] != self.port:
            raise Exception("TwoN.IOStateCreate: port['port'] != self.port")
        state = port['state']
        if type(state) != int:
            raise Exception('TwoN.IOStateCreate: type(state) != int')
        if state != 0 and state != 1:
            raise Exception('TwoN.IOStateCreate: state != 0 and state != 1')
        self.current_value = bool(state)

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
        self.services.trigger('state')
        self.current_value = new_value


class IOStateGetValue(contracts.IOStateGetValue):
    def run(self, *, services: ProcedureServices, state: 'IOStateCreate') -> contracts.IOStateGetValueResult:
        value = state.current_value
        if value is None:
            raise Exception('TwoN.IOStateCreate: get_value() called while current_value is None')

        return contracts.IOStateGetValueResult(output=value)


class SwitchStateCreate(contracts.SwitchStateCreate):
    def initialize(self, *, services: StateServices[Literal['state']], device: ConnectionCreate,
                   switch_number: Int32) -> contracts.SwitchStateCreateResult:
        self.switch_number = switch_number.value

        # TODO: What value should this provide while there is no connection?
        self.current_value: bool | None = None

        # TODO: Read current value on start-up in a race-free way

        verbose_print('Create TwoN SwitchState', flush=True)

        verbose_print(f'  SwitchNumber: {self.switch_number!r}', flush=True)

        self.native_device = device
        verbose_print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['SwitchStateChanged'], self.handler)
        return contracts.SwitchStateCreateResult(state=self)

    async def start_tasks_async(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        if not self.native_device.log_receiver.is_subscribed:
            raise Exception('Attempting to start up SwitchStateCreate without event subscription')

        response = await self.native_device.client.json_request("GET", "/api/switch/status", params={
            'switch': self.switch_number,
        })
        # print('RESPONSE', response)
        switches = response['switches']
        if len(switches) != 1:
            raise Exception('TwoN.SwitchStateCreate: len(switches) != 1')
        switch = switches[0]
        if switch['switch'] != self.switch_number:
            raise Exception("TwoN.SwitchStateCreate: switch['switch'] != self.switch_number")
        active = switch['active']
        if type(active) != bool:
            raise Exception('TwoN.SwitchStateCreate: type(active) != bool')
        self.current_value = active

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
        self.services.trigger('state')
        self.current_value = new_value


class SwitchStateGetValue(contracts.SwitchStateGetValue):
    def run(self, *, services: ProcedureServices, state: 'SwitchStateCreate') -> contracts.SwitchStateGetValueResult:
        value = state.current_value
        if value is None:
            raise Exception('TwoN.SwitchStateCreate: get_value() called while current_value is None')

        return contracts.SwitchStateGetValueResult(output=value)


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
        return contracts.OutputControlCreateResult(state=self)

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

            verbose_print(f"Sending {value} to port {self.port}", flush=True)
            # response = await self.native_device.client.json_request("GET", "/api/io/ctrl", params={
            # response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={}, params={
            response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={
                'port': self.port,
                'action': action,
            })
            verbose_print(f"Sending {value} to port {self.port} done with result {response}", flush=True)


class OutputControlSendValue(contracts.OutputControlSendValue):
    def run(self, *, services: ProcedureServices, state: 'OutputControlCreate',
            input: Some[bool] | None) -> contracts.OutputControlSendValueResult:
        # TODO: Add checks whether everything is running?

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
