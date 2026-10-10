import asyncio
import contextlib

import wirely


async def run_engine_until(condition, *, timeout_s):
    run_task = asyncio.create_task(engine.run())
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
    # The clock only advances by 1 s after each step, so the wakeup deadline
    # is in the future while the step runs and has passed when run() reads
    # the clock afterwards.
    now = wirely.MonotonicTime.current_time()
    original_current_time = wirely.MonotonicTime.current_time
    original_step = engine.step

    def step():
        nonlocal now
        result = original_step()
        now += wirely.Duration.from_seconds(1)
        return result

    wirely.MonotonicTime.current_time = staticmethod(lambda: now)
    engine.step = step
    try:
        steps = engine.step_count
        engine.get_native_state('reached_input').set_value(wirely.Some(now + wirely.Duration.from_seconds(0.5)))
        await run_engine_until(lambda: engine.step_count >= steps + 2, timeout_s=1)
        engine.assert_value_is('reached', wirely.Some(True))
    finally:
        wirely.MonotonicTime.current_time = original_current_time
        del engine.step


async def check_wakeup_with_fake_time_offset():
    # The deadline is reached shortly after the start in fake time, but only
    # after an hour in real time.
    offset = wirely.Duration.from_seconds(3600)
    engine.advance_fake_monotonic_time(offset)
    try:
        steps = engine.step_count
        deadline = wirely.MonotonicTime.current_time() + offset + wirely.Duration.from_seconds(0.1)
        engine.get_native_state('reached_input').set_value(wirely.Some(deadline))
        await run_engine_until(
            lambda: engine.step_count > steps and engine.get_value('reached') == wirely.Some(True), timeout_s=5)
    finally:
        engine.fake_monotonic_time_offset = None


async def run_test():
    await check_wakeup_passed_during_step()
    await check_wakeup_with_fake_time_offset()
