import asyncio
import contextlib

import wirely


async def run_test():
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
        engine.get_native_state('reached_input').set_value(wirely.Some(now + wirely.Duration.from_seconds(0.5)))
        run_task = asyncio.create_task(engine.run())
        try:
            for _ in range(100):
                if run_task.done() or engine.step_count >= 2:
                    break
                await asyncio.sleep(0)
            if run_task.done():
                run_task.result()
            assert engine.step_count >= 2
            engine.assert_value_is('reached', wirely.Some(True))
        finally:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
    finally:
        wirely.MonotonicTime.current_time = original_current_time
        del engine.step
