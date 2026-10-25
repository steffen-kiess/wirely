import asyncio
import contextlib
import datetime
import os

import wirely
from wirely.program import RunStage

def assert_equals(value1, value2):
    if value1 != value2:
        raise Exception(f'Test assertion failed: {value1!r} != {value2!r}')

def assert_content_is(fn, value):
    with open(fn, 'rb') as file:
        actual = file.read().decode('utf-8')
    if actual != value:
        raise Exception(f'Test failed: Expected content {value!r}, got {actual!r}')

async def check_repeat_boundaries(filename):
    with open(filename, 'rb') as file:
        expected = file.read().decode('utf-8')
    source = engine.get_native_state('test_option_input.create')

    def step(row=None):
        nonlocal expected
        _, _, step_info = engine.step()
        if row is not None:
            time = step_info.stage_start_time[RunStage.DEFAULT].isoformat()
            expected += f'{time},{row[0]},{row[1]}\r\n'
        assert_content_is(filename, expected)
        engine.step()
        assert_content_is(filename, expected)
        engine.assert_is_idle()
        assert_content_is(filename, expected)

    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)
        source.set_value(wirely.Some(False))
        step(('', 'false'))
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        step()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
        step(('false', 'false'))

        # Changing the value halfway through the hour restarts the interval.
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(1800))
        source.set_value(wirely.Some(True))
        step(('false', 'true'))
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(1800))
        step()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(1799))
        step()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
        step(('true', 'true'))
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        step()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
        step(('true', 'true'))

        # Empty values are written on change, but not repeated.
        source.set_value(None)
        step(('true', ''))
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3600))
        step()
        source.set_value(wirely.Some(False))
        step(('', 'false'))

    expected += f'{engine.last_shutdown_time.isoformat()},false,\r\n'
    assert_content_is(filename, expected)


async def run_test():
    engine.set_fake_wall_time(datetime.datetime(
        2026, 1, 1, 14, tzinfo=datetime.timezone(datetime.timedelta(hours=2))))
    fn = engine.get_native_state('test_value_writer.create').filename
    fn2 = engine.get_native_state('test_option_value_writer.create').filename

    # TODO: Move some of this code to test infrastructure
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn), False)

        await engine.start_tasks_async(tg, async_exit_stack)
        assert_equals(os.path.getsize(fn), 0)
        expected = ''

        engine.get_native_state('test_input.create').set_value(False)
        _, _, step_info = engine.step()
        expected += '2026-01-01T14:00:00+02:00,,false\r\n'
        assert_content_is(fn, expected)

        engine.get_native_state('test_input.create').set_value(False)
        _, _, step_info = engine.step()
        assert_content_is(fn, expected)

        engine.get_native_state('test_input.create').set_value(True)
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},false,true\r\n'
        assert_content_is(fn, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly before the entry gets repeated
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly after the entry gets repeated
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},true,true\r\n'
        assert_content_is(fn, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly before the entry gets repeated again
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn, expected)

        # Wait until shortly after the entry gets repeated again
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},true,true\r\n'
        assert_content_is(fn, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)
    assert_equals(engine.last_shutdown_time.isoformat(), '2026-01-01T14:00:00+02:00')
    expected += '2026-01-01T14:00:00+02:00,true,\r\n'
    assert_content_is(fn, expected)

    # Re-open file after deleting it
    os.unlink(fn)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn), False)

        await engine.start_tasks_async(tg, async_exit_stack)
        assert_equals(os.path.getsize(fn), 0)
        expected = ''

        engine.get_native_state('test_input.create').set_value(False)

        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},,false\r\n'
        assert_content_is(fn, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn, expected)

        engine.assert_is_idle()
        assert_content_is(fn, expected)
    expected += f'{engine.last_shutdown_time.isoformat()},false,\r\n'
    assert_content_is(fn, expected)

    # Re-open file without deleting it
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        engine.get_native_state('test_input.create').set_value(False)

        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},,false\r\n'
        assert_content_is(fn, expected)

        # Because the last-write timestamp changed some procedures will be executed again
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

        engine.get_native_state('test_input.create').set_value(False)

        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},,false\r\n'
        assert_content_is(fn, expected)

        # Because the last-write timestamp changed some procedures will be executed again
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

        engine.get_native_state('test_option_input.create').set_value(wirely.Some(False))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},,false\r\n'
        assert_content_is(fn2, expected)

        engine.get_native_state('test_option_input.create').set_value(wirely.Some(False))
        _, _, step_info = engine.step()
        assert_content_is(fn2, expected)

        engine.get_native_state('test_option_input.create').set_value(wirely.Some(True))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},false,true\r\n'
        assert_content_is(fn2, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},true,true\r\n'
        assert_content_is(fn2, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated again
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated again
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},true,true\r\n'
        assert_content_is(fn2, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        engine.get_native_state('test_option_input.create').set_value(None)
        _, _, step_info = engine.step()
        expected += f'{step_info.stage_start_time[RunStage.DEFAULT].isoformat()},true,\r\n'
        assert_content_is(fn2, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(10))
        _, _, step_info = engine.step()
        # Note: No new message because value is None
        assert_content_is(fn2, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly before the entry gets repeated again
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3599))
        engine.assert_is_idle()
        assert_content_is(fn2, expected)

        # Wait until shortly after the entry gets repeated again
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
        _, _, step_info = engine.step()
        # Note: No new message because value is None
        assert_content_is(fn2, expected)

        # Because the last-write timestamp changed some procedures will be executed again
        engine.step()
        assert_content_is(fn2, expected)

        engine.assert_is_idle()
        assert_content_is(fn2, expected)
    # Note: No message on shutdown because old value is None
    assert_content_is(fn2, expected)
    await check_repeat_boundaries(fn2)

    # Unknown time preserves transitions and monotonic repeats, with empty timestamps.
    os.unlink(fn2)
    engine.set_fake_wall_time(None)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)
        engine.get_native_state('test_option_input.create').set_value(wirely.Some(True))
        engine.step()
        engine.step()
        expected = ',,true\r\n'
        assert_content_is(fn2, expected)
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3600))
        engine.step()
        engine.step()
        expected += ',true,true\r\n'
        assert_content_is(fn2, expected)
        engine.get_native_state('test_option_input.create').set_value(wirely.Some(False))
        engine.step()
        engine.step()
        expected += ',true,false\r\n'
        assert_content_is(fn2, expected)
    assert engine.last_shutdown_time is None
    expected += ',false,\r\n'
    assert_content_is(fn2, expected)

    engine.set_fake_wall_time(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)
        engine.get_native_state('test_option_input.create').set_value(wirely.Some(True))
        engine.step()
        engine.step()
        expected += '2026-01-01T00:00:00+00:00,,true\r\n'
        assert_content_is(fn2, expected)
    expected += '2026-01-01T00:00:00+00:00,true,\r\n'
    assert_content_is(fn2, expected)
