import asyncio
import tempfile

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

        prog1 = engine.get_native_state('prog1.create')
        prog1.shell_command = cmd

        check_content('A\n')

        engine.step()
        engine.assert_is_idle()

        check_content('A\n')

        engine.get_native_state('evsrc.create').send_event(wirely.Some(wirely.Unit()))
        engine.step()
        engine.step()
        engine.assert_is_idle()

        # Wait for command to be executed
        await asyncio.sleep(0.2)

        check_content('A\nx\n')

        # Send an event
        engine.get_native_state('evsrc.create').send_event(wirely.Some(wirely.Unit()))
        engine.step()
        engine.step()
        engine.assert_is_idle()

        # Wait for command execution to start
        await asyncio.sleep(0.03)

        # Send an another event
        engine.get_native_state('evsrc.create').send_event(wirely.Some(wirely.Unit()))
        engine.step()
        engine.step()
        engine.assert_is_idle()

        # Wait for command execution to start
        await asyncio.sleep(0.03)

        # Send an another event, which should be ignored
        engine.get_native_state('evsrc.create').send_event(wirely.Some(wirely.Unit()))
        engine.step()
        engine.step()
        engine.assert_is_idle()

        # Wait for everything to finish
        await asyncio.sleep(0.4)

        check_content('A\nx\nx\nx\n')

        # A command that cannot be started is dropped, and later events still launch it.
        prog1.shell = '/nonexistent/sh'
        engine.get_native_state('evsrc.create').send_event(wirely.Some(wirely.Unit()))
        engine.step()
        engine.step()
        engine.assert_is_idle()
        async with asyncio.timeout(5):
            while prog1.pending:
                await asyncio.sleep(0.01)
        prog1.shell = '/bin/sh'
        engine.get_native_state('evsrc.create').send_event(wirely.Some(wirely.Unit()))
        engine.step()
        engine.step()
        engine.assert_is_idle()
        await asyncio.sleep(0.2)

        check_content('A\nx\nx\nx\nx\n')
