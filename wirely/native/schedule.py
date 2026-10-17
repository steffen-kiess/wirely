import datetime
import asyncio
import contextlib
import typing

import wirely
from wirely.native import native_node_type, native_procedure, native_resource_type
from wirely.runtime.contexts import NativeProcedure, NativeState

native_node_types: dict[str, type] = {}
native_resource_types: dict[str, type] = {}


@native_resource_type(native_resource_types, 'TimeOfDayMinuteState')
@native_node_type(native_node_types, has_native_state=True)
class TimeOfDayMinute(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)
        self.task = None
        self._fake_time = None

        # Make sure current_value is always initialized
        self._update_value()

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    def _update_value(self):
        # print('_update_value')

        now_time = datetime.datetime.now().time()
        # print(now_time, flush=True)
        now_min = wirely.TimeOfDay.from_parts(now_time.hour, now_time.minute)
        wait_time_s = wait_time_s = 60 - (now_time.second + now_time.microsecond * 1e-6)

        self.current_value = now_min
        # TODO: Should this avoid triggering if the value doesn't change?
        # TODO: Should this avoid triggering while _fake_time is set?
        self.trigger('state')

        return wait_time_s

    async def run_task(self):
        # TODO: Stop this while _fake_time is set? Probably not.
        while True:
            wait_time_s = self._update_value()

            # Wait slightly longer to make sure it really is the next minute
            wait_time_s += 0.1

            # print(f'Waiting for {wait_time_s}', flush=True)
            await asyncio.sleep(wait_time_s)

    @native_procedure('get_value')
    class GetValue(NativeProcedure):
        def __call__(self, *, context):
            state = context.inputs['state'].object
            if state._fake_time is not None:
                context.set_output('output', state._fake_time)
                return

            if state.task is None:
                raise Exception('start_tasks() was never called')

            context.set_output('output', state.current_value)

    # TODO: Is this used anywhere?
    def update_value(self):
        import datetime

        self.trigger('state')

    def set_fake_time(self, fake_time: typing.Optional[wirely.TimeOfDay]):
        self._fake_time = fake_time

        # TODO: Make sure that seconds/microseconds are 0?

        self.trigger('state')

    def debug_command(self, parameters):
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'set_fake_time':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            if parameters[1].lower() == 'none':
                self.set_fake_time(None)
            else:
                value = wirely.value_types.time_of_day.parse(parameters[1])
                self.set_fake_time(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            print(f'Current real time of day is {self.current_value}, fake time is {self._fake_time}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')
