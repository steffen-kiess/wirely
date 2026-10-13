import datetime
import asyncio
import contextlib
import typing

import wirely
from wirely.native import native_node_type
from wirely.util import check_parameters
from wirely.runtime.contexts import NativeState

native_node_types = {}


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
        self.trigger('Schedule.TimeOfDayMinute:get_value', 'state', output='state')

        return wait_time_s

    async def run_task(self):
        # TODO: Stop this while _fake_time is set? Probably not.
        while True:
            wait_time_s = self._update_value()

            # Wait slightly longer to make sure it really is the next minute
            wait_time_s += 0.1

            # print(f'Waiting for {wait_time_s}', flush=True)
            await asyncio.sleep(wait_time_s)

    @staticmethod
    def get_value(*, context):
        state = context.inputs['state']
        if state._fake_time is not None:
            context.set_output('output', state._fake_time)
            return

        if state.task is None:
            raise Exception('start_tasks() was never called')

        context.set_output('output', state.current_value)

    # TODO: Is this used anywhere?
    def update_value(self):
        import datetime

        self.trigger('Schedule.TimeOfDayMinute:get_value', 'state', output='state')

    @check_parameters
    def set_fake_time(self, fake_time: typing.Optional[wirely.TimeOfDay]):
        self._fake_time = fake_time

        # TODO: Make sure that seconds/microseconds are 0?

        self.trigger('Schedule.TimeOfDayMinute:get_value', 'state', output='state')

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


@native_node_type(native_node_types, has_native_state=False)
class IsInDailyInterval:
    @staticmethod
    def run(*, context):
        start = context.values['Start']
        end = context.values['End']
        time = context.inputs['current_time']

        if start < end:
            res = (time >= start) and (time < end)
        else:
            res = (time >= start) or (time < end)

        context.set_output('output', res)


@native_node_type(native_node_types, has_native_state=False)
class TriggerAt:
    @staticmethod
    def run(*, context):
        output_value = context.values['OutputValue']
        value = wirely.Unit() if output_value is None else output_value.value

        old_state = context.inputs['state_old']
        before = context.inputs['before']
        after = context.inputs['after']

        if before and after:
            # TODO: Handle this without an exception?
            raise Exception('before and after')
        do_trigger = False
        if old_state == 0:
            if before:
                new_state = 1
            elif after:
                # Note: This can happen startup is during an 'after' range
                # (Or if for some reason the entire 'before' range was skipped.)
                new_state = 2
            else:
                # Keep state
                new_state = 0
        elif old_state == 1:
            if before:
                # Keep state
                new_state = 1
            elif after:
                # Trigger the output
                do_trigger = True
                new_state = 2
            else:
                # Return to outside state
                new_state = 0
        elif old_state == 2:
            if before:
                # Keep state
                # This might happen if the clock after triggering goes back slightly (e.g. DST change).
                new_state = 2
            elif after:
                # Keep state
                new_state = 2
            else:
                # Return to outside state
                new_state = 0
        else:
            # TODO: Handle this without an exception?
            raise Exception(f'Unknown state: {old_state}')

        # print(f'TriggerAt.run(): {old_state} {before} {after} -> {new_state} {do_trigger}')

        context.set_output('output', wirely.Some(value) if do_trigger else None)
        context.set_output('trigger_state', new_state)
