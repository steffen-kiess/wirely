import asyncio
import sys
import json
import contextlib

import wirely
from wirely.native import native_node_type
from wirely.program import RunStage
from wirely.runtime.contexts import NativeState

native_node_types = {}


@native_node_type(native_node_types, has_native_state=True)
class Connection(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('device', self)

        import wirely.two_n.client

        print('Create TwoN Connection', flush=True)

        hostname = context.values['Host']
        port = context.values['Port']
        tlsa = context.values['TLSA']
        credentials = context.values['credentials']
        print(f'  Connecting to: {hostname!r}:{port}', flush=True)
        print(f'  Using TLSA: {tlsa!r}', flush=True)

        self.client = wirely.two_n.client.Client(hostname=hostname, port=port, tlsa=tlsa, http_api_username_password=(credentials['username'], credentials['password']))
        self.log_receiver = wirely.two_n.client.LogReceiver(self.client)

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        print('Starting TwoN client', flush=True)
        await async_exit_stack.enter_async_context(self.client)
        print('Starting log receiver', flush=True)
        # TODO: Should this try to unsubscribe on shutdown and if yes, how long should it wait?
        await self.log_receiver.start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)
        print('Starting TwoN client done', flush=True)


@native_node_type(native_node_types, has_native_state=True)
class KeyEvent(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

        self.received_value = None

        print('Create TwoN KeyEvent', flush=True)

        if wirely.util.getEnvironmentBool('WIRELY_TWON_TRACE_KEYS', False):
            print(f"  Key: {self.values['Key']!r}", flush=True)

        self.native_device = context.inputs['Device']
        print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['KeyPressed', 'KeyReleased'], self.handler)

    def handler(self, event):
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] == 'KeyPressed':
            new_value = True
        elif event['event'] == 'KeyReleased':
            new_value = False
        else:
            print('Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['key'] != self.values['Key']:
            return

        # Note: This will override older events which generally should not be a problem.
        if self.received_value is not None:
            print(f'Warning: TwoN.KeyEvent: Got event while old event is pending: {self.received_value} -> {new_value}', flush=True)

        # print(f'New value: {new_value}')
        self.trigger('state')
        self.received_value = new_value

    @staticmethod
    def get_value(*, context):
        state = context.inputs['state']
        value = state.received_value
        state.received_value = None

        if value is None:
            context.set_output('output', None)
        else:
            context.set_output('output', wirely.Some(value))
            # Trigger to set value to None again
            context.trigger_self()


@native_node_type(native_node_types, has_native_state=True)
class CodeEnteredEvent(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

        self.pending = False

        print('Create TwoN CodeEnteredEvent', flush=True)

        if wirely.util.getEnvironmentBool('WIRELY_TWON_TRACE_KEYS', False):
            print(f"  Code: {self.values['Code']!r}", flush=True)

        self.native_device = context.inputs['Device']
        print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['CodeEntered'], self.handler)

    def handler(self, event):
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] != 'CodeEntered':
            print('Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['code'] != self.values['Code']:
            return

        # Note: This will override older events which generally should not be a problem.
        if self.pending:
            print('Warning: TwoN.CodeEnteredEvent: Got event while old event is pending', flush=True)

        # print(f'New value: {new_value}')
        self.trigger('state')
        self.pending = True

    @staticmethod
    def get_value(*, context):
        state = context.inputs['state']
        value = state.pending
        state.pending = False

        if not value:
            context.set_output('output', None)
        else:
            context.set_output('output', wirely.Some(wirely.Unit()))
            # Trigger to set value to None again
            context.trigger_self()


@native_node_type(native_node_types, has_native_state=False)
class KeyState(NativeState):
    @staticmethod
    def get_value(*, context):
        value = context.inputs['current_value_old']
        timeout_time = context.inputs['timeout_time_old']
        timeout_reached = context.inputs['timeout_reached']
        event = context.inputs['key_event']

        if event == wirely.Some(True):
            # KeyPressed
            now = context.start_time_for_stage(RunStage.DEFAULT).monotonic
            new_value = True
            new_timeout_time = wirely.Some(now + context.values['Timeout'])
        elif event == wirely.Some(False):
            # KeyReleased
            new_value = False
            new_timeout_time = None
        else:
            # No event, keep current value (if there is no timeout)
            if not value:
                new_value = False
                new_timeout_time = None
            elif timeout_reached != wirely.Some(False):
                # Old value was True, but timeout was reached or time was lost
                print('Warning: TwoN.KeyState: Resetting key after timeout', flush=True)
                # Change value to False, cancel timeout
                new_value = False
                new_timeout_time = None
            else:
                # Current (True) value is kept, keep timeout
                new_value = True
                new_timeout_time = timeout_time

        context.set_output('output', new_value)
        context.set_output('timeout_time', new_timeout_time)
        context.set_output('current_value', new_value)


@native_node_type(native_node_types, has_native_state=True)
class IOState(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

        # TODO: What value should this provide while there is no connection?
        self.current_value = None

        # TODO: Read current value on start-up in a race-free way

        print('Create TwoN IOState', flush=True)

        print(f"  Port: {self.values['Port']!r}", flush=True)

        self.native_device = context.inputs['Device']
        print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['InputChanged', 'OutputChanged'], self.handler)

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        if not self.native_device.log_receiver.is_subscribed:
            raise Exception('Attempting to start up IOState without event subscription')

        response = await self.native_device.client.json_request("GET", "/api/io/status", params={
            'port': self.values['Port'],
        })
        # print('RESPONSE', response)
        ports = response['ports']
        if len(ports) != 1:
            raise Exception('TwoN.IOState: len(ports) != 1')
        port = ports[0]
        if port['port'] != self.values['Port']:
            raise Exception("TwoN.IOState: port['port'] != self.values['Port']")
        state = port['state']
        if type(state) != int:
            raise Exception('TwoN.IOState: type(state) != int')
        if state != 0 and state != 1:
            raise Exception('TwoN.IOState: state != 0 and state != 1')
        self.current_value = bool(state)

    def handler(self, event):
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] != 'InputChanged' and event['event'] != 'OutputChanged':
            print('Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['port'] != self.values['Port']:
            return

        new_value = event['params']['state']
        if type(new_value) != bool:
            print('Warning: Got unexpected non-boolean value: {new_value!r}', flush=True)
            return

        # print(f'New value: {new_value}')
        self.trigger('state')
        self.current_value = new_value

    @staticmethod
    def get_value(*, context):
        state = context.inputs['state']
        value = state.current_value
        if value is None:
            raise Exception('TwoN.IOState: get_value() called while current_value is None')

        context.set_output('output', value)


@native_node_type(native_node_types, has_native_state=True)
class SwitchState(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

        # TODO: What value should this provide while there is no connection?
        self.current_value = None

        # TODO: Read current value on start-up in a race-free way

        print('Create TwoN SwitchState', flush=True)

        print(f"  SwitchNumber: {self.values['SwitchNumber']!r}", flush=True)

        self.native_device = context.inputs['Device']
        print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['SwitchStateChanged'], self.handler)

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        if not self.native_device.log_receiver.is_subscribed:
            raise Exception('Attempting to start up SwitchState without event subscription')

        response = await self.native_device.client.json_request("GET", "/api/switch/status", params={
            'switch': self.values['SwitchNumber'],
        })
        # print('RESPONSE', response)
        switches = response['switches']
        if len(switches) != 1:
            raise Exception('TwoN.SwitchState: len(switches) != 1')
        switch = switches[0]
        if switch['switch'] != self.values['SwitchNumber']:
            raise Exception("TwoN.SwitchState: switch['switch'] != self.values['SwitchNumber']")
        active = switch['active']
        if type(active) != bool:
            raise Exception('TwoN.SwitchState: type(active) != bool')
        self.current_value = active

    def handler(self, event):
        # print(f'Got event: {event}')

        if event is None:
            return

        if event['event'] != 'SwitchStateChanged':
            print('Warning: Got unexpected event: {event!r}', flush=True)
            return

        if event['params']['switch'] != self.values['SwitchNumber']:
            return

        new_value = event['params']['state']
        if type(new_value) != bool:
            print('Warning: Got unexpected non-boolean value: {new_value!r}', flush=True)
            return

        # print(f'New value: {new_value}')
        self.trigger('state')
        self.current_value = new_value

    @staticmethod
    def get_value(*, context):
        state = context.inputs['state']
        value = state.current_value
        if value is None:
            raise Exception('TwoN.SwitchState: get_value() called while current_value is None')

        context.set_output('output', value)


@native_node_type(native_node_types, has_native_state=True)
class OutputControl(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)

        # Task is needed for sending out values
        self.task = None
        self.value_to_send = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        print('Create TwoN OutputControl', flush=True)

        print(f"  Port: {self.values['Port']!r}", flush=True)

        self.native_device = context.inputs['Device']
        print(f'  Using native device object: {self.native_device}', flush=True)

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @staticmethod
    def send_value(*, context):
        state = context.inputs['state']
        # TODO: Add checks whether everything is running?

        value_opt = context.inputs['input']
        if value_opt is None:
            return
        value = value_opt.value

        if state.value_to_send is not None:
            print('Warning: TwoN.OutputControl: Got new event to send while old event is pending', flush=True)
        state.value_to_send = value
        state.event.set()

        # This makes sure that the value is sent again if the input does not change
        context.trigger_self()

    async def run_task(self):
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
                print('Warning: TwoN.OutputControl: Unexpected value: {value}', flush=True)
                continue

            print(f"Sending {value} to port {self.values['Port']}", flush=True)
            # response = await self.native_device.client.json_request("GET", "/api/io/ctrl", params={
            # response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={}, params={
            response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={
                'port': self.values['Port'],
                'action': action,
            })
            print(f"Sending {value} to port {self.values['Port']} done with result {response}", flush=True)
