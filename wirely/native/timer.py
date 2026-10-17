import wirely

from wirely.native import native_node_type, native_procedure
from wirely.program import RunStage
from wirely.runtime.contexts import NativeProcedure, NativeState

native_node_types: dict[str, type] = {}


@native_node_type(native_node_types, has_native_state=False)
class MonotonicTimestamp(NativeState):
    @native_procedure('run')
    class Run(NativeProcedure):
        def __call__(self, *, context):
            if context.inputs['input'] is None:
                context.set_output('output', None)
            else:
                context.set_output('output', wirely.Some(context.start_time_for_stage(RunStage.DEFAULT).monotonic))
                context.trigger_self()


@native_node_type(native_node_types, has_native_state=False)
class MonotonicTimeReached(NativeState):
    @native_procedure('run')
    class Run(NativeProcedure):
        def __call__(self, *, context):
            time = context.inputs['input']
            now = context.start_time_for_stage(RunStage.DEFAULT).monotonic

            # print('MonotonicTimeReached', time, now)
            if time is not None:
                time = time.value
            if time is None or not wirely.MonotonicTime.is_comparable(time, now):
                result = None
                context.set_wakeup_time(None)
            elif wirely.MonotonicTime.compare(now, time) >= 0:
                result = wirely.Some(True)
                context.set_wakeup_time(None)
            else:
                result = wirely.Some(False)
                context.set_wakeup_time(time)
            # print('MonotonicTimeReached', time, now, result)

            context.set_output('output', result)
