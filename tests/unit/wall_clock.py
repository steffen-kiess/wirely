import asyncio
import contextlib
import datetime
import errno
import os
import sys
from unittest import TestCase
from zoneinfo import ZoneInfo

import wirely
from wirely.compiler.compilation import compile_strs
from wirely.compiler.sources import standard_library_sources
from wirely.compiler.program import compile_nocatch
from wirely.generated import extension
from wirely.generated import schedule as contracts
from wirely.native.schedule import IsInDailyIntervalRun, TimeOfDayMinuteGetValue
from wirely.program.program import RunStage
from wirely.runtime.engine import Engine
from wirely.runtime.loading import LoadedProgram
from wirely.runtime.native_api import ProcedureServices
from wirely.runtime.wall_clock import TimerFD, WallClockWaiter
from tests.infra.support import internal, test_contracts, test_extension


checks = TestCase()
UTC = datetime.timezone.utc
BASE = datetime.datetime(2026, 1, 1, 12, 34, 30, tzinfo=UTC)


def make_engine(callback=None):
    probes = []

    class Probe(TimeOfDayMinuteGetValue):
        def __init__(self):
            self.calls = 0
            self.snapshots = []
            probes.append(self)

        def run(self, *, services):
            self.calls += 1
            self.snapshots.append(services.start_time_for_stage(RunStage.DEFAULT))
            if callback is not None:
                callback(services, self.calls)
                return contracts.TimeOfDayMinuteGetValueResult(output=None)
            return super().run(services=services)

    graph = compile_strs([*standard_library_sources(['std', 'schedule']),
                         ('<wall-clock>', 'node clock: Schedule.TimeOfDayMinute {}')])
    assert graph is not None
    bindings = test_extension(procedures={'Schedule.TimeOfDayMinute:get_value': Probe})
    runtime = Engine(LoadedProgram.load(compile_nocatch(graph), override_extensions=[bindings]))
    return runtime, probes[0]


