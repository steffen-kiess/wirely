import asyncio
import contextlib
import tempfile
from dataclasses import replace

import wirely


async def run_test():
    with tempfile.TemporaryDirectory(prefix='test_dir_ext.') as tmp:
        tmp_file = tmp + '/file'
        cmd = "echo x >> '" + tmp_file.replace("'", "'\\''") + "'; sleep 0.1"

        with open(tmp_file, 'w') as file:
            file.write('A\n')

        def check_content(expected):
            with open(tmp_file, 'r') as file:
                actual = file.read()
            if actual != expected:
                raise Exception(f'Expected file content {expected!r}, got {actual!r}')

        prog1 = engine.get_native_state('prog1')
        prog1._context = replace(prog1.context, values={'ShellCommand': cmd})

        async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
            await engine.start_tasks_async(tg, async_exit_stack)

            check_content('A\n')

            engine.step()
            engine.assert_is_idle()

            check_content('A\n')

            engine.get_native_state('evsrc').send_event(wirely.Some(wirely.Unit()))
            engine.step()
            engine.step()
            engine.assert_is_idle()

            # Wait for command to be executed
            await asyncio.sleep(0.2)

            check_content('A\nx\n')

            # Send an event
            engine.get_native_state('evsrc').send_event(wirely.Some(wirely.Unit()))
            engine.step()
            engine.step()
            engine.assert_is_idle()

            # Wait for command execution to start
            await asyncio.sleep(0.03)

            # Send an another event
            engine.get_native_state('evsrc').send_event(wirely.Some(wirely.Unit()))
            engine.step()
            engine.step()
            engine.assert_is_idle()

            # Wait for command execution to start
            await asyncio.sleep(0.03)

            # Send an another event, which should be ignored
            engine.get_native_state('evsrc').send_event(wirely.Some(wirely.Unit()))
            engine.step()
            engine.step()
            engine.assert_is_idle()

            # Wait for everything to finish
            await asyncio.sleep(0.4)

            check_content('A\nx\nx\nx\n')
