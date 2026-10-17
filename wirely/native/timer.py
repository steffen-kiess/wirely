from typing import Any

import wirely
from wirely import DynamicValue, Some, ValueType
from wirely.contracts import timer as contracts
from wirely.native import native_node_type, native_procedure
from wirely.program import RunStage
from wirely.runtime.native_api import PreparationServices, ProcedureServices

native_node_types: dict[str, type] = {}


@native_node_type(native_node_types, has_native_state=False)
class MonotonicTimestamp:
    @native_procedure('run')
    class Run(contracts.MonotonicTimestampRun):
        def prepare(self, *, services: PreparationServices, type: ValueType[Any]) -> None:
            pass

        def run(self, *, services: ProcedureServices,
                input: Some[DynamicValue] | None) -> contracts.MonotonicTimestampRunResult:
            if input is None:
                return contracts.MonotonicTimestampRunResult(output=None)
            services.trigger_self()
            return contracts.MonotonicTimestampRunResult(
                output=wirely.Some(services.start_time_for_stage(RunStage.DEFAULT).monotonic))


@native_node_type(native_node_types, has_native_state=False)
class MonotonicTimeReached:
    @native_procedure('run')
    class Run(contracts.MonotonicTimeReachedRun):
        def run(self, *, services: ProcedureServices,
                input: Some[wirely.MonotonicTime] | None) -> contracts.MonotonicTimeReachedRunResult:
            now = services.start_time_for_stage(RunStage.DEFAULT).monotonic

            time = None if input is None else input.value
            result: Some[bool] | None
            if time is None or not wirely.MonotonicTime.is_comparable(time, now):
                result = None
                services.set_wakeup_time(None)
            elif wirely.MonotonicTime.compare(now, time) >= 0:
                result = wirely.Some(True)
                services.set_wakeup_time(None)
            else:
                result = wirely.Some(False)
                services.set_wakeup_time(time)

            return contracts.MonotonicTimeReachedRunResult(output=result)
