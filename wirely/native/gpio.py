from wirely.native import native_node_type
from wirely.runtime.contexts import NativeState

native_node_types = {}


@native_node_type(native_node_types, has_native_state=True)
class Output(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        self.gpio_request = None
        self.direction_set = False

    def start_tasks(self, task_group, async_exit_stack):
        super().start_tasks(task_group, async_exit_stack)

        import gpiod
        from gpiod.line import Bias, Direction

        bias = {
            'AsIs': Bias.AS_IS,
            'Disable': Bias.DISABLED,
            'PullDown': Bias.PULL_DOWN,
            'PullUp': Bias.PULL_UP,
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
                direction=Direction.INPUT, bias=bias[self.values['Bias'].name])},
            consumer='Wirely',
        ))
        self.direction_set = False

    def set_value(self, *, context):
        import gpiod
        from gpiod.line import Bias, Direction, Drive, Value

        value = context.inputs['input']
        logical_value = Value.ACTIVE if value else Value.INACTIVE

        if not self.direction_set:
            bias = {
                'AsIs': Bias.AS_IS,
                'Disable': Bias.DISABLED,
                'PullDown': Bias.PULL_DOWN,
                'PullUp': Bias.PULL_UP,
            }
            drive = {
                'PushPull': Drive.PUSH_PULL,
                'OpenDrain': Drive.OPEN_DRAIN,
                'OpenSource': Drive.OPEN_SOURCE,
            }
            self.gpio_request.reconfigure_lines({
                self.gpio_offset: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=logical_value,
                    active_low=self.values['ActiveState'].name == 'Low',
                    drive=drive[self.values['Drive'].name],
                    bias=bias[self.values['Bias'].name],
                ),
            })
            self.direction_set = True
        else:
            self.gpio_request.set_value(self.gpio_offset, logical_value)