@contextlib.asynccontextmanager
async def running(runtime):
    task = asyncio.create_task(runtime.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def wait_for(task, condition):
    async with asyncio.timeout(3):
        while not condition():
            if task.done():
                task.result()
                raise AssertionError('Engine stopped')
            await asyncio.sleep(0)


async def unchanged(probe, calls):
    for _ in range(5):
        await asyncio.sleep(0)
    assert probe.calls == calls


def check_validation():
    def services():
        return ProcedureServices(name='clock', stage_times={}, trigger_self=lambda: None,
                                 native_types=extension.native_types)

    for start, end in ((BASE, None), (None, BASE),
                       (BASE.replace(tzinfo=None), BASE), (BASE, BASE),
                       (BASE, BASE - datetime.timedelta(seconds=1))):
        with checks.assertRaises(ValueError):
            services().set_wall_wakeup_time(start, end, utc_offset=None)
    for bounds in ((), (BASE, BASE + datetime.timedelta(minutes=1))):
        invocation = services()
        invocation.set_wall_wakeup_time(*bounds, **({'utc_offset': None} if bounds else {}))
        with checks.assertRaisesRegex(ValueError, 'already set'):
            invocation.set_wall_wakeup_time()

    with checks.assertRaisesRegex(ValueError, 'require utc_offset'):
        services().set_wall_wakeup_time(BASE, BASE + datetime.timedelta(minutes=1))
    for offset in (None, datetime.timedelta(0)):
        with checks.assertRaisesRegex(ValueError, 'do not accept an offset'):
            services().set_wall_wakeup_time(utc_offset=offset)
    for offset in (0, 'UTC', UTC):
        with checks.assertRaisesRegex(ValueError, 'require utc_offset'):
            services().set_wall_wakeup_time(BASE, BASE + datetime.timedelta(minutes=1), utc_offset=offset)

    # Endpoint representations do not choose the offset baseline.
    end = BASE + datetime.timedelta(minutes=1)
    shifted = datetime.timezone(datetime.timedelta(hours=2))
    first, second = services(), services()
    first.set_wall_wakeup_time(BASE, end, utc_offset=None)
    second.set_wall_wakeup_time(BASE.astimezone(shifted), end.astimezone(shifted), utc_offset=None)
    assert first.wall_wakeup == second.wall_wakeup

    # Endpoints are ordered as instants, including repeated local times.
    berlin = ZoneInfo('Europe/Berlin')
    start = datetime.datetime(2026, 10, 25, 2, 45, tzinfo=berlin, fold=0)
    end = datetime.datetime(2026, 10, 25, 2, 15, tzinfo=berlin, fold=1)
    services().set_wall_wakeup_time(start, end, utc_offset=None)
    with checks.assertRaises(ValueError):
        services().set_wall_wakeup_time(end, start, utc_offset=None)


async def check_windows_and_availability():
    runtime, probe = make_engine()
    other, other_probe = make_engine()
    other.set_fake_wall_time(BASE)
    other.step()
    runtime.set_fake_wall_time(BASE)
    async with running(runtime) as task:
        await wait_for(task, lambda: probe.calls == 1)
        runtime.assert_value_is('clock', internal(wirely.Some(wirely.TimeOfDay.from_parts(12, 34))))
        for value in (BASE - datetime.timedelta(seconds=2), BASE.replace(second=0), BASE.replace(second=59)):
            runtime.set_fake_wall_time(value)
            await unchanged(probe, 1)
        runtime.set_fake_wall_time(BASE - datetime.timedelta(minutes=2))
        await wait_for(task, lambda: probe.calls == 2)
        runtime.assert_value_is('clock', internal(wirely.Some(wirely.TimeOfDay.from_parts(12, 32))))
        runtime.set_fake_wall_time(BASE.replace(minute=33, second=0))
        await wait_for(task, lambda: probe.calls == 3)
        shifted = BASE.replace(minute=33, second=0).astimezone(datetime.timezone(datetime.timedelta(hours=1)))
        runtime.set_fake_wall_time(shifted)
        await wait_for(task, lambda: probe.calls == 4)
        runtime.assert_value_is('clock', internal(wirely.Some(wirely.TimeOfDay.from_parts(13, 33))))
        runtime.set_fake_wall_time(None)
        await wait_for(task, lambda: probe.calls == 5)
        runtime.assert_value_is('clock', None)
        assert probe.snapshots[-1].utc is None and probe.snapshots[-1].local is None
        runtime.set_fake_wall_time(None)
        await unchanged(probe, 5)
        runtime.set_fake_wall_time(BASE)
        await wait_for(task, lambda: probe.calls == 6)
        runtime.clear_fake_wall_time()
        await wait_for(task, lambda: probe.calls == 7)
        assert other_probe.calls == 1
        other.assert_is_idle()

    unknown, probe = make_engine()
    unknown.set_fake_wall_time(None)
    async with running(unknown) as task:
        await wait_for(task, lambda: probe.calls == 1)
        await unchanged(probe, 1)
        unknown.set_fake_wall_time(BASE)
        await wait_for(task, lambda: probe.calls == 2)

    berlin = ZoneInfo('Europe/Berlin')
    runtime, probe = make_engine()
    runtime.set_fake_wall_time(datetime.datetime(2026, 10, 25, 2, 59, 59, tzinfo=berlin, fold=0))
    runtime.step()
    runtime.set_fake_wall_time(datetime.datetime(2026, 10, 25, 2, tzinfo=berlin, fold=1))
    runtime.step()
    assert probe.calls == 2
    runtime.assert_value_is('clock', internal(wirely.Some(wirely.TimeOfDay.from_parts(2, 0))))


def check_daily_intervals():
    def interval(start, end):
        probes = []

        class Probe(IsInDailyIntervalRun):
            def __init__(self):
                self.calls = 0
                probes.append(self)

            def run(self, *, services, start, end):
                self.calls += 1
                return super().run(services=services, start=start, end=end)

        graph = compile_strs([*standard_library_sources(['std', 'schedule']), ('<interval>', f'''
node interval: Schedule.IsInDailyInterval {{ Start: "{start}", End: "{end}" }}
''')])
        assert graph is not None
        bindings = test_extension(procedures={'Schedule.IsInDailyInterval:run': Probe})
        runtime = Engine(LoadedProgram.load(compile_nocatch(graph), override_extensions=[bindings]))

        def step(value, expected, calls):
            if isinstance(value, str):
                value = datetime.datetime.fromisoformat(value).replace(tzinfo=UTC)
            runtime.set_fake_wall_time(value)
            runtime.step()
            runtime.assert_value_is('interval', None if expected is None else wirely.Some(expected))
            assert probes[0].calls == calls
            runtime.assert_is_idle()

        return runtime, step

    runtime, step = interval('08:00', '12:00')
    step('2026-01-01T07:59:59', False, 1)
    step('2026-01-01T07:59:58', False, 1)
    step('2026-01-01T08:00', True, 2)
    step('2026-01-01T09:00', True, 2)
    step('2026-01-01T12:00', False, 3)
    step('2026-01-01T09:00', True, 4)
    step('2025-12-31T23:00', False, 5)
    step('2026-01-02T07:00', False, 6)
    step(None, None, 7)
    step(None, None, 7)
    step('2026-01-02T09:00', True, 8)

    runtime, step = interval('08:00', '12:00')
    instant = BASE.replace(hour=10)
    step(instant, True, 1)
    # The UTC instant stays inside the window, but its local offset changes.
    step(instant.astimezone(datetime.timezone(datetime.timedelta(hours=1))), True, 2)
    step(instant.astimezone(datetime.timezone(datetime.timedelta(hours=3))), False, 3)

    runtime, step = interval('22:00', '06:00')
    step('2026-01-01T21:59:59', False, 1)
    step('2026-01-01T22:00', True, 2)
    step('2026-01-01T23:00', True, 2)
    step('2026-01-02T00:00', True, 2)
    step('2026-01-02T05:59:59', True, 2)
    step('2026-01-02T06:00', False, 3)
    step('2026-01-02T00:00', True, 4)

    for start, end in (('00:00', '12:00'), ('24:00', '12:00')):
        runtime, step = interval(start, end)
        step('2026-01-01T23:59:59.999999', False, 1)
        step('2026-01-02T00:00', True, 2)
        step('2026-01-01T23:59:59.999999', False, 3)
    for start, end in (('12:00', '24:00'), ('12:00', '00:00')):
        runtime, step = interval(start, end)
        step('2026-01-01T23:59:59.999999', True, 1)
        step('2026-01-02T00:00', False, 2)

    for start, end, expected in (('00:00', '24:00', True), ('24:00', '00:00', False)):
        runtime, step = interval(start, end)
        for value in (None, BASE, BASE.astimezone(datetime.timezone(datetime.timedelta(hours=2))), None):
            step(value, expected, 1)
            assert not runtime.wall_wakeups

    runtime, step = interval('08:00:00.5', '08:00:01.5')
    step('2026-01-01T08:00:00.499999', False, 1)
    step('2026-01-01T08:00:00.500000', True, 2)
    step('2026-01-01T08:00:01.499999', True, 2)
    step('2026-01-01T08:00:01.500000', False, 3)

    # Exact binary fractions fall between snapshots; neither boundary may fire early.
    runtime, step = interval('08:00:00.00000095367431640625', '08:00:00.0000019073486328125')
    step('2026-01-01T08:00:00', False, 1)
    step('2026-01-01T08:00:00.000001', True, 2)
    step('2026-01-01T08:00:00.000002', False, 3)

    # Two distinct boundaries can map to the same representable microsecond.
    runtime, step = interval('08:00:00.00000000000000000005', '08:00:00.0000000000000000001')
    step('2026-01-01T08:00:00', False, 1)
    step('2026-01-01T08:00:00.000001', False, 2)

    berlin = ZoneInfo('Europe/Berlin')
    runtime, step = interval('02:30', '03:30')
    step(datetime.datetime(2026, 10, 25, 2, 45, tzinfo=berlin, fold=0), True, 1)
    step(datetime.datetime(2026, 10, 25, 2, 45, tzinfo=berlin, fold=1), True, 2)
    step(datetime.datetime(2026, 10, 25, 2, 15, tzinfo=berlin, fold=1), False, 3)
    step(datetime.datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1), True, 4)
    runtime, step = interval('02:30', '03:30')
    step(datetime.datetime(2026, 3, 29, 1, 59, tzinfo=berlin), False, 1)
    step(datetime.datetime(2026, 3, 29, 3, 0, tzinfo=berlin), True, 2)
    step(datetime.datetime(2026, 3, 29, 3, 30, tzinfo=berlin), False, 3)


