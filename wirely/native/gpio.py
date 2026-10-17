from wirely.native import native_node_type, native_procedure, native_resource_type
from wirely.runtime.contexts import NativeProcedure, NativeState
from wirely.runtime.enums import GPIOActiveState, GPIOBias, GPIODrive

native_node_types: dict[str, type] = {}
native_resource_types: dict[str, type] = {}


def _setting(value, mapping, description):
    native = value.native
    if native not in mapping:
        raise ValueError(f'Unsupported GPIO {description} {value.value}')
    return mapping[native]


@native_resource_type(native_resource_types, 'OutputState')
@native_node_type(native_node_types, has_native_state=True)
class Output(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)
        self.gpio_request = None
        self.direction_set = False

    def start_tasks(self, task_group, async_exit_stack):
        super().start_tasks(task_group, async_exit_stack)

        import gpiod
        from gpiod.line import Bias, Direction

        bias = {
            GPIOBias.AsIs: Bias.AS_IS,
            GPIOBias.Disable: Bias.DISABLED,
            GPIOBias.PullDown: Bias.PULL_DOWN,
            GPIOBias.PullUp: Bias.PULL_UP,
        }
        chip = async_exit_stack.enter_context(gpiod.Chip(self.values['ChipDeviceFile']))
        try:
            line_info = chip.get_line_info(self.values['Name'])
            if line_info.name != self.values['Name']:
                raise ValueError('Line name does not match')
            self.gpio_offset = line_info.offset
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Cannot find GPIO {self.values['Name']!r} on chip "
                f"{self.values['ChipDeviceFile']!r}"
            ) from exc
        self.gpio_request = async_exit_stack.enter_context(chip.request_lines(
            config={self.gpio_offset: gpiod.LineSettings(
                direction=Direction.INPUT, bias=_setting(self.values['Bias'], bias, 'bias'))},
            consumer='Wirely',
        ))
        self.direction_set = False

    @native_procedure('set_value')
    class SetValue(NativeProcedure):
        def __call__(self, *, context):
            state = context.inputs['state'].object
            import gpiod
            from gpiod.line import Bias, Direction, Drive, Value

            value = context.inputs['input']
            logical_value = Value.ACTIVE if value else Value.INACTIVE

            if not state.direction_set:
                bias = {
                    GPIOBias.AsIs: Bias.AS_IS,
                    GPIOBias.Disable: Bias.DISABLED,
                    GPIOBias.PullDown: Bias.PULL_DOWN,
                    GPIOBias.PullUp: Bias.PULL_UP,
                }
                drive = {
                    GPIODrive.PushPull: Drive.PUSH_PULL,
                    GPIODrive.OpenDrain: Drive.OPEN_DRAIN,
                    GPIODrive.OpenSource: Drive.OPEN_SOURCE,
                }
                state.gpio_request.reconfigure_lines({
                    state.gpio_offset: gpiod.LineSettings(
                        direction=Direction.OUTPUT,
                        output_value=logical_value,
                        active_low=_setting(state.values['ActiveState'],
                                            {GPIOActiveState.High: False, GPIOActiveState.Low: True}, 'active state'),
                        drive=_setting(state.values['Drive'], drive, 'drive'),
                        bias=_setting(state.values['Bias'], bias, 'bias'),
                    ),
                })
                state.direction_set = True
            else:
                state.gpio_request.set_value(state.gpio_offset, logical_value)
