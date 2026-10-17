import asyncio
import sys
import signal
from collections.abc import Awaitable
from typing import NoReturn


# TODO: Should this be in service.util?

# TODO: Shouldn't the handlers be set earlier for most callers?
# Maybe this should be split into a part which throws when a signal is received and a part which just sleeps until canceled
async def loop_until_sigint(allow_sigterm: bool = True) -> None:
    """Loop until Crtl-C was pressed."""

    sigint_received = asyncio.Event()

    def sigint_handler() -> None:
        """End loop."""
        sigint_received.set()

    if sys.platform == "win32":
        print("Windows does not support signals", flush=True)
    else:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, sigint_handler)
        if allow_sigterm:
            loop.add_signal_handler(signal.SIGTERM, sigint_handler)
    print("Press Ctrl+C to stop", flush=True)
    await sigint_received.wait()
    # TODO: Reset handlers?


class TerminateProgram(BaseException):
    pass


async def loop_until_sigint_and_throw(allow_sigterm: bool = True) -> NoReturn:
    await loop_until_sigint(allow_sigterm=allow_sigterm)
    # Raising a KeyboardInterrupt (or SystemExit) here causes a
    # "Task exception was never retrieved" message
    # See <https://github.com/python/cpython/issues/101515>
    # Raise a different exception instead
    # raise KeyboardInterrupt()
    raise TerminateProgram()


async def should_never_return(awaitable: Awaitable[object]) -> NoReturn:
    # TODO: Get a stacktrace here and show that if awaitable returns?
    await awaitable
    raise Exception('Awaitable which should never return has returned: {}'.format(awaitable))