def check_explicit_offsets():
    for baseline in (datetime.timedelta(hours=1), None):
        def window(services, calls):
            local = services.start_time_for_stage(RunStage.DEFAULT).local
            if local is None:
                services.set_wall_wakeup_time()
            else:
                offset = baseline if calls == 1 else (None if baseline is None else local.utcoffset())
                services.set_wall_wakeup_time(BASE, BASE + datetime.timedelta(minutes=1), utc_offset=offset)

        runtime, probe = make_engine(window)
        runtime.set_fake_wall_time(BASE)
        runtime.step()
        assert next(iter(runtime.wall_wakeups.values())).utc_offset == baseline
        # A caller-selected baseline can already disagree with the stage snapshot.
        runtime.step()
        assert probe.calls == (1 if baseline is None else 2)

        before = probe.calls
        runtime.set_fake_wall_time(BASE.astimezone(datetime.timezone(datetime.timedelta(hours=2))))
        runtime.step()
        assert probe.calls == before + (0 if baseline is None else 1)

        # Ignoring offsets still observes expiration and loss of wall time.
        before = probe.calls
        runtime.set_fake_wall_time(BASE + datetime.timedelta(minutes=1))
        runtime.step()
        assert probe.calls == before + 1
        runtime.set_fake_wall_time(None)
        runtime.step()
        assert probe.calls == before + 2
        runtime.assert_is_idle()


