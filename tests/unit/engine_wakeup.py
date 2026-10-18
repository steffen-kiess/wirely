import asyncio
import contextlib

import wirely
from tests.infra.support import test_engine


async def run_engine_until(runtime, condition, *, timeout_s):
    run_task = asyncio.create_task(runtime.run())
    try:
        async with asyncio.timeout(timeout_s):
            while not run_task.done() and not condition():
                await asyncio.sleep(0.01)
        if run_task.done():
            run_task.result()
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task


async def check_wakeup_passed_during_step():
    # The clock only advances by 1 h after each step, so the wakeup deadline
    # is in the future while the step runs and has passed when run() reads
    # the clock afterwards.
    engine.enable_fixed_monotonic_time()
    now = engine.get_monotonic_time()
    original_step = engine.step

    def step():
        result = original_step()
        # Simulate time spent in step() without a clock-change notification.
        offset = engine.fake_monotonic_time_offset
        assert offset is not None
        engine.fake_monotonic_time_offset = offset + wirely.Duration.from_seconds(3600)
        return result

    engine.step = step
    try:
        steps = engine.step_count
        engine.get_native_state('reached_input').set_value(wirely.Some(now + wirely.Duration.from_seconds(1800)))
        engine.triggered_procs_event.clear()
        await run_engine_until(engine, lambda: engine.step_count >= steps + 2, timeout_s=1)
        engine.assert_value_is('reached', wirely.Some(True))
    finally:
        del engine.step


async def check_wakeup_with_fake_time_offset():
    # The deadline is reached shortly after the start in fake time, but only
    # after an hour in real time.
    runtime = test_engine(engine.program)
    offset = wirely.Duration.from_seconds(3600)
    runtime.advance_fake_monotonic_time(offset)
    deadline = runtime.get_monotonic_time() + wirely.Duration.from_seconds(0.1)
    runtime.get_native_state('reached_input').set_value(wirely.Some(deadline))
    await run_engine_until(
        runtime, lambda: runtime.step_count > 0 and runtime.get_value('reached') == wirely.Some(True), timeout_s=5)


async def check_clock_change_wakes_waiting_engine(mode):
    runtime = test_engine(engine.program)
    if mode in ('advance', 'generation'):
        runtime.enable_fixed_monotonic_time()
    span = wirely.Duration.from_seconds(3600)
    deadline = runtime.get_monotonic_time() + span
    runtime.get_native_state('reached_input').set_value(wirely.Some(deadline))
    run_task = asyncio.create_task(runtime.run())
    try:
        async with asyncio.timeout(1):
            while runtime.next_wakeup != deadline or runtime.triggered_procs_event.is_set():
                await asyncio.sleep(0)
            # Let run() enter its wait after clearing the initial triggers.
            await asyncio.sleep(0)
            runtime.assert_value_is('reached', wirely.Some(False))
            steps = runtime.step_count
            await asyncio.sleep(0)
            assert runtime.step_count == steps
            if mode == 'generation':
                runtime.advance_fake_monotonic_time_generation()
            elif mode == 'enable':
                runtime.enable_fixed_monotonic_time()
            else:
                runtime.advance_fake_monotonic_time(span)
            expected = None if mode in ('generation', 'enable') else wirely.Some(True)
            while runtime.get_value('reached') != expected:
                await asyncio.sleep(0)
                if run_task.done():
                    run_task.result()
            assert runtime.next_wakeup is None
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task


async def run_test():
    await check_wakeup_passed_during_step()
    await check_wakeup_with_fake_time_offset()
    for mode in ('advance', 'generation', 'enable', 'offset'):
        await check_clock_change_wakes_waiting_engine(mode)
