import datetime
import asyncio
import contextlib
import typing

import wirely.misc.service
import wirely as blocks
from wirely.native import native_block_type
from wirely.util import check_parameters
from wirely.program import RunStage
from wirely.runtime.contexts import NativeState

native_block_types = {}


@native_block_type(native_block_types, has_native_state=False)
class MonotonicTimeReached(NativeState):
    @staticmethod
    def run(*, context):
        time = context.inputs['input']
        now = context.start_time_for_stage(RunStage.DEFAULT).monotonic

        # print('MonotonicTimeReached', time, now)
        if time is not None:
            time = time.value
        if time is None or not blocks.MonotonicTime.is_comparable(time, now):
            result = None
            context.set_wakeup_time(None)
        elif blocks.MonotonicTime.compare(now, time) >= 0:
            result = blocks.Some(True)
            context.set_wakeup_time(None)
        else:
            result = blocks.Some(False)
            context.set_wakeup_time(time)
        # print('MonotonicTimeReached', time, now, result)

        context.set_result('output', result)


@native_block_type(native_block_types, has_native_state=False)
class DebounceStable(NativeState):
    @staticmethod
    def run(*, context):
        inp = context.inputs['input']
        reached = context.inputs['reached']
        cur = context.inputs['current_value_old']
        new = context.inputs['new_value_old']
        update_time = context.inputs['update_time_old']

        # print('TODO', inp, cur, new, update_time, reached)
        if cur is None or (new is not None and reached is None) or cur == blocks.Some(inp):
            # Directly after startup or after time got lost.
            # Or: Input is current value. Cancel any changes.
            n_cur = blocks.Some(inp)
            n_new = None
            n_update_time = None
            out = inp
        elif new != blocks.Some(inp):
            # Input is neither cur not new. Start a change to inp.
            now = context.start_time_for_stage(RunStage.DEFAULT).monotonic
            n_cur = cur
            n_new = blocks.Some(inp)
            n_update_time = blocks.Some(now + context.values['Duration'])
            # Note: cur cannot be None because if the first if
            out = cur.value
        elif not reached.value:
            # Input is new (and != cur), but the time has not yet expired. Do nothing.
            # Some checks
            if new != blocks.Some(inp):
                raise Exception('new != Some(inp)')

            n_cur = cur
            n_new = new
            n_update_time = update_time
            # Note: cur cannot be None because if the first if
            out = cur.value
        else:
            # Input is new and Timeout has been reached. Change to new.
            # Some checks
            if new != blocks.Some(inp):
                raise Exception('new != Some(inp)')

            n_cur = new
            n_new = None
            n_update_time = None
            out = new.value

        context.set_result('current_value', n_cur)
        context.set_result('new_value', n_new)
        context.set_result('update_time', n_update_time)
        context.set_result('output', out)


@native_block_type(native_block_types, has_native_state=True)
class TimeOfDayMinute(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
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
        now_min = blocks.TimeOfDay.from_parts(now_time.hour, now_time.minute)
        wait_time_s = wait_time_s = 60 - (now_time.second + now_time.microsecond * 1e-6)

        self.current_value = now_min
        # TODO: Should this avoid triggering if the value doesn't change?
        # TODO: Should this avoid triggering while _fake_time is set?
        self.trigger('get_value')

        return wait_time_s

    async def run_task(self):
        # TODO: Stop this while _fake_time is set? Probably not.
        while True:
            wait_time_s = self._update_value()

            # Wait slightly longer to make sure it really is the next minute
            wait_time_s += 0.1

            # print(f'Waiting for {wait_time_s}', flush=True)
            await asyncio.sleep(wait_time_s)

    def get_value(self, *, context):
        if self._fake_time is not None:
            context.set_result('output', self._fake_time)
            return

        if self.task is None:
            raise Exception('start_tasks() was never called')

        context.set_result('output', self.current_value)

    # TODO: Is this used anywhere?
    def update_value(self):
        import datetime

        self.trigger('get_value')

    @check_parameters
    def set_fake_time(self, fake_time: typing.Optional[blocks.TimeOfDay]):
        self._fake_time = fake_time

        # TODO: Make sure that seconds/microseconds are 0?

        self.trigger('get_value')

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
                value = blocks.value_types.time_of_day.parse(parameters[1])
                self.set_fake_time(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            print(f'Current real time of day is {self.current_value}, fake time is {self._fake_time}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@native_block_type(native_block_types, has_native_state=False)
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

        context.set_result('output', res)


@native_block_type(native_block_types, has_native_state=False)
class TriggerAt:
    @staticmethod
    def run(*, context):
        output_value = context.values['OutputValue']
        value = blocks.Unit() if output_value is None else output_value.value

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

        context.set_result('output', blocks.Some(value) if do_trigger else None)
        context.set_result('trigger_state', new_state)
