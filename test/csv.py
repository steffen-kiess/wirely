import asyncio
import contextlib
import os

import wirely as blocks

def assert_equals(value1, value2):
    if value1 != value2:
        raise Exception(f'Test assertion failed: {value1!r} != {value2!r}')

def assert_content_is(fn, value):
    with open(fn, 'rb') as file:
        actual = file.read().decode('utf-8')
    if actual != value:
        raise Exception(f'Test failed: Expected content {value!r}, got {actual!r}')

async def run_test():
    fn = engine.get_native_state('test_value_writer').data.filename
    fn2 = engine.get_native_state('test_option_value_writer').data.filename

    # TODO: Move some of this code to test infrastructure
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn), False)

        await engine.start_tasks_async(tg, async_exit_stack)
        assert_equals(os.path.getsize(fn), 0)
        expected = ''

        engine.get_native_state('test_input').set_value(False)
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},,false\r\n'
        assert_content_is(fn, expected)

        engine.get_native_state('test_input').set_value(False)
        _, _, step_info = engine.step()
        assert_content_is(fn, expected)

        engine.get_native_state('test_input').set_value(True)
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},false,true\r\n'
        assert_content_is(fn, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly before the entry gets repeated
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly after the entry gets repeated
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(10))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},true,true\r\n'
        assert_content_is(fn, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly before the entry gets repeated again
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly after the entry gets repeated again
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},true,true\r\n'
        assert_content_is(fn, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)
    expected += f'{engine.last_shutdown_time.isoformat()},true,\r\n'
    assert_content_is(fn, expected)

    # Re-open file after deleting it
    os.unlink(fn)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn), False)

        await engine.start_tasks_async(tg, async_exit_stack)
        assert_equals(os.path.getsize(fn), 0)
        expected = ''

        engine.get_native_state('test_input').set_value(False)

        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},,false\r\n'
        assert_content_is(fn, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)
    expected += f'{engine.last_shutdown_time.isoformat()},false,\r\n'
    assert_content_is(fn, expected)

    # Re-open file without deleting it
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        engine.get_native_state('test_input').set_value(False)

        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},,false\r\n'
        assert_content_is(fn, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)
    expected += f'{engine.last_shutdown_time.isoformat()},false,\r\n'
    assert_content_is(fn, expected)

    # Add incomplete line and reopen file
    with open(fn, 'a') as file:
        file.write('some-data')
        expected += 'some-data'
    assert_content_is(fn, expected)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        expected += '\n'
        assert_content_is(fn, expected)

        engine.get_native_state('test_input').set_value(False)

        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},,false\r\n'
        assert_content_is(fn, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)
    expected += f'{engine.last_shutdown_time.isoformat()},false,\r\n'
    assert_content_is(fn, expected)

    os.unlink(fn2)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn2), False)

        await engine.start_tasks_async(tg, async_exit_stack)
        assert_equals(os.path.getsize(fn2), 0)
        expected = ''

        engine.get_native_state('test_option_input').set_value(blocks.Some(False))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},,false\r\n'
        assert_content_is(fn2, expected)

        engine.get_native_state('test_option_input').set_value(blocks.Some(False))
        _, _, step_info = engine.step()
        assert_content_is(fn2, expected)

        engine.get_native_state('test_option_input').set_value(blocks.Some(True))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},false,true\r\n'
        assert_content_is(fn2, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(10))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},true,true\r\n'
        assert_content_is(fn2, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated again
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated again
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},true,true\r\n'
        assert_content_is(fn2, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        engine.get_native_state('test_option_input').set_value(None)
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[blocks.RunStage.Default].isoformat()},true,\r\n'
        assert_content_is(fn2, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(10))
        _, _, step_info = engine.step()
        # Note: No new message because value is None
        assert_content_is(fn2, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated again
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated again
        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        _, _, step_info = engine.step()
        # Note: No new message because value is None
        assert_content_is(fn2, expected)

        # Because of the changed RepeatTime some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)
    # Note: No message on shutdown because old value is None
    assert_content_is(fn2, expected)
