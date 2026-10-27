import asyncio
import contextlib
import enum
import sys
import types
from unittest import mock

from tests.infra.support import test_engine


class Direction(enum.Enum):
    INPUT = 1
    OUTPUT = 2


class Value(enum.Enum):
    INACTIVE = 0
    ACTIVE = 1


class Bias(enum.Enum):
    AS_IS = 0
    DISABLED = 1
    PULL_DOWN = 2
    PULL_UP = 3


class Drive(enum.Enum):
    PUSH_PULL = 1
    OPEN_DRAIN = 2
    OPEN_SOURCE = 3


class LineSettings:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class LineRequest:
    def __init__(self):
        self.reconfigurations = []
        self.values = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def reconfigure_lines(self, config):
        self.reconfigurations.append(config)

    def set_value(self, line, value):
        self.values.append((line, value))


class Chip:
    instances = []

    def __init__(self, path):
        self.path = path
        self.closed = False
        self.request = LineRequest()
        self.lookups = []
        self.requests = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.requests:
            assert self.request.closed
        self.closed = True

    def get_line_info(self, name):
        self.lookups.append(name)
        if name != 'LED':
            raise OSError('No such line')
        return types.SimpleNamespace(offset=7, name='LED')

    def request_lines(self, *, config, consumer):
        self.requests.append((config, consumer))
        return self.request


async def run_test():
    line = types.ModuleType('gpiod.line')
    line.Bias = Bias
    line.Direction = Direction
    line.Drive = Drive
    line.Value = Value
    gpiod = types.ModuleType('gpiod')
    gpiod.Chip = Chip
    gpiod.LineSettings = LineSettings
    gpiod.line = line

    with mock.patch.dict(sys.modules, {'gpiod': gpiod, 'gpiod.line': line}):
        assert not Chip.instances
        async with asyncio.TaskGroup() as tasks, contextlib.AsyncExitStack() as stack:
            await engine.activate(tasks, stack)
            chip = Chip.instances[-1]
            assert chip.path == '/dev/gpiochip-test'
            assert chip.lookups == ['LED']
            config, consumer = chip.requests[0]
            assert consumer == 'Wirely'
            assert list(config) == [7]
            assert config[7].direction is Direction.INPUT
            assert config[7].bias is Bias.PULL_UP
            assert chip.request.reconfigurations == []

            input_state = engine.get_native_state('test_input.create')
            input_state.set_value(True)
            engine.step()
            assert len(chip.request.reconfigurations) == 1
            output_settings = chip.request.reconfigurations[0][7]
            assert output_settings.direction is Direction.OUTPUT
            assert output_settings.output_value is Value.ACTIVE
            assert output_settings.active_low is True
            assert output_settings.drive is Drive.OPEN_DRAIN
            assert output_settings.bias is Bias.PULL_UP
            assert chip.request.values == []

            input_state.set_value(False)
            engine.step()
            assert chip.request.values == [(7, Value.INACTIVE)]

        assert chip.request.closed
        assert chip.closed

        restarted = test_engine(engine.program)
        restarted.get_native_state('test_output.create').name = 'missing'
        async with asyncio.TaskGroup() as tasks, contextlib.AsyncExitStack() as stack:
            try:
                await restarted.activate(tasks, stack)
            except ValueError as exc:
                assert "Cannot find GPIO 'missing'" in str(exc)
            else:
                raise AssertionError('Missing line was accepted')
        missing_chip = Chip.instances[-1]
        assert missing_chip.lookups == ['missing']
        assert not missing_chip.requests
        assert missing_chip.closed
