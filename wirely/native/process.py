import contextlib
import asyncio

import wirely
from wirely.native import native_node_type
from wirely.runtime.contexts import NativeState

native_node_types = {}


@native_node_type(native_node_types, has_native_state=True)
class LaunchOnEvent(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)

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
            print('Warning: Process.LaunchOnEvent: Got new trigger while launch is already pending', flush=True)
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

            print(f"Launching command {self.values['ShellCommand']!r}", flush=True)
            proc = await asyncio.create_subprocess_exec('/bin/sh', '-c', self.values['ShellCommand'])
            code = await proc.wait()
            print(f"Command {self.values['ShellCommand']!r} finished with exit code {code}", flush=True)
