import datetime
import asyncio
import dataclasses
import contextlib
import typing

import wirely.misc.service
import wirely as blocks
from wirely.native import native_block_type, native_procedure
from wirely.util import check_parameters

native_block_types = {}


@native_block_type(native_block_types, has_state=False)
class MonotonicTimeReached(blocks.NativeState):
    @native_procedure
    @staticmethod
    def run(*, context):
        time = context.inputs['input']
        now = context.step_info.stage_start_time_monotonic[blocks.RunStage.Default]

        # print('MonotonicTimeReached', time, now)
        if not blocks.MonotonicTime.is_comparable(time, now):
            result = None
            context.wakeup_time = None
        elif blocks.MonotonicTime.compare(now, time) >= 0:
            result = blocks.Some(True)
            context.wakeup_time = None
        else:
            result = blocks.Some(False)
            context.wakeup_time = time
        # print('MonotonicTimeReached', time, now, result)

        context.set_result('output', result)


# TODO: Use slots=True here / for other dataclasses?
@dataclasses.dataclass(frozen=True)
class DelayChangeData:
    time: blocks.Duration

    @staticmethod
    def create(*, parameter_values):
        time = parameter_values['Duration'].evaluate()

        return DelayChangeData(time=time)


@native_block_type(native_block_types, has_state=False, data_type=DelayChangeData)
class DelayChange(blocks.NativeState):
    @native_procedure
    @staticmethod
    def read_update_time(*, context):
        value = context.inputs['update_time_old']
        context.set_result('UpdateTimeOld', value)

    @native_procedure
    @staticmethod
    def run(*, context):
        data = context.static.block.parsed_native_instance

        inp = context.inputs['input']
        reached = context.inputs['Reached']
        cur = context.inputs['current_value_old']
        new = context.inputs['new_value_old']
        update_time = context.inputs['update_time_old']

        # print('TODO', inp, cur, new, update_time, reached)
        if cur is None or (new is not None and reached is None) or cur == blocks.Some(inp):
            # Directly after startup or after time got lost.
            # Or: Input is current value. Cancel any changes.
            n_cur = blocks.Some(inp)
            n_new = None
            n_update_time = blocks.MonotonicTime.create_invalid()
            out = inp
        elif new != blocks.Some(inp):
            # Input is neither cur not new. Start a change to inp.
            now = context.step_info.stage_start_time_monotonic[blocks.RunStage.Default]
            n_cur = cur
            n_new = blocks.Some(inp)
            n_update_time = now + data.time
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
            n_update_time = blocks.MonotonicTime.create_invalid()
            out = new.value

        context.set_result('CurrentValue', n_cur)
        context.set_result('NewValue', n_new)
        context.set_result('UpdateTime', n_update_time)
        context.set_result('output', out)


@native_block_type(native_block_types, has_state=True)
class TimeOfDayMinute(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)
        # print('XXX', path)
        self.task = None
        self._fake_time = None

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'get_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

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
        now_min = datetime.time(now_time.hour, now_time.minute)
        wait_time_s = wait_time_s = 60 - (now_time.second + now_time.microsecond * 1e-6)

        self.current_value = now_min
        # TODO: Should this avoid triggering if the value doesn't change?
        # TODO: Should this avoid triggering while _fake_time is set?
        self.engine.trigger(self.block_path, self.proc)

        return wait_time_s

    async def run_task(self):
        # TODO: Stop this while _fake_time is set? Probably not.
        while True:
            wait_time_s = self._update_value()

            # Wait slightly longer to make sure it really is the next minute
            wait_time_s += 0.1

            # print(f'Waiting for {wait_time_s}', flush=True)
            await asyncio.sleep(wait_time_s)

    @native_procedure
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

        self.engine.trigger(self.block_path, self.proc)

    @check_parameters
    def set_fake_time(self, fake_time: typing.Optional[datetime.time]):
        self._fake_time = fake_time

        # TODO: Make sure that seconds/microseconds are 0?

        self.engine.trigger(self.block_path, self.proc)

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
                value = blocks.types.TimeOfDay.parse(parameters[1])
                self.set_fake_time(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            print(f'Current real time of day is {self.current_value}, fake time is {self._fake_time}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@dataclasses.dataclass(frozen=True, slots=True)
class IsBetweenData:
    start: datetime.time
    end: datetime.time

    # TODO: verify types? (Might also be useful for other dataclass types)

    # TODO: Allow "24:00" for start or end

    # TODO: Add static properties for "always" and "never"?

    # TODO: Add a method to check whether a certain datetime.time is in this range?

    def __post_init__(self):
        if self.start == self.end:
            raise Exception('self.start == self.end')

    @staticmethod
    def create(*, parameter_values):
        par_start = parameter_values['Start'].evaluate()
        par_end = parameter_values['End'].evaluate()

        return IsBetweenData(start=par_start, end=par_end)


@native_block_type(native_block_types, has_state=False, data_type=IsBetweenData)
class IsBetween:
    @native_procedure
    @staticmethod
    def run(*, context):
        data = context.static.block.parsed_native_instance

        time = context.inputs['current_time']

        # print('IsBetween', data.start, data.end, time)
        if data.start == data.end:
            # Note: This was checked in create_block_instance()
            raise Exception('data.start == data.end')
        elif data.start < data.end:
            res = (time >= data.start) and (time < data.end)
        else: # data.end > data.start
            res = (time >= data.start) or (time < data.end)

        context.set_result('output', res)


@dataclasses.dataclass(frozen=True)
class TriggerAtData:
    value: object

    @staticmethod
    def create(*, parameter_values):
        # TODO: default parameters should be handled somewhere else
        if 'OutputType' not in parameter_values:
            par_type = blocks.types.unit
        else:
            par_type = parameter_values['OutputType']

        if 'OutputValue' not in parameter_values:
            value = blocks.Unit()
            if par_type != blocks.types.unit:
                # TODO: Provide error location in a better way / avoid stack trace?
                raise Exception(f'Trigger type is not unit ({par_type}) but no value is given')
        else:
            value = parameter_values['OutputValue'].value

        value = par_type.verify_value(value)

        return TriggerAtData(value=value)


@native_block_type(native_block_types, has_state=False, data_type=TriggerAtData)
class TriggerAt:
    @native_procedure
    @staticmethod
    def run(*, context):
        data = context.static.block.parsed_native_instance

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

        context.set_result('output', blocks.Some(data.value) if do_trigger else None)
        context.set_result('State', new_state)
