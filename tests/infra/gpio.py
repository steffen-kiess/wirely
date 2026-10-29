"""Fake GPIO chips, which test engines give to GPIO programs through the wirely.gpio.open_chip runtime service."""

import errno
import os
import types
from typing import Any


class FakeLineRequest:
    def __init__(self) -> None:
        self.reconfigurations: list[Any] = []
        self.values: list[tuple[int, Any]] = []
        self.closed = False

    def __enter__(self) -> 'FakeLineRequest':
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def reconfigure_lines(self, config: Any) -> None:
        self.reconfigurations.append(config)

    def set_value(self, line: int, value: Any) -> None:
        self.values.append((line, value))


# The chip names by device file; both paths lead to the same chip, as symlinks would.
CHIP_PATHS = {
    '/dev/gpiochip0': 'gpiochip0',
    '/dev/gpio/by-name/test': 'gpiochip0',
}
NUM_LINES = 16
# The lines of gpiochip0 by offset, with the consumer of lines in use; the others are named GPIO<offset>.
LINES: dict[int, tuple[str, str | None]] = {
    7: ('LED', None),
    8: ('BUSY', 'leds-gpio'),
    9: ('DUP', None),
    10: ('DUP', None),
}


class FakeChip:
    instances: list['FakeChip'] = []

    def __init__(self, path: str) -> None:
        if path not in CHIP_PATHS:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        self.path = path
        self.closed = False
        self.request = FakeLineRequest()
        self.requests: list[tuple[Any, str]] = []
        self.instances.append(self)

    def __enter__(self) -> 'FakeChip':
        return self

    def __exit__(self, *args: object) -> None:
        if self.requests:
            assert self.request.closed
        self.closed = True

    def get_info(self) -> Any:
        return types.SimpleNamespace(name=CHIP_PATHS[self.path], label='fake', num_lines=NUM_LINES)

    def get_line_info(self, offset: int) -> Any:
        name, consumer = LINES.get(offset, (f'GPIO{offset}', None))
        return types.SimpleNamespace(offset=offset, name=name, used=consumer is not None, consumer=consumer or '')

    def request_lines(self, *, config: Any, consumer: str) -> FakeLineRequest:
        self.requests.append((config, consumer))
        return self.request
