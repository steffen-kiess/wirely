import dataclasses

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


@dataclasses.dataclass(frozen=True)
class ToggleFlipFlopTriggerData:
    output_value: object
    minimum_delay: blocks.Duration

    @staticmethod
    def create(*, parameter_values):
        # TODO: default parameters should be handled somewhere else
        if 'OutputType' not in parameter_values:
            par_type = blocks.types.unit
        else:
            par_type = parameter_values['OutputType']

        if 'OutputValue' not in parameter_values:
            output_value = None
            if par_type != blocks.types.unit:
                # TODO: Provide error location in a better way / avoid stack trace?
                raise Exception(f'Trigger type is not unit ({par_type}) but no value is given')
        else:
            output_value = parameter_values['OutputValue'].value

        output_value = par_type.verify_value(output_value)

        # TODO: default parameters should be handled somewhere else
        if 'MinimumDelay' not in parameter_values:
            minimum_delay = blocks.Duration.from_seconds(1)
        else:
            minimum_delay = parameter_values['MinimumDelay'].value

        return ToggleFlipFlopTriggerData(output_value=output_value, minimum_delay=minimum_delay)


@native_block_type(native_block_types, has_state=False, data_type=ToggleFlipFlopTriggerData)
class ToggleFlipFlopTrigger:
    @native_procedure
    @staticmethod
    def read_time(*, context):
        var_output = context.static.targets['NextEventAllowedAtOld']
        var_state = context.static.state['NextEventAllowedAt']

        value = context.state_old[var_state]

        # Turn value from Option[MonotonicTime] into MonotonicTime
        if value is None:
            value = blocks.MonotonicTime.create_invalid()
        else:
            value = value[0]

        context.results[var_output] = value

    @native_procedure
    @staticmethod
    def update(*, context):
        var_output = context.static.targets['']
        var_input = context.static.sources['implicit input']
        var_feedback = context.static.sources['Feedback']
        var_reached = context.static.sources['NextEventAllowedAtReached']
        var_next = context.static.state['NextEventAllowedAt']
        var_pending = context.static.state['PendingEvent']

        data = context.static.block.parsed_native_instance
        now = context.step_info.stage_start_time_monotonic[blocks.RunStage.Default]

        inp = context.dep_values[var_input]
        feedback = context.dep_values[var_feedback]
        reached = context.dep_values[var_reached]
        next = context.state_old[var_next]
        pending = context.state_old[var_pending]
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

        if next is not None and reached == (False,):
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
                output = (data.output_value,)
                pending = None
                next = (now + data.minimum_delay,)

        context.results[var_output] = output
        context.state_new[var_next] = next
        context.state_new[var_pending] = pending