async def check_one_shot_requests():
    def available(services, calls):
        if calls == 1:
            services.set_wall_wakeup_time()

    runtime, probe = make_engine(available)
    runtime.set_fake_wall_time(BASE)
    async with running(runtime) as task:
        await wait_for(task, lambda: probe.calls == 2)
        runtime.set_fake_wall_time(None)
        await unchanged(probe, 2)

    def outside(services, calls):
        if calls == 1:
            services.set_wall_wakeup_time(BASE + datetime.timedelta(seconds=1), BASE + datetime.timedelta(seconds=2),
                                          utc_offset=None)

    runtime, probe = make_engine(outside)
    runtime.set_fake_wall_time(BASE)
    async with running(runtime) as task:
        await wait_for(task, lambda: probe.calls == 2)
        await unchanged(probe, 2)

    # Either clock can wake the same procedure; omitting both requests removes them.
    for clock in ('monotonic', 'wall'):
        def both(services, calls):
            if calls == 1:
                now = services.start_time_for_stage(RunStage.DEFAULT)
                services.set_wakeup_time(now.monotonic + wirely.Duration.from_seconds(3600))
                services.set_wall_wakeup_time(BASE, BASE + datetime.timedelta(seconds=1), utc_offset=None)

        runtime, probe = make_engine(both)
        runtime.enable_fixed_monotonic_time()
        runtime.set_fake_wall_time(BASE)
        async with running(runtime) as task:
            await wait_for(task, lambda: probe.calls == 1)
            if clock == 'monotonic':
                runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(3600))
            else:
                runtime.set_fake_wall_time(BASE + datetime.timedelta(seconds=1))
            await wait_for(task, lambda: probe.calls == 2)
            runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(3600))
            runtime.set_fake_wall_time(None)
            await unchanged(probe, 2)


async def check_real_timer():
    def deadline(services, calls):
        if calls == 1:
            now = services.start_time_for_stage(RunStage.DEFAULT).utc
            services.set_wall_wakeup_time(now - datetime.timedelta(seconds=1), now + datetime.timedelta(milliseconds=30),
                                          utc_offset=None)

    runtime, probe = make_engine(deadline)
    async with running(runtime) as task:
        await wait_for(task, lambda: probe.calls == 2)


async def check_mixed_requests():
    try:
        timer = TimerFD()
    except NotImplementedError:
        return
    timer.close()
    graph = compile_strs([*standard_library_sources(['std', 'schedule']), ('<mixed-clocks>', '''
namespace TestLibrary {
  node type Clock {
    default output: value Option[Schedule.TimeOfDay];
    native procedure run writes default output as "output";
  }
}
node utc: TestLibrary.Clock {}
node local: TestLibrary.Clock {}
''')])
    assert graph is not None
    invocations = {}
    monitor_local = True
    with test_contracts(graph) as contracts:
        class Clock(contracts.ClockRun):
            def run(self, *, services):
                invocations[services.name] = services
                snapshot = services.start_time_for_stage(RunStage.DEFAULT)
                if services.name == 'utc' or monitor_local:
                    services.set_wall_wakeup_time(
                        snapshot.utc - datetime.timedelta(hours=1), snapshot.utc + datetime.timedelta(hours=1),
                        utc_offset=None if services.name == 'utc' else snapshot.local.utcoffset())
                return contracts.ClockRunResult(output=None)

        bindings = test_extension(contracts=contracts, procedures={'TestLibrary.Clock:run': Clock})
        runtime = Engine(LoadedProgram.load(compile_nocatch(graph), override_extensions=[bindings]))

    reads = 0
    get_time = runtime.get_wall_time

    def read_time():
        nonlocal reads
        reads += 1
        return get_time()

    runtime.get_wall_time = read_time
    async with running(runtime) as task:
        await wait_for(task, lambda: len(invocations) == 2)
        await asyncio.sleep(0)
        steps, initial_reads = runtime.step_count, reads
        await wait_for(task, lambda: reads > initial_reads)
        # Native offset polling reads the clock without stepping the engine.
        assert runtime.step_count == steps

        monitor_local = False
        invocations['local'].trigger_self()
        await wait_for(task, lambda: len(runtime.wall_wakeups) == 1)
        await asyncio.sleep(0)
        # Removing the last offset subscription leaves only the native UTC timer.
        initial_reads = reads
        await asyncio.sleep(1.1)
        assert reads == initial_reads


