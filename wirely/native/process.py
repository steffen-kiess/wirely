import contextlib
import asyncio
from typing import Any, Literal

from wirely import Some
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.generated import process as contracts
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.util import verbose_print


class LaunchOnEventCreate(contracts.LaunchOnEventCreate):
    def initialize(self, *, services: StateServices[Literal['state']],
                   shell_command: str) -> contracts.LaunchOnEventCreateResult:
        self.shell_command = shell_command
        # The shell that runs the command.
        self.shell = '/bin/sh'
        self.task: asyncio.Task | None = None
        self.pending = False
        self.event = asyncio.Event()
        return contracts.LaunchOnEventCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    async def run_task(self) -> None:
        while True:
            while not self.pending:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            self.pending = False

            verbose_print(f"Launching command {self.shell_command!r}", flush=True)
            try:
                proc = await asyncio.create_subprocess_exec(self.shell, '-c', self.shell_command)
            except OSError as e:
                # For example, too many processes or too little memory; the launch is dropped.
                print(f'Warning: Process.LaunchOnEvent: Starting {self.shell_command!r} failed: {e}', flush=True)
                continue
            # When torn down, this task stops waiting; asyncio kills a command that is still running when its
            # transport is closed or garbage-collected.
            # TODO: Once teardown happens without the process exiting, that is at an unspecified time; kill the
            # command explicitly then?
            code = await proc.wait()
            if code != 0:
                print(f"Command {self.shell_command!r} finished with exit code {code}", flush=True)
            else:
                verbose_print(f"Command {self.shell_command!r} finished with exit code {code}", flush=True)


class LaunchOnEventRun(contracts.LaunchOnEventRun):
    def prepare(self, *, services: PreparationServices, state: 'LaunchOnEventCreate', type: ValueType[Any]) -> None:
        pass

    def run(self, *, services: ProcedureServices, state: 'LaunchOnEventCreate',
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


LaunchOnEventState = LaunchOnEventCreate  # Used as a resource type.
