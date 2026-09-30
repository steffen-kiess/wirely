import asyncio
import datetime
import contextlib

import wirely as blocks


async def run_test():
    # TODO: Move more of this functionality to test.py?
    # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        inp = engine.get_native_state('in')
        feedback = engine.get_native_state('feedback')
        trigger = engine.get_native_state('trigger')

        def tst(exp, cmd=None, fdbck=()):
            if exp is not None:
                if exp != 1:
                    raise Exception('exp != 1')
                exp = (None,)

            if cmd is not None:
                inp.send_event((cmd,))
            if fdbck is not ():
                feedback.set_value(fdbck)
            engine.step()
            engine.assert_value_is('trigger', exp)
            engine.step()
            engine.assert_value_is('trigger', None)
            engine.assert_is_idle()

        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_is_idle()

        tst(cmd=True, exp=None)

        tst(fdbck=(False,), exp=None)

        tst(cmd=True, exp=1)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.2))
        tst(cmd=True, exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.9))
        tst(cmd=True, exp=1)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        tst(cmd=False, exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        tst(fdbck=(True,), cmd=False, exp=1)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.8))
        tst(cmd=False, exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.5))
        tst(exp=1)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.8))
        # Still within timeout
        tst(cmd=False, exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.1))
        # Feedback lost during timeout
        tst(fdbck=None, exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3))
        tst(exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.3))
        tst(fdbck=(True,), cmd=False, exp=1)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.1))
        # Within timeout
        tst(cmd=False, exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.1))
        # Feedback change should clear pending
        tst(fdbck=(False,), exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(0.1))
        # Feedback change should clear pending, event if feedback is changed back
        tst(fdbck=(True,), exp=None)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(3))
        # Pending should be clear
        tst(exp=None)
