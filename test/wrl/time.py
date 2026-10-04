import asyncio
import datetime
import contextlib

import wirely as blocks


async def run_test():
    # TODO: Move more of this functionality to test.py?
    # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        engine.get_native_state('delay_input').set_value(4)

        engine.step()

        engine.assert_value_is('delay', 4)

        val0 = engine.get_value('t0')
        val1 = engine.get_value('t1')

        todm = engine.get_native_state_singleton('Time.TimeOfDayMinute')

        now_time = datetime.datetime.now().time()
        hour = now_time.hour
        if hour >= 1 and hour < 11:
            engine.assert_value_is('t0', True)
            engine.assert_value_is('t1', False)
        if hour >= 13 and hour < 23:
            engine.assert_value_is('t0', False)
            engine.assert_value_is('t1', True)

        # Note: There are 2 engine.step() calls because if the first call changes the TriggerAt state, TriggerAt.run() will be re-run.

        todm.set_fake_time(datetime.time.fromisoformat('00:00'))
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', True)
        engine.assert_value_is('t1', False)

        todm.set_fake_time(datetime.time.fromisoformat('04:34'))
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', True)
        engine.assert_value_is('t1', False)

        todm.set_fake_time(datetime.time.fromisoformat('11:59'))
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', True)
        engine.assert_value_is('t1', False)

        todm.set_fake_time(datetime.time.fromisoformat('12:00'))
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', False)
        engine.assert_value_is('t1', True)

        todm.set_fake_time(datetime.time.fromisoformat('13:45'))
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', False)
        engine.assert_value_is('t1', True)

        todm.set_fake_time(datetime.time.fromisoformat('23:58'))
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', False)
        engine.assert_value_is('t1', True)

        todm.set_fake_time(datetime.time.fromisoformat('23:59'))
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', False)
        # TODO: Fix test (and allow 24:00)
        # engine.assert_value_is('t1', True)
        engine.assert_value_is('t1', False)

        # Test TriggerAt

        todm.set_fake_time(datetime.time.fromisoformat('12:33'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:34'))
        engine.step()
        engine.assert_value_is('trigger', blocks.Some(blocks.Unit()))
        engine.assert_value_is('trigger_value', blocks.Some(True))

        todm.set_fake_time(datetime.time.fromisoformat('12:35'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:30'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('18:00'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:30'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', blocks.Some(blocks.Unit()))
        engine.assert_value_is('trigger_value', blocks.Some(True))

        todm.set_fake_time(datetime.time.fromisoformat('18:00'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:30'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', blocks.Some(blocks.Unit()))
        engine.assert_value_is('trigger_value', blocks.Some(True))

        todm.set_fake_time(datetime.time.fromisoformat('12:37'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:30'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:37'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', blocks.Some(blocks.Unit()))
        engine.assert_value_is('trigger_value', blocks.Some(True))

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # Test: none, after, before, after (should not trigger)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # Test: none, none, after, before, after (should not trigger)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('18:06'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # Test: none, before, none, before, after (should trigger)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', blocks.Some(blocks.Unit()))
        engine.assert_value_is('trigger_value', blocks.Some(True))

        # Test: none, before, none, after (should not trigger)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:31'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('18:05'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        todm.set_fake_time(datetime.time.fromisoformat('12:36'))
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # TODO: Tests for trigger2

        # Tests for Time.DebounceStable

        engine.assert_value_is('delay', 4)

        engine.get_native_state('delay_input').set_value(5)
        engine.step()
        engine.assert_value_is('delay', 4)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('delay', 4)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.get_native_state('delay_input').set_value(17)
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.get_native_state('delay_input').set_value(18)
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.advance_fake_monotonic_time(blocks.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('delay', 18)

        # TODO: Add test for generation change?

        # Without this sleep there is a warning: "sys:1: RuntimeWarning: coroutine 'TimeOfDayMinute.run_task' was never awaited"
        await asyncio.sleep(0.001)
