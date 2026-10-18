import datetime

import wirely
from wirely.generated import schedule as contracts
from wirely.program import RunStage
from wirely.runtime.native_api import ProcedureServices


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
            services.set_wall_wakeup_time(start, start + datetime.timedelta(minutes=1))
            return contracts.TimeOfDayMinuteGetValueResult(
                output=wirely.Some(wirely.TimeOfDay.from_parts(local.hour, local.minute)))