async def check_timer_backends():
    if sys.platform == 'linux':
        for use_libc in (False, True):
            try:
                timer = TimerFD(use_libc=use_libc)
            except NotImplementedError:
                continue
            fd = timer.fd
            ready = asyncio.Event()
            loop = asyncio.get_running_loop()
            try:
                timer.arm(datetime.datetime.now(UTC) + datetime.timedelta(milliseconds=20))
                loop.add_reader(fd, ready.set)
                async with asyncio.timeout(3):
                    await ready.wait()
                loop.remove_reader(fd)
                assert timer.consume()
                assert not timer.consume()

                def cancelled(fd, size):
                    raise OSError(errno.ECANCELED, 'clock changed')

                timer._read = cancelled
                assert timer.consume()
            finally:
                loop.remove_reader(fd)
                timer.close()
            with checks.assertRaises(OSError):
                os.fstat(fd)

    def unavailable():
        raise NotImplementedError('Exercise portable polling')

    ready = asyncio.Event()
    waiter = WallClockWaiter(lambda: BASE, ready.set, timer_factory=unavailable)
    try:
        for monitor_offset in (False, True):
            ready.clear()
            waiter.arm(BASE + datetime.timedelta(minutes=1), real=True, now=BASE, monitor_offset=monitor_offset)
            async with asyncio.timeout(3):
                await ready.wait()
        # A close deadline is polled before the regular interval elapses.
        ready.clear()
        waiter.arm(BASE + datetime.timedelta(milliseconds=20), real=True, now=BASE, monitor_offset=False)
        async with asyncio.timeout(0.5):
            await ready.wait()
        waiter.arm(None, real=False, now=None, monitor_offset=False)
        ready.clear()
        await asyncio.sleep(0)
        assert not ready.is_set()
    finally:
        waiter.close()

    if sys.platform == 'linux':
        # Exercise clock-change notification through the reader callback,
        # without changing the host clock or patching shared functions.
        timers = []

        def cancelled_timer():
            timer = TimerFD()

            def cancelled(fd, size):
                raise OSError(errno.ECANCELED, 'clock changed')

            timer._read = cancelled
            timers.append(timer)
            return timer

        ready = asyncio.Event()
        now = datetime.datetime.now(UTC)
        waiter = WallClockWaiter(lambda: now, ready.set, timer_factory=cancelled_timer)
        try:
            waiter.arm(now + datetime.timedelta(milliseconds=20), real=True, now=now, monitor_offset=False)
            assert waiter._poll_handle is None
            async with asyncio.timeout(3):
                await ready.wait()
        finally:
            waiter.close()
        assert all(timer.fd == -1 for timer in timers)

        # UTC-offset changes invalidate even a distant, otherwise valid window.
        samples = [datetime.datetime.now(UTC)]
        ready = asyncio.Event()
        waiter = WallClockWaiter(lambda: samples[0], ready.set)
        try:
            waiter.arm(samples[0] + datetime.timedelta(hours=1), real=True, now=samples[0], monitor_offset=True)
            samples[0] = samples[0].astimezone(datetime.timezone(datetime.timedelta(hours=1)))
            async with asyncio.timeout(3):
                await ready.wait()
        finally:
            waiter.close()


async def run_test():
    check_validation()
    check_daily_intervals()
    check_explicit_offsets()
    await check_windows_and_availability()
    await check_one_shot_requests()
    await check_real_timer()
    await check_mixed_requests()
    await check_timer_backends()
