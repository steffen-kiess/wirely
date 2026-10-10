import wirely
from wirely.native import native_node_type
from wirely.program import RunStage

native_node_types = {}


@native_node_type(native_node_types, has_native_state=False)
class ToggleFlipFlopTrigger:
    @staticmethod
    def update(*, context):
        output_value = context.values['OutputValue']
        value = wirely.Unit() if output_value is None else output_value.value
        minimum_delay = context.values['MinimumDelay']
        now = context.start_time_for_stage(RunStage.DEFAULT).monotonic

        inp = context.inputs['input']
        feedback = context.inputs['Feedback']
        reached = context.inputs['next_event_allowed_at_reached']
        next = context.inputs['next_event_allowed_at_old']
        pending = context.inputs['pending_event_old']
        # print(f'ToggleFlipFlopTrigger.run inp={inp} feedback={feedback} reached={reached} next={next} pending={pending}')

        if next is not None and reached is None:
            # Time has been lost while waiting for timeout.
            # Reset pending, but don't ignore input
            pending = None

        # Always overwrite pending with input
        if inp is not None:
            pending = inp

        # Check whether pending is current value
        if feedback is None:
            # No feedback => Reset pending value and ignore input
            pending = None
        elif pending == feedback:
            # Trying to set current value => Unset pending
            pending =  None

        if next is not None and reached == wirely.Some(False):
            # Cannot emit event right now
            output = None
            # Keep value of next
        else:
            # Set output if needed
            if pending is None:
                output = None
                next = None
            else:
                # Trigger change
                output = wirely.Some(value)
                pending = None
                next = wirely.Some(now + minimum_delay)

        context.set_output('output', output)
        context.set_output('next_event_allowed_at', next)
        context.set_output('pending_event', pending)
