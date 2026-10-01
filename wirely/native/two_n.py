import asyncio
import sys
import os
import json
import dataclasses
import typing
import contextlib

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


# TODO: Move somewhere else?
def get_username_password(fn):
    with open(fn, 'r') as file:
        username = file.readline().strip()
        password = file.readline().strip()

    if username == '' or password == '':
        raise Exception('Failed to read username and password from {!r}'.format(fn))

    return username, password


@dataclasses.dataclass(frozen=True)
class TwoNDeviceData:
    hostname: str
    port: int
    # TODO: Parse into (int,int,int,bytes)?
    tlsa: list[str]
    # TODO: Should this contain the password directly or only the filename?
    username: str
    password: str

    @staticmethod
    def create(*, parameter_values):
        par_hostname = parameter_values['Host'].value
        if 'Port' in parameter_values:
            par_port = parameter_values['Port'].value
        else:
            # TODO: Should not be needed, this should use the default parameter value from two_n.wrl automatically
            par_port = 443
        par_tlsa = parameter_values['TLSA'].value

        par_pwdfile = parameter_values['UsernamePasswordFile'].value
        blk_file = parameter_values['']['BlkFilename']
        par_pwdfile = os.path.join(os.path.dirname(blk_file), par_pwdfile)
        username, password = get_username_password(par_pwdfile)

        return TwoNDeviceData(hostname=par_hostname, port=par_port, tlsa=par_tlsa, username=username, password=password)

    def dump(self):
        print(f'  Connecting to: {self.hostname!r}:{self.port}', flush=True)
        print(f'  Using TLSA: {self.tlsa!r}', flush=True)


@native_block_type(native_block_types, has_state=True, data_type=TwoNDeviceData)
class Connection(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        import wirely.two_n.client

        print('Create TwoN Connection', flush=True)

        self.data = static_block_context.parsed_native_instance

        self.data.dump()

        self.client = wirely.two_n.client.Client(hostname=self.data.hostname, port=self.data.port, tlsa=self.data.tlsa, http_api_username_password=(self.data.username, self.data.password))
        self.log_receiver = wirely.two_n.client.LogReceiver(self.client)

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        print('Starting TwoN client', flush=True)
        await async_exit_stack.enter_async_context(self.client)
        print('Starting log receiver', flush=True)
        # TODO: Should this try to unsubscribe on shutdown and if yes, how long should it wait?
        await self.log_receiver.start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)
        print('Starting TwoN client done', flush=True)


@dataclasses.dataclass(frozen=True)
class TwoNTimeoutData:
    timeout: blocks.Duration

    @staticmethod
    def create(*, parameter_values):
        # TODO: default parameters should be handled somewhere else
        if 'Timeout' not in parameter_values:
            par_timeout = blocks.Duration.parse('PT30S')
        else:
            par_timeout = parameter_values['Timeout'].value

        return TwoNTimeoutData(timeout=par_timeout)

    def dump(self):
        print(f'  Timeout: {self.timeout!r}', flush=True)


@dataclasses.dataclass(frozen=True)
class TwoNKeyData:
    key: str

    @staticmethod
    def create(*, parameter_values):
        par_key = parameter_values['Key'].value

        return TwoNKeyData(key=par_key)

    def dump(self):
        if blocks.util.getEnvironmentBool('WIRELY_TWON_TRACE_KEYS', False):
            print(f'  Key: {self.key!r}', flush=True)


@dataclasses.dataclass(frozen=True)
class TwoNIOData:
    port: str

    @staticmethod
    def create(*, parameter_values):
        par_port = parameter_values['Port'].value

        return TwoNIOData(port=par_port)

    def dump(self):
        print(f'  Port: {self.port!r}', flush=True)


@dataclasses.dataclass(frozen=True)
class TwoNSwitchData:
    switch_number: int

    @staticmethod
    def create(*, parameter_values):
        par_switch_number = int(parameter_values['SwitchNumber'].value)

        return TwoNSwitchData(switch_number=par_switch_number)

    def dump(self):
        print(f'  SwitchNumber: {self.switch_number!r}', flush=True)


@dataclasses.dataclass(frozen=True)
class TwoNCodeData:
    code: str

    @staticmethod
    def create(*, parameter_values):
        par_code = parameter_values['Code'].value

        if len(par_code) > 15:
            raise Exception('len(par_code) > 15')
        for c in par_code:
            if c < '0' or c > '9':
                raise Exception(f'Got non-digit in code {par_code!r}')

        return TwoNCodeData(code=par_code)

    def dump(self):
        if blocks.util.getEnvironmentBool('WIRELY_TWON_TRACE_KEYS', False):
            print(f'  Code: {self.code!r}', flush=True)


