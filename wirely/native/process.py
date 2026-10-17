import contextlib
import asyncio

import wirely
from wirely.native import native_node_type, native_procedure, native_resource_type
from wirely.runtime.contexts import NativeNodeContext, NativeProcedure, NativeState, ProcedureContext

native_node_types: dict[str, type] = {}
native_resource_types: dict[str, type] = {}


@native_resource_type(native_resource_types, 'LaunchOnEventState')
@native_node_type(native_node_types, has_native_state=True)
class LaunchOnEvent(NativeState):
    def __init__(self, *, context: NativeNodeContext) -> None:
        super().__init__(context=context)
        context.set_output('state', self)

        self.task: asyncio.Task | None = None
        self.pending = False
        self.event = asyncio.Event()

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @native_procedure('run')
    class Run(NativeProcedure):
        def __call__(self, *, context: ProcedureContext) -> None:
            state = context.inputs['state'].object
            inp = context.inputs['input']

            if inp is None:
                return

            if state.pending:
                print('Warning: Process.LaunchOnEvent: Got new trigger while launch is already pending', flush=True)
            state.pending = True
            state.event.set()

            # This makes sure that the value is sent again if the input does not change
            context.trigger_self()

    async def run_task(self) -> None:
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
