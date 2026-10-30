import asyncio
import contextlib
import datetime
import os

import wirely
from wirely.compiler import program as program_compiler
from wirely.compiler.compilation import compile_strs
from wirely.compiler.sources import standard_library_sources
from wirely.program.program import RunStage
from wirely.runtime.procedures import LoadError
from tests.infra.support import test_engine

def restart(wall_time):
    """Load the program again, as after a runtime restart, and use the new engine from now on."""
    global engine
    engine = test_engine(engine.program)
    engine.set_fake_wall_time(wall_time)

def assert_equals(value1, value2):
    if value1 != value2:
        raise Exception(f'Test assertion failed: {value1!r} != {value2!r}')

def assert_content_is(fn, value):
    with open(fn, 'rb') as file:
        actual = file.read().decode('utf-8')
    if actual != value:
        raise Exception(f'Test failed: Expected content {value!r}, got {actual!r}')

async def wait_until(condition):
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


class FailingStream:
    def write(self, data):
        raise OSError(28, 'No space left on device')


async def check_file_errors(filename):
    wall_time = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    time = wall_time.isoformat()
    with open(filename, 'rb') as file:
        expected = file.read().decode('utf-8')

    # A failed write closes the file. After it is open again, a row with empty values marks the time of the failure,
    # and the current value follows as after a start.
    restart(wall_time)
    writer = engine.get_native_state('test_option_value_writer.create')
    writer.reopen_delays = (0.01, 0.01)
    source = engine.get_native_state('test_option_input.create')
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        engine.activate(tg, async_exit_stack)
        source.set_value(wirely.Some(True))
        engine.step()
        engine.step()
        expected += f'{time},,true\r\n'
        writer.writer.stream = FailingStream()
        source.set_value(wirely.Some(False))
        engine.step()
        assert writer.file is None
        await wait_until(lambda: writer.file is not None)
        engine.step()
        engine.step()
        engine.assert_is_idle()
        expected += f'{time},,\r\n{time},,false\r\n'
        assert_content_is(filename, expected)
    expected += f'{time},false,\r\n'
    assert_content_is(filename, expected)

    # A file that cannot be opened is opened again later; values are dropped until then.
    directory = os.path.join(os.path.dirname(filename), 'later')
    later = os.path.join(directory, 'later.csv')
    restart(wall_time)
    writer = engine.get_native_state('test_option_value_writer.create')
    writer.filename = later
    writer.reopen_delays = (0.01, 0.01)
    source = engine.get_native_state('test_option_input.create')
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        engine.activate(tg, async_exit_stack)
        assert writer.file is None
        source.set_value(wirely.Some(True))
        engine.step()
        engine.step()
        engine.assert_is_idle()
        os.mkdir(directory)
        await wait_until(lambda: writer.file is not None)
        engine.step()
        engine.step()
        assert_content_is(later, f'{time},,true\r\n')
    assert_content_is(later, f'{time},,true\r\n{time},true,\r\n')

    # Two writers of the same file are rejected when loading.
    source_text = ''.join(f'''
node input{index}: Std.DebugValueSource {{ Type: boolean, Initial: false }}
node writer{index}: CSV.ValueWriter {{ Filename: "{path}", Type: boolean }}
connect input{index} -> writer{index};
''' for index, path in enumerate([later, os.path.join(directory, '.', 'later.csv')]))
    graph = compile_strs([*standard_library_sources(['std', 'timer', 'csv']), ('<csv-loading>', source_text)])
    assert graph is not None
    try:
        test_engine(program_compiler.compile_nocatch(graph))
    except LoadError as exc:
        assert f'CSV file {os.path.join(directory, ".", "later.csv")!r} is already written by writer0' in str(exc), exc
    else:
        raise AssertionError('Two writers of one file accepted')


async def check_repeat_boundaries(filename, wall_time):
    with open(filename, 'rb') as file:
        expected = file.read().decode('utf-8')
    restart(wall_time)
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
        engine.activate(tg, async_exit_stack)
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
    wall_time = datetime.datetime(2026, 1, 1, 14, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    engine.set_fake_wall_time(wall_time)
    fn = engine.get_native_state('test_value_writer.create').filename
    fn2 = engine.get_native_state('test_option_value_writer.create').filename

    # TODO: Move some of this code to test infrastructure
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn), False)

        engine.activate(tg, async_exit_stack)
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
    restart(wall_time)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn), False)

        engine.activate(tg, async_exit_stack)
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
    restart(wall_time)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        engine.activate(tg, async_exit_stack)

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
    restart(wall_time)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        engine.activate(tg, async_exit_stack)

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
    restart(wall_time)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        assert_equals(os.path.exists(fn2), False)

        engine.activate(tg, async_exit_stack)
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
    await check_repeat_boundaries(fn2, wall_time)

    # Unknown time preserves transitions and monotonic repeats, with empty timestamps.
    os.unlink(fn2)
    restart(None)
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        engine.activate(tg, async_exit_stack)
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

    restart(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        engine.activate(tg, async_exit_stack)
        engine.get_native_state('test_option_input.create').set_value(wirely.Some(True))
        engine.step()
        engine.step()
        expected += '2026-01-01T00:00:00+00:00,,true\r\n'
        assert_content_is(fn2, expected)
    expected += '2026-01-01T00:00:00+00:00,true,\r\n'
    assert_content_is(fn2, expected)

    await check_file_errors(fn2)
