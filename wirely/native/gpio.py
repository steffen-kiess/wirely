"""GPIO lines of the hardware the runtime runs on.

The GPIO calls are synchronous on the event loop, which relies on them being fast, as for on-chip GPIOs or
memory-mapped ones like those of the Raspberry Pi 5's RP1. GPIO expanders on I2C, SPI, or USB block while accessing
their bus; they would need a separate library that accesses them asynchronously.
"""

import asyncio
import contextlib
import enum
from collections.abc import Callable, Mapping
from typing import Any, Literal, cast

import gpiod
from gpiod.line import Bias, Direction, Drive, Value

from wirely.generated import gpio as contracts
from wirely.generated.enums import GPIOActiveState, GPIOBias, GPIODrive
from wirely.runtime.native_api import ProcedureServices, StateServices


def _setting(value: enum.IntEnum, mapping: Mapping[Any, Any], description: str) -> Any:
    if value not in mapping:
        raise ValueError(f'Unsupported GPIO {description} {int(value)}')
    return mapping[value]


_BIASES = {
    GPIOBias.AsIs: Bias.AS_IS,
    GPIOBias.Disable: Bias.DISABLED,
    GPIOBias.PullDown: Bias.PULL_DOWN,
    GPIOBias.PullUp: Bias.PULL_UP,
}
_DRIVES = {
    GPIODrive.PushPull: Drive.PUSH_PULL,
    GPIODrive.OpenDrain: Drive.OPEN_DRAIN,
    GPIODrive.OpenSource: Drive.OPEN_SOURCE,
}
_ACTIVE_LOW = {GPIOActiveState.High: False, GPIOActiveState.Low: True}

# The runtime service that opens a chip, a callable taking the chip device file and returning an object with the
# interface of gpiod.Chip; it lets tests or embedding applications provide chips. gpiod.Chip is used without it.
OPEN_CHIP_SERVICE = 'wirely.gpio.open_chip'


class OutputCreate(contracts.OutputCreate):
    def initialize(self, *, services: StateServices[Literal['state']], chip_device_file: str, name: str,
                   active_state: GPIOActiveState, drive: GPIODrive,
                   bias: GPIOBias) -> contracts.OutputCreateResult:
        self.chip_device_file = chip_device_file
        self.name = name
        # The enums are open, so a program can contain values not handled here; they are rejected when loading.
        self.active_low = _setting(active_state, _ACTIVE_LOW, 'active state')
        self.drive = _setting(drive, _DRIVES, 'drive')
        self.bias = _setting(bias, _BIASES, 'bias')
        self.open_chip: Callable[[str], Any] = cast(
            Callable[[str], Any], services.runtime_services.get(OPEN_CHIP_SERVICE, gpiod.Chip))
        self.gpio_offset = self.check_line(services)
        self.gpio_request: Any = None  # A gpiod LineRequest once started.
        self.direction_set = False
        return contracts.OutputCreateResult(state=self)

    def check_line(self, services: StateServices[Literal['state']]) -> int:
        """Check that the line can be claimed at activation and return its offset.

        The GPIO lines are part of the hardware the runtime runs on, so they are checked when loading. Only their
        description is read; the chip is closed again, and the line is claimed when activating.
        """
        path = self.chip_device_file
        try:
            chip_context = self.open_chip(path)
        except OSError as e:
            raise ValueError(f'Cannot open GPIO chip {path!r}: {e}') from e
        with chip_context as chip:
            info = chip.get_info()
            lines = [chip.get_line_info(offset) for offset in range(info.num_lines)]
        matches = [line for line in lines if line.name == self.name]
        if len(matches) == 0:
            raise ValueError(f'No GPIO line {self.name!r} on {path!r}')
        if len(matches) > 1:
            raise ValueError(f'GPIO line name {self.name!r} is not unique on {path!r}')
        line = matches[0]
        if line.used:
            raise ValueError(f'GPIO line {self.name!r} on {path!r} is in use by {line.consumer!r}')
        # The kernel's name of the chip is the same for every path to it.
        claimed: dict[tuple[str, int], str] = services.program_state.setdefault('GPIO', {})
        key = (info.name, line.offset)
        if key in claimed:
            raise ValueError(f'GPIO line {self.name!r} on {path!r} is already used by {claimed[key]}')
        claimed[key] = services.name
        return int(line.offset)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        # Loading checked the line, so errors here mean that the runtime's hardware is broken; they are not retried.
        # What the line does after it is released at teardown is up to the kernel driver.
        chip = async_exit_stack.enter_context(self.open_chip(self.chip_device_file))
        if chip.get_line_info(self.gpio_offset).name != self.name:
            raise ValueError(f'GPIO line {self.name!r} on {self.chip_device_file!r} changed since loading')
        self.gpio_request = async_exit_stack.enter_context(chip.request_lines(
            config={self.gpio_offset: gpiod.LineSettings(
                direction=Direction.INPUT, bias=self.bias)},
            consumer='Wirely',
        ))
        self.direction_set = False


class OutputSetValue(contracts.OutputSetValue):
    def run(self, *, services: ProcedureServices, state: 'OutputCreate',
            input: bool) -> contracts.OutputSetValueResult:
        logical_value = Value.ACTIVE if input else Value.INACTIVE

        if not state.direction_set:
            state.gpio_request.reconfigure_lines({
                state.gpio_offset: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=logical_value,
                    active_low=state.active_low,
                    drive=state.drive,
                    bias=state.bias,
                ),
            })
            state.direction_set = True
        else:
            state.gpio_request.set_value(state.gpio_offset, logical_value)
        return contracts.OutputSetValueResult()


OutputState = OutputCreate  # Used as a resource type.
