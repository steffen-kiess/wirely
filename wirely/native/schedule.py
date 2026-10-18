import datetime

import wirely
from wirely.generated import schedule as contracts
from wirely.program import RunStage
from wirely.runtime.native_api import PreparationServices, ProcedureServices
from wirely.types.time import TICKS_PER_SECOND


class IsInDailyInterval:

    class Run(contracts.IsInDailyIntervalRun):
        def prepare(self, *, services: PreparationServices, start: wirely.TimeOfDay, end: wirely.TimeOfDay) -> None:
            self._constant = (start.ticks == 0) if {start.ticks, end.ticks} == {0, wirely.TimeOfDay.END_TICKS} else None
            self._wraps = start > end
            # A boundary takes effect at the first representable snapshot at or after it.
            self._start_us = (start.ticks * 1_000_000 + TICKS_PER_SECOND - 1) // TICKS_PER_SECOND
            self._end_us = (end.ticks * 1_000_000 + TICKS_PER_SECOND - 1) // TICKS_PER_SECOND
            self._day_us = 86_400 * 1_000_000
            self._boundaries = {self._start_us % self._day_us, self._end_us % self._day_us}

        def run(self, *, services: ProcedureServices) -> contracts.IsInDailyIntervalRunResult:
            if self._constant is not None:
                return contracts.IsInDailyIntervalRunResult(output=wirely.Some(self._constant))
            snapshot = services.start_time_for_stage(RunStage.DEFAULT)
            local = snapshot.local
            if local is None:
                services.set_wall_wakeup_time()
                return contracts.IsInDailyIntervalRunResult(output=None)
            assert snapshot.utc is not None
            current_us = ((local.hour * 60 + local.minute) * 60 + local.second) * 1_000_000 + local.microsecond
            inside = (current_us >= self._start_us or current_us < self._end_us) if self._wraps else (
                self._start_us <= current_us < self._end_us)
            boundaries = [day * self._day_us + boundary for day in (-1, 0, 1) for boundary in self._boundaries]
            start_us = max(boundary for boundary in boundaries if boundary <= current_us)
            end_us = min(boundary for boundary in boundaries if boundary > current_us)
            # Project with the current offset; a later offset change invalidates this window.
            services.set_wall_wakeup_time(
                snapshot.utc + datetime.timedelta(microseconds=start_us - current_us),
                snapshot.utc + datetime.timedelta(microseconds=end_us - current_us),
                utc_offset=local.utcoffset())
            return contracts.IsInDailyIntervalRunResult(output=wirely.Some(inside))


class TimeOfDayMinute:

    class GetValue(contracts.TimeOfDayMinuteGetValue):
        def run(self, *, services: ProcedureServices) -> contracts.TimeOfDayMinuteGetValueResult:
            snapshot = services.start_time_for_stage(RunStage.DEFAULT)
            local = snapshot.local
            if local is None:
                services.set_wall_wakeup_time()
                return contracts.TimeOfDayMinuteGetValueResult(output=None)
            assert snapshot.utc is not None
            elapsed = datetime.timedelta(seconds=local.second, microseconds=local.microsecond)
            start = snapshot.utc - elapsed
            services.set_wall_wakeup_time(start, start + datetime.timedelta(minutes=1),
                                          utc_offset=local.utcoffset())
            return contracts.TimeOfDayMinuteGetValueResult(
                output=wirely.Some(wirely.TimeOfDay.from_parts(local.hour, local.minute)))
