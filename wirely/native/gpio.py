import asyncio
import contextlib
import enum
from collections.abc import Mapping
from typing import Any, Literal

from wirely.generated import gpio as contracts
from wirely.generated.enums import GPIOActiveState, GPIOBias, GPIODrive
from wirely.runtime.native_api import ProcedureServices, StateServices


def _setting(value: enum.IntEnum, mapping: Mapping[Any, Any], description: str) -> Any:
    if value not in mapping:
        raise ValueError(f'Unsupported GPIO {description} {int(value)}')
    return mapping[value]


class OutputCreate(contracts.OutputCreate):
    def initialize(self, *, services: StateServices[Literal['state']], chip_device_file: str, name: str,
                   active_state: GPIOActiveState, drive: GPIODrive,
                   bias: GPIOBias) -> contracts.OutputCreateResult:
        self.chip_device_file = chip_device_file
        self.name = name
        self.active_state = active_state
        self.drive = drive
        self.bias = bias
        self.gpio_request: Any = None  # A gpiod LineRequest once started.
        self.direction_set = False
        return contracts.OutputCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        import gpiod
        from gpiod.line import Bias, Direction

        bias = {
            GPIOBias.AsIs: Bias.AS_IS,
            GPIOBias.Disable: Bias.DISABLED,
            GPIOBias.PullDown: Bias.PULL_DOWN,
            GPIOBias.PullUp: Bias.PULL_UP,
        }
        chip = async_exit_stack.enter_context(gpiod.Chip(self.chip_device_file))
        try:
            line_info = chip.get_line_info(self.name)
            if line_info.name != self.name:
                raise ValueError('Line name does not match')
            self.gpio_offset = line_info.offset
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Cannot find GPIO {self.name!r} on chip "
                f"{self.chip_device_file!r}"
            ) from exc
        self.gpio_request = async_exit_stack.enter_context(chip.request_lines(
            config={self.gpio_offset: gpiod.LineSettings(
                direction=Direction.INPUT, bias=_setting(self.bias, bias, 'bias'))},
            consumer='Wirely',
        ))
        self.direction_set = False


class OutputSetValue(contracts.OutputSetValue):
    def run(self, *, services: ProcedureServices, state: 'OutputCreate',
            input: bool) -> contracts.OutputSetValueResult:
        import gpiod
        from gpiod.line import Bias, Direction, Drive, Value

        logical_value = Value.ACTIVE if input else Value.INACTIVE

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
                    active_low=_setting(state.active_state,
                                        {GPIOActiveState.High: False, GPIOActiveState.Low: True}, 'active state'),
                    drive=_setting(state.drive, drive, 'drive'),
                    bias=_setting(state.bias, bias, 'bias'),
                ),
            })
            state.direction_set = True
        else:
            state.gpio_request.set_value(state.gpio_offset, logical_value)
        return contracts.OutputSetValueResult()


OutputState = OutputCreate  # Used as a resource type.