@native_block_type(native_block_types, has_state=True, data_type=TwoNKeyData)
class KeyEvent(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        self.received_value = None

        self.data = static_block_context.parsed_native_instance

        print('Create TwoN KeyEvent', flush=True)

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'get_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        self.data.dump()

        # TODO: Clean up
        device_path, device_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Device'])
        print(f'  Using device object: {device_path}', flush=True)

        self.native_device = engine.native_states[device_path]
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

        if event['params']['key'] != self.data.key:
            return

        # Note: This will override older events which generally should not be a problem.
        if self.received_value is not None:
            print(f'Warning: TwoN.KeyEvent: Got event while old event is pending: {self.received_value} -> {new_value}', flush=True)

        # print(f'New value: {new_value}')
        self.engine.trigger(self.block_path, self.proc)
        self.received_value = new_value

    @native_procedure
    def get_value(self, *, context):
        value = self.received_value
        self.received_value = None

        if value is None:
            context.set_result('output', None)
        else:
            context.set_result('output', blocks.Some(value))
            # Trigger to set value to None again
            self.engine.trigger(self.block_path, self.proc)


@native_block_type(native_block_types, has_state=True, data_type=TwoNCodeData)
class CodeEnteredEvent(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        self.pending = False

        self.data = static_block_context.parsed_native_instance

        print('Create TwoN CodeEnteredEvent', flush=True)

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'get_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        self.data.dump()

        # TODO: Clean up
        device_path, device_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Device'])
        print(f'  Using device object: {device_path}', flush=True)

        self.native_device = engine.native_states[device_path]
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

        if event['params']['code'] != self.data.code:
            return

        # Note: This will override older events which generally should not be a problem.
        if self.pending:
            print('Warning: TwoN.CodeEnteredEvent: Got event while old event is pending', flush=True)

        # print(f'New value: {new_value}')
        self.engine.trigger(self.block_path, self.proc)
        self.pending = True

    @native_procedure
    def get_value(self, *, context):
        value = self.pending
        self.pending = False

        if not value:
            context.set_result('output', None)
        else:
            context.set_result('output', blocks.Some(None))
            # Trigger to set value to None again
            self.engine.trigger(self.block_path, self.proc)


@native_block_type(native_block_types, has_state=False, data_type=TwoNTimeoutData)
class KeyState(blocks.NativeState):
    @native_procedure
    @staticmethod
    def read_timeout_time(*, context):
        value = context.inputs['timeout_time_old']
        context.set_result('TimeoutTimeOld', value)

    @native_procedure
    @staticmethod
    def get_value(*, context):
        data = context.static.block.parsed_native_instance

        value = context.inputs['current_value_old']
        timeout_time = context.inputs['timeout_time_old']
        timeout_reached = context.inputs['TimeoutReached']
        event = context.inputs['key_event']

        if event == blocks.Some(True):
            # KeyPressed
            now = context.step_info.stage_start_time_monotonic[blocks.RunStage.Default]
            new_value = True
            new_timeout_time = now + data.timeout
        elif event == blocks.Some(False):
            # KeyReleased
            new_value = False
            new_timeout_time = blocks.MonotonicTime.create_invalid()
        else:
            # No event, keep current value (if there is no timeout)
            if not value:
                new_value = False
                new_timeout_time = blocks.MonotonicTime.create_invalid()
            elif timeout_reached != blocks.Some(False):
                # Old value was True, but timeout was reached or time was lost
                print('Warning: TwoN.KeyState: Resetting key after timeout', flush=True)
                # Change value to False, cancel timeout
                new_value = False
                new_timeout_time = blocks.MonotonicTime.create_invalid()
            else:
                # Current (True) value is kept, keep timeout
                new_value = True
                new_timeout_time = timeout_time

        context.set_result('output', new_value)
        context.set_result('TimeoutTime', new_timeout_time)
        context.set_result('CurrentValue', new_value)


@native_block_type(native_block_types, has_state=True, data_type=TwoNIOData)
class IOState(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        # TODO: What value should this provide while there is no connection?
        self.current_value = None

        # TODO: Read current value on start-up in a race-free way

        self.data = static_block_context.parsed_native_instance

        print('Create TwoN IOState', flush=True)

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'get_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        self.data.dump()

        # TODO: Clean up
        device_path, device_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Device'])
        print(f'  Using device object: {device_path}', flush=True)

        self.native_device = engine.native_states[device_path]
        print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['InputChanged', 'OutputChanged'], self.handler)

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        if not self.native_device.log_receiver.is_subscribed:
            raise Exception('Attempting to start up IOState without event subscription')

        response = await self.native_device.client.json_request("GET", "/api/io/status", params={
            'port': self.data.port,
        })
        # print('RESPONSE', response)
        ports = response['ports']
        if len(ports) != 1:
            raise Exception('TwoN.IOState: len(ports) != 1')
        port = ports[0]
        if port['port'] != self.data.port:
            raise Exception("TwoN.IOState: port['port'] != self.data.port")
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

        if event['params']['port'] != self.data.port:
            return

        new_value = event['params']['state']
        if type(new_value) != bool:
            print('Warning: Got unexpected non-boolean value: {new_value!r}', flush=True)
            return

        # print(f'New value: {new_value}')
        self.engine.trigger(self.block_path, self.proc)
        self.current_value = new_value

    @native_procedure
    def get_value(self, *, context):
        value = self.current_value
        if value is None:
            raise Exception('TwoN.IOState: get_value() called while current_value is None')

        context.set_result('output', value)


@native_block_type(native_block_types, has_state=True, data_type=TwoNSwitchData)
class SwitchState(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        # TODO: What value should this provide while there is no connection?
        self.current_value = None

        # TODO: Read current value on start-up in a race-free way

        self.data = static_block_context.parsed_native_instance

        print('Create TwoN SwitchState', flush=True)

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'get_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        self.data.dump()

        # TODO: Clean up
        device_path, device_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Device'])
        print(f'  Using device object: {device_path}', flush=True)

        self.native_device = engine.native_states[device_path]
        print(f'  Using native device object: {self.native_device}', flush=True)

        # TODO: Do this here? Or register / deregister later?
        self.native_device.log_receiver.add_handler(['SwitchStateChanged'], self.handler)

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        if not self.native_device.log_receiver.is_subscribed:
            raise Exception('Attempting to start up SwitchState without event subscription')

        response = await self.native_device.client.json_request("GET", "/api/switch/status", params={
            'switch': self.data.switch_number,
        })
        # print('RESPONSE', response)
        switches = response['switches']
        if len(switches) != 1:
            raise Exception('TwoN.SwitchState: len(switches) != 1')
        switch = switches[0]
        if switch['switch'] != self.data.switch_number:
            raise Exception("TwoN.SwitchState: switch['switch'] != self.data.switch_number")
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

        if event['params']['switch'] != self.data.switch_number:
            return

        new_value = event['params']['state']
        if type(new_value) != bool:
            print('Warning: Got unexpected non-boolean value: {new_value!r}', flush=True)
            return

        # print(f'New value: {new_value}')
        self.engine.trigger(self.block_path, self.proc)
        self.current_value = new_value

    @native_procedure
    def get_value(self, *, context):
        value = self.current_value
        if value is None:
            raise Exception('TwoN.SwitchState: get_value() called while current_value is None')

        context.set_result('output', value)


@native_block_type(native_block_types, has_state=True, data_type=TwoNIOData)
class OutputControl(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        # Task is needed for sending out values
        self.task = None
        self.value_to_send = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        self.data = static_block_context.parsed_native_instance

        print('Create TwoN OutputControl', flush=True)

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'send_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        self.data.dump()

        # TODO: Clean up
        device_path, device_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Device'])
        print(f'  Using device object: {device_path}', flush=True)

        self.native_device = engine.native_states[device_path]
        print(f'  Using native device object: {self.native_device}', flush=True)

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @native_procedure
    def send_value(self, *, context):
        # TODO: Add checks whether everything is running?

        data = context.static.block.parsed_native_instance

        value_opt = context.inputs['input']
        if value_opt is None:
            return
        value = value_opt.value

        if self.value_to_send is not None:
            print('Warning: TwoN.OutputControl: Got new event to send while old event is pending', flush=True)
        self.value_to_send = value
        self.event.set()

        # This makes sure that the value is sent again if the input does not change
        self.engine.trigger(self.block_path, self.proc)

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

            print(f'Sending {value} to port {self.data.port}', flush=True)
            # response = await self.native_device.client.json_request("GET", "/api/io/ctrl", params={
            # response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={}, params={
            response = await self.native_device.client.json_request("POST", "/api/io/ctrl", data={
                'port': self.data.port,
                'action': action,
            })
            print(f'Sending {value} to port {self.data.port} done with result {response}', flush=True)
