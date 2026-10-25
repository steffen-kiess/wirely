import asyncio
import datetime
import contextlib

import wirely
from wirely.program.program import RunStage
from tests.infra.support import internal


def set_time(value):
    engine.set_fake_wall_time(datetime.datetime.fromisoformat('2026-01-01T' + value).replace(
        tzinfo=datetime.timezone.utc))


async def run_test():
    # TODO: Move more of this functionality to test.py?
    # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
    async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
        await engine.start_tasks_async(tg, async_exit_stack)

        engine.get_native_state('delay_input.create').set_value(4)
        engine.get_native_state('reached_input.create').set_value(None)
        engine.get_native_state('elapsed_input.create').set_value(None)

        engine.step()

        engine.assert_value_is('delay', 4)

        now_time = datetime.datetime.now().time()
        hour = now_time.hour
        if hour >= 1 and hour < 11:
            engine.assert_value_is('t0', wirely.Some(True))
            engine.assert_value_is('t1', wirely.Some(False))
        if hour >= 13 and hour < 23:
            engine.assert_value_is('t0', wirely.Some(False))
            engine.assert_value_is('t1', wirely.Some(True))

        engine.set_fake_wall_time(None)
        engine.step()
        engine.step()
        engine.assert_is_idle()
        for name in ('t0', 't1', 'overnight'):
            engine.assert_value_is(name, None)
        engine.assert_value_is('all_day', wirely.Some(True))
        engine.assert_value_is('never', wirely.Some(False))

        # Note: There are 2 engine.step() calls because if the first call changes the TriggerAt state, TriggerAt.run() will be re-run.

        set_time('00:00')
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', wirely.Some(True))
        engine.assert_value_is('t1', wirely.Some(False))
        engine.assert_value_is('all_day', wirely.Some(True))
        engine.assert_value_is('never', wirely.Some(False))

        set_time('04:34')
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', wirely.Some(True))
        engine.assert_value_is('t1', wirely.Some(False))

        set_time('11:59')
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', wirely.Some(True))
        engine.assert_value_is('t1', wirely.Some(False))

        set_time('12:00')
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', wirely.Some(False))
        engine.assert_value_is('t1', wirely.Some(True))
        engine.assert_value_is('all_day', wirely.Some(True))
        engine.assert_value_is('never', wirely.Some(False))

        set_time('13:45')
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', wirely.Some(False))
        engine.assert_value_is('t1', wirely.Some(True))

        set_time('23:58')
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', wirely.Some(False))
        engine.assert_value_is('t1', wirely.Some(True))

        set_time('23:59')
        engine.step()
        engine.step(); engine.assert_is_idle()
        engine.assert_value_is('t0', wirely.Some(False))
        engine.assert_value_is('t1', wirely.Some(True))
        engine.assert_value_is('all_day', wirely.Some(True))
        engine.assert_value_is('never', wirely.Some(False))

        # The interval includes its start and excludes its end across midnight.
        for time, expected in [('21:59', False), ('22:00', True), ('23:00', True),
                               ('00:00', True), ('05:59', True), ('06:00', False),
                               ('12:00', False)]:
            set_time(time)
            engine.step()
            engine.step()
            engine.assert_value_is('overnight', wirely.Some(expected))
            engine.assert_is_idle()

        # Test TriggerAt

        set_time('12:33')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:34')
        engine.step()
        engine.assert_value_is('trigger', wirely.Some(wirely.Unit()))
        engine.assert_value_is('trigger_value', wirely.Some(True))

        set_time('12:35')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:30')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('18:00')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:30')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', wirely.Some(wirely.Unit()))
        engine.assert_value_is('trigger_value', wirely.Some(True))

        set_time('18:00')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:30')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', wirely.Some(wirely.Unit()))
        engine.assert_value_is('trigger_value', wirely.Some(True))

        set_time('12:37')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:30')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:37')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', wirely.Some(wirely.Unit()))
        engine.assert_value_is('trigger_value', wirely.Some(True))

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # Test: none, after, before, after (should not trigger)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # Test: none, none, after, before, after (should not trigger)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('18:06')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # Test: none, before, none, before, after (should trigger)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', wirely.Some(wirely.Unit()))
        engine.assert_value_is('trigger_value', wirely.Some(True))

        # Test: none, before, none, after (should not trigger)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:31')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('18:05')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        set_time('12:36')
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_value_is('trigger_value', None)

        # TODO: Tests for trigger2

        # Test MonotonicTimestamp before advancing fake time.
        engine.assert_value_is('timestamp', None)
        engine.get_native_state('timestamp_input.create').send_event(wirely.Some(wirely.Unit()))
        _, _, step_info = engine.step()
        timestamp = engine.get_value('timestamp')
        assert isinstance(timestamp, wirely.Some)
        now = wirely.MonotonicTime.current_time()
        stamp = step_info.stage_start_time_monotonic[RunStage.DEFAULT]
        assert timestamp.value == internal(stamp)
        assert wirely.MonotonicTime.is_comparable(stamp, now)
        assert wirely.MonotonicTime.compare(stamp, now) <= 0
        engine.step()
        engine.assert_value_is('timestamp', None)
        engine.assert_is_idle()

        # Tests for Timer.DebounceStable

        engine.assert_value_is('delay', 4)

        engine.get_native_state('delay_input.create').set_value(5)
        engine.step()
        engine.assert_value_is('delay', 4)

        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('delay', 4)

        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.get_native_state('delay_input.create').set_value(17)
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.get_native_state('delay_input.create').set_value(18)
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('delay', 5)

        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('delay', 18)

        # TODO: Add test for generation change?

        # MonotonicTimeReached: None and non-comparable times give None.
        reached_input = engine.get_native_state('reached_input.create')
        now = wirely.MonotonicTime.current_time()
        for time, expected in [(None, None), (wirely.Some(now + wirely.Duration.parse('PT1H')), wirely.Some(False)),
                               (wirely.Some(wirely.MonotonicTime(generation=now.generation, ticks=0)), wirely.Some(True)),
                               (wirely.Some(wirely.MonotonicTime(generation=0, ticks=0)), None)]:
            reached_input.set_value(internal(time))
            engine.step()
            engine.assert_value_is('reached', expected)

        # DurationElapsed preserves absence and rejects lost clock generations.
        elapsed_input = engine.get_native_state('elapsed_input.create')
        for start in (None, wirely.Some(wirely.MonotonicTime(generation=0, ticks=0))):
            elapsed_input.set_value(internal(start))
            engine.step()
            engine.assert_value_is('elapsed', None)
            engine.assert_value_is('elapsed_zero', None)

        # Advancing time expires the wait without changing its input.
        _, _, step_info = engine.step()
        start = step_info.stage_start_time_monotonic[RunStage.DEFAULT]
        elapsed_input.set_value(internal(wirely.Some(start)))
        engine.step()
        engine.assert_value_is('elapsed', wirely.Some(False))
        engine.assert_value_is('elapsed_zero', wirely.Some(True))
        engine.assert_is_idle()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('elapsed', wirely.Some(False))
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('elapsed', wirely.Some(True))
        engine.assert_is_idle()

        # Replacing the start time restarts a pending wait.
        _, _, step_info = engine.step()
        elapsed_input.set_value(internal(wirely.Some(step_info.stage_start_time_monotonic[RunStage.DEFAULT])))
        engine.step()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
        _, _, step_info = engine.step()
        elapsed_input.set_value(internal(wirely.Some(step_info.stage_start_time_monotonic[RunStage.DEFAULT])))
        engine.step()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
        engine.step()
        engine.assert_value_is('elapsed', wirely.Some(False))
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(9))
        engine.step()
        engine.assert_value_is('elapsed', wirely.Some(True))

        # A future start with zero duration waits until that start is reached.
        _, _, step_info = engine.step()
        start = step_info.stage_start_time_monotonic[RunStage.DEFAULT]
        elapsed_input.set_value(internal(wirely.Some(start + wirely.Duration.from_seconds(10))))
        engine.step()
        engine.assert_value_is('elapsed_zero', wirely.Some(False))
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(11))
        engine.step()
        engine.assert_value_is('elapsed_zero', wirely.Some(True))
        engine.assert_value_is('elapsed', wirely.Some(False))

        # Clearing a pending wait keeps it absent after its deadline passes.
        elapsed_input.set_value(None)
        engine.step()
        engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(11))
        engine.step()
        engine.assert_value_is('elapsed', None)
        engine.assert_value_is('elapsed_zero', None)
        engine.assert_is_idle()

        # Adding the duration near the timestamp limit saturates, not overflows.
        elapsed_input.set_value(internal(wirely.Some(wirely.MonotonicTime(generation=start.generation, ticks=wirely.MonotonicTime.MAX_TICKS - 5))))
        engine.step()
        engine.assert_value_is('elapsed', wirely.Some(False))
        elapsed_input.set_value(None)
        engine.step()
        engine.assert_is_idle()
