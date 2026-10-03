import dataclasses
import contextlib
import asyncio

import wirely as blocks
from wirely.native import native_block_type

native_block_types = {}


@dataclasses.dataclass(frozen=True)
class LaunchOnEventData:
    shell_command: str


@native_block_type(native_block_types, has_native_state=True, data_type=LaunchOnEventData)
class LaunchOnEvent(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)

        self.data = context.configuration

        self.task = None
        self.pending = False
        self.event = asyncio.Event()

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    def run(self, *, context):
        inp = context.inputs['input']

        if inp is None:
            return

        if self.pending:
            print('Warning: ExtProg.LaunchOnEvent: Got new trigger while launch is already pending', flush=True)
        self.pending = True
        self.event.set()

        # This makes sure that the value is sent again if the input does not change
        self.trigger('run')

    async def run_task(self):
        while True:
            while not self.pending:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            self.pending = False

            print(f'Launching command {self.data.shell_command!r}', flush=True)
            proc = await asyncio.create_subprocess_exec('/bin/sh', '-c', self.data.shell_command)
            code = await proc.wait()
            print(f'Command {self.data.shell_command!r} finished with exit code {code}', flush=True)
