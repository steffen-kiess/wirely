import wirely as blocks
from wirely.native import native_block_type
from wirely.program import RunStage

native_block_types = {}


@native_block_type(native_block_types, has_native_state=False)
class ToggleFlipFlopTrigger:
    @staticmethod
    def update(*, context):
        output_value = context.values['OutputValue']
        value = blocks.Unit() if output_value is None else output_value.value
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

        if next is not None and reached == blocks.Some(False):
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
                output = blocks.Some(value)
                pending = None
                next = blocks.Some(now + minimum_delay)

        context.set_result('output', output)
        context.set_result('next_event_allowed_at', next)
        context.set_result('pending_event', pending)
