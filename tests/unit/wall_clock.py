import asyncio
import contextlib
import datetime
import errno
import os
import sys
from unittest import TestCase
from zoneinfo import ZoneInfo

import wirely
from wirely.compiler import compile_strs, standard_library_sources
from wirely.compiler.program import compile_nocatch
from wirely.generated import extension
from wirely.generated import schedule as contracts
from wirely.native.schedule import TimeOfDayMinute
from wirely.program import RunStage
from wirely.runtime.engine import Engine
from wirely.runtime.native_api import ProcedureServices
from wirely.runtime.wall_clock import TimerFD, WallClockWaiter
from tests.infra.support import test_extension


checks = TestCase()
UTC = datetime.timezone.utc
BASE = datetime.datetime(2026, 1, 1, 12, 34, 30, tzinfo=UTC)


def make_engine(callback=None):
    probes = []

    class Probe(TimeOfDayMinute.GetValue):
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
    runtime = Engine(compile_nocatch(graph), override_extensions=[bindings])
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
            services().set_wall_wakeup_time(start, end)
    for bounds in ((), (BASE, BASE + datetime.timedelta(minutes=1))):
        invocation = services()
        invocation.set_wall_wakeup_time(*bounds)
        with checks.assertRaisesRegex(ValueError, 'already set'):
            invocation.set_wall_wakeup_time()

    # Endpoints are ordered as instants, including repeated local times.
    berlin = ZoneInfo('Europe/Berlin')
    start = datetime.datetime(2026, 10, 25, 2, 45, tzinfo=berlin, fold=0)
    end = datetime.datetime(2026, 10, 25, 2, 15, tzinfo=berlin, fold=1)
    services().set_wall_wakeup_time(start, end)
    with checks.assertRaises(ValueError):
        services().set_wall_wakeup_time(end, start)


async def check_windows_and_availability():
    runtime, probe = make_engine()
    other, other_probe = make_engine()
    other.set_fake_wall_time(BASE)
    other.step()
    runtime.set_fake_wall_time(BASE)
    async with running(runtime) as task:
        await wait_for(task, lambda: probe.calls == 1)
        runtime.assert_value_is('clock', wirely.Some(wirely.TimeOfDay.from_parts(12, 34)))
        for value in (BASE - datetime.timedelta(seconds=2), BASE.replace(second=0), BASE.replace(second=59)):
            runtime.set_fake_wall_time(value)
            await unchanged(probe, 1)
        runtime.set_fake_wall_time(BASE - datetime.timedelta(minutes=2))
        await wait_for(task, lambda: probe.calls == 2)
        runtime.assert_value_is('clock', wirely.Some(wirely.TimeOfDay.from_parts(12, 32)))
        runtime.set_fake_wall_time(BASE.replace(minute=33, second=0))
        await wait_for(task, lambda: probe.calls == 3)
        shifted = BASE.replace(minute=33, second=0).astimezone(datetime.timezone(datetime.timedelta(hours=1)))
        runtime.set_fake_wall_time(shifted)
        await wait_for(task, lambda: probe.calls == 4)
        runtime.assert_value_is('clock', wirely.Some(wirely.TimeOfDay.from_parts(13, 33)))
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
    runtime.assert_value_is('clock', wirely.Some(wirely.TimeOfDay.from_parts(2, 0)))


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
            services.set_wall_wakeup_time(BASE + datetime.timedelta(seconds=1), BASE + datetime.timedelta(seconds=2))

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
                services.set_wall_wakeup_time(BASE, BASE + datetime.timedelta(seconds=1))

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
            services.set_wall_wakeup_time(now - datetime.timedelta(seconds=1), now + datetime.timedelta(milliseconds=30))

    runtime, probe = make_engine(deadline)
    async with running(runtime) as task:
        await wait_for(task, lambda: probe.calls == 2)


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
        waiter.arm(BASE + datetime.timedelta(minutes=1), real=True, now=BASE)
        async with asyncio.timeout(3):
            await ready.wait()
        waiter.arm(None, real=False, now=None)
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
            waiter.arm(now + datetime.timedelta(milliseconds=20), real=True, now=now)
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
            waiter.arm(samples[0] + datetime.timedelta(hours=1), real=True, now=samples[0])
            samples[0] = samples[0].astimezone(datetime.timezone(datetime.timedelta(hours=1)))
            async with asyncio.timeout(3):
                await ready.wait()
        finally:
            waiter.close()


async def run_test():
    check_validation()
    await check_windows_and_availability()
    await check_one_shot_requests()
    await check_real_timer()
    await check_timer_backends()
