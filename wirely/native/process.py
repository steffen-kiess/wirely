import contextlib
import asyncio
from typing import Any, Literal

from wirely import DynamicValue, Some, ValueType
from wirely.generated import process as contracts
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.util import verbose_print


class LaunchOnEvent(contracts.LaunchOnEventCreate):
    def initialize(self, *, services: StateServices[Literal['state']],
                   shell_command: str) -> contracts.LaunchOnEventCreateResult:
        self.shell_command = shell_command
        self.task: asyncio.Task | None = None
        self.pending = False
        self.event = asyncio.Event()
        return contracts.LaunchOnEventCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    class Run(contracts.LaunchOnEventRun):
        def prepare(self, *, services: PreparationServices, state: 'LaunchOnEvent', type: ValueType[Any]) -> None:
            pass

        def run(self, *, services: ProcedureServices, state: 'LaunchOnEvent',
                input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.LaunchOnEventRunResult:
            if input is None:
                return contracts.LaunchOnEventRunResult()

            if state.pending:
                print('Warning: Process.LaunchOnEvent: Got new trigger while launch is already pending', flush=True)
            state.pending = True
            state.event.set()

            # This makes sure that the value is sent again if the input does not change
            services.trigger_self()
            return contracts.LaunchOnEventRunResult()

    async def run_task(self) -> None:
        while True:
            while not self.pending:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            self.pending = False

            verbose_print(f"Launching command {self.shell_command!r}", flush=True)
            proc = await asyncio.create_subprocess_exec('/bin/sh', '-c', self.shell_command)
            code = await proc.wait()
            if code != 0:
                print(f"Command {self.shell_command!r} finished with exit code {code}", flush=True)
            else:
                verbose_print(f"Command {self.shell_command!r} finished with exit code {code}", flush=True)


LaunchOnEventState = LaunchOnEvent  # Used as a resource type.
