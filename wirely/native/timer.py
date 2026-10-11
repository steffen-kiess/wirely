import wirely

from wirely.native import native_node_type
from wirely.program import RunStage
from wirely.runtime.contexts import NativeState

native_node_types = {}


@native_node_type(native_node_types, has_native_state=False)
class MonotonicTimeReached(NativeState):
    @staticmethod
    def run(*, context):
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


@native_node_type(native_node_types, has_native_state=False)
class DebounceStable(NativeState):
    @staticmethod
    def run(*, context):
        inp = context.inputs['input']
        reached = context.inputs['reached']
        cur = context.inputs['current_value_old']
        new = context.inputs['new_value_old']
        update_time = context.inputs['update_time_old']

        # print('TODO', inp, cur, new, update_time, reached)
        if cur is None or (new is not None and reached is None) or cur == wirely.Some(inp):
            # Directly after startup or after time got lost.
            # Or: Input is current value. Cancel any changes.
            n_cur = wirely.Some(inp)
            n_new = None
            n_update_time = None
            out = inp
        elif new != wirely.Some(inp):
            # Input is neither cur not new. Start a change to inp.
            now = context.start_time_for_stage(RunStage.DEFAULT).monotonic
            n_cur = cur
            n_new = wirely.Some(inp)
            n_update_time = wirely.Some(now + context.values['Duration'])
            # Note: cur cannot be None because if the first if
            out = cur.value
        elif not reached.value:
            # Input is new (and != cur), but the time has not yet expired. Do nothing.
            # Some checks
            if new != wirely.Some(inp):
                raise Exception('new != Some(inp)')

            n_cur = cur
            n_new = new
            n_update_time = update_time
            # Note: cur cannot be None because if the first if
            out = cur.value
        else:
            # Input is new and Timeout has been reached. Change to new.
            # Some checks
            if new != wirely.Some(inp):
                raise Exception('new != Some(inp)')

            n_cur = new
            n_new = None
            n_update_time = None
            out = new.value

        context.set_output('current_value', n_cur)
        context.set_output('new_value', n_new)
        context.set_output('update_time', n_update_time)
        context.set_output('output', out)
