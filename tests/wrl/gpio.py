import asyncio
import contextlib

from gpiod.line import Bias, Direction, Drive, Value

import wirely.native.gpio
from tests.infra.gpio import FakeChip
from tests.infra.support import test_engine
from wirely.compiler import program as program_compiler
from wirely.compiler.compilation import compile_strs
from wirely.compiler.sources import standard_library_sources
from wirely.generated.enums import GPIOBias
from wirely.runtime.procedures import LoadError


def output(name, path, line):
    return f'''
node {name}_input: Std.DebugValueSource {{ Type: boolean, Initial: false }}
node {name}: GPIO.Output {{ ChipDeviceFile: "{path}", Name: "{line}" }}
connect {name}_input -> {name};
'''


def check_load_error(source, message):
    graph = compile_strs([*standard_library_sources(['std', 'gpio']), ('<gpio-loading>', source)])
    assert graph is not None
    try:
        test_engine(program_compiler.compile_nocatch(graph))
    except LoadError as exc:
        assert message in str(exc), exc
    else:
        raise AssertionError(f'Loaded although expecting {message!r}')


def check_loading():
    # Open enums can contain values the native code does not handle; they are rejected when loading.
    try:
        wirely.native.gpio._setting(GPIOBias(7), wirely.native.gpio._BIASES, 'bias')
    except ValueError as exc:
        assert str(exc) == 'Unsupported GPIO bias 7', exc
    else:
        raise AssertionError('Unknown bias accepted')

    # The line is checked when loading, without claiming it.
    check_load_error(output('a', '/dev/missing', 'LED'), "Cannot open GPIO chip '/dev/missing'")
    check_load_error(output('a', '/dev/gpiochip0', 'missing'), "No GPIO line 'missing' on '/dev/gpiochip0'")
    check_load_error(output('a', '/dev/gpiochip0', 'DUP'), "GPIO line name 'DUP' is not unique")
    check_load_error(output('a', '/dev/gpiochip0', 'BUSY'), "is in use by 'leds-gpio'")
    # Another path to the same chip is the same line.
    check_load_error(output('a', '/dev/gpiochip0', 'LED') + output('b', '/dev/gpio/by-name/test', 'LED'),
                     "GPIO line 'LED' on '/dev/gpio/by-name/test' is already used by a")
    assert all(not chip.requests and chip.closed for chip in FakeChip.instances)


async def run_test():
    check_loading()

    async with asyncio.TaskGroup() as tasks, contextlib.AsyncExitStack() as stack:
        engine.activate(tasks, stack)
        chip = FakeChip.instances[-1]
        assert chip.path == '/dev/gpiochip0'
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

    # A line that differs from the one checked when loading is not claimed.
    restarted = test_engine(engine.program)
    restarted.get_native_state('test_output.create').name = 'missing'
    async with asyncio.TaskGroup() as tasks, contextlib.AsyncExitStack() as stack:
        try:
            restarted.activate(tasks, stack)
        except ValueError as exc:
            assert "GPIO line 'missing' on '/dev/gpiochip0' changed since loading" in str(exc), exc
        else:
            raise AssertionError('Changed line was accepted')
    missing_chip = FakeChip.instances[-1]
    assert not missing_chip.requests
    assert missing_chip.closed
