import datetime
import asyncio
import contextlib
import typing
from collections.abc import Sequence

import wirely
from wirely.generated import schedule as contracts
from wirely.runtime.native_api import ProcedureServices, StateServices


class TimeOfDayMinute(contracts.TimeOfDayMinuteInitializer):
    def initialize(self, *, services: StateServices[typing.Literal['state']]
                   ) -> contracts.TimeOfDayMinuteInitializerResult:
        self.task: asyncio.Task | None = None
        self._fake_time: wirely.TimeOfDay | None = None

        # Make sure current_value is always initialized
        self._update_value()
        return contracts.TimeOfDayMinuteInitializerResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    def _update_value(self) -> float:
        # print('_update_value')

        now_time = datetime.datetime.now().time()
        # print(now_time, flush=True)
        now_min = wirely.TimeOfDay.from_parts(now_time.hour, now_time.minute)
        wait_time_s = wait_time_s = 60 - (now_time.second + now_time.microsecond * 1e-6)

        self.current_value = now_min
        # TODO: Should this avoid triggering if the value doesn't change?
        # TODO: Should this avoid triggering while _fake_time is set?
        self.services.trigger('state')

        return wait_time_s

    async def run_task(self) -> None:
        # TODO: Stop this while _fake_time is set? Probably not.
        while True:
            wait_time_s = self._update_value()

            # Wait slightly longer to make sure it really is the next minute
            wait_time_s += 0.1

            # print(f'Waiting for {wait_time_s}', flush=True)
            await asyncio.sleep(wait_time_s)

    class GetValue(contracts.TimeOfDayMinuteGetValue):
        def run(self, *, services: ProcedureServices,
                state: 'TimeOfDayMinute') -> contracts.TimeOfDayMinuteGetValueResult:
            if state._fake_time is not None:
                return contracts.TimeOfDayMinuteGetValueResult(output=state._fake_time)

            if state.task is None:
                raise Exception('start_tasks() was never called')

            return contracts.TimeOfDayMinuteGetValueResult(output=state.current_value)

    # TODO: Is this used anywhere?
    def update_value(self) -> None:
        import datetime

        self.services.trigger('state')

    def set_fake_time(self, fake_time: typing.Optional[wirely.TimeOfDay]) -> None:
        self._fake_time = fake_time

        # TODO: Make sure that seconds/microseconds are 0?

        self.services.trigger('state')

    def debug_command(self, parameters: Sequence[str]) -> None:
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
                self.set_fake_time(value.extract(wirely.value_types.time_of_day, native_types=self.services.native_types))
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            print(f'Current real time of day is {self.current_value}, fake time is {self._fake_time}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


TimeOfDayMinuteState = TimeOfDayMinute  # Used as a resource type.
