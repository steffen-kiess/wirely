import dataclasses

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


@dataclasses.dataclass(frozen=True)
class GPIOData:
    chip_device_file: str
    name: str
    active_state: str
    drive: str
    bias: str

    @staticmethod
    def create(*, parameter_values):
        chip_device_file = parameter_values['ChipDeviceFile'].value
        name = parameter_values['Name'].value

        # TODO: Should not be needed, this should use the default parameter value automatically
        if 'ActiveState' in parameter_values:
            active_state = parameter_values['ActiveState'].value
        else:
            active_state = 'High'
        if 'Drive' in parameter_values:
            drive = parameter_values['Drive'].value
        else:
            drive = 'PushPull'
        if 'Bias' in parameter_values:
            bias = parameter_values['Bias'].value
        else:
            bias = 'AsIs'

        return GPIOData(chip_device_file=chip_device_file, name=name, active_state=active_state, drive=drive, bias=bias)

    def dump(self):
        print(f'  Chip device file: {self.chip_device_file!r}', flush=True)
        print(f'  GPIO name: {self.name!r}', flush=True)


@native_block_type(native_block_types, has_state=True, data_type=GPIOData)
class Output(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)
        self.data = static_block_context.parsed_native_instance
        self.gpio_request = None
        self.direction_set = False

    def start_tasks(self, task_group, async_exit_stack):
        super().start_tasks(task_group, async_exit_stack)

        import gpiod
        from gpiod.line import Bias, Direction, Drive

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
        if self.data.active_state not in ('High', 'Low'):
            raise ValueError(f'Unknown active state: {self.data.active_state!r}')
        if self.data.drive not in drive:
            raise ValueError(f'Unknown drive: {self.data.drive!r}')
        if self.data.bias not in bias:
            raise ValueError(f'Unknown bias: {self.data.bias!r}')

        chip = async_exit_stack.enter_context(gpiod.Chip(self.data.chip_device_file))
        try:
            line_info = chip.get_line_info(self.data.name)
            if line_info.name != self.data.name:
                raise ValueError('Line name does not match')
            self.gpio_offset = line_info.offset
        except (OSError, ValueError) as exc:
            raise ValueError(
                f'Cannot find GPIO {self.data.name!r} on chip '
                f'{self.data.chip_device_file!r}'
            ) from exc
        self.gpio_request = async_exit_stack.enter_context(chip.request_lines(
            config={self.gpio_offset: gpiod.LineSettings(
                direction=Direction.INPUT, bias=bias[self.data.bias])},
            consumer='Wirely',
        ))
        self.direction_set = False

    @native_procedure
    def set_value(self, *, context):
        import gpiod
        from gpiod.line import Bias, Direction, Drive, Value

        var_input = context.static.sources['implicit input']
        value = context.dep_values[var_input]
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
                    active_low=self.data.active_state == 'Low',
                    drive=drive[self.data.drive],
                    bias=bias[self.data.bias],
                ),
            })
            self.direction_set = True
        else:
            self.gpio_request.set_value(self.gpio_offset, logical_value)
