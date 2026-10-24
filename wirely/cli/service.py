import argparse
import datetime
import contextlib

import sdnotify

import asyncio
import ssl
import sys
import logging
from pathlib import Path

from wirely.compiler import program as program_compiler

# TODO: Where should this import be done?
import wirely.compiler

import wirely.misc.service
from wirely.control.server import ControlServer
from wirely.misc.state_directory import StateDirectoryLocked, lock_state_directory
from wirely.runtime.engine import Engine
from wirely.runtime.procedures import LoadFailure
from wirely.program.loader import load_program
from wirely.program.container import SectionList

# TODO: Handle stopping restarting properly, i.e. write data to CSV log files etc.


def _is_binary_program(filename: str) -> bool:
    with open(filename, 'rb') as file:
        return file.read(4) == SectionList.MAGIC.to_bytes(4, 'little')


async def run_engine(engine: Engine, state_directory: str | None = None,
                     tls_context: ssl.SSLContext | None = None) -> None:
    try:
        # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
        async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
            await engine.start_tasks_async(tg, async_exit_stack)
            tg.create_task(wirely.misc.service.should_never_return(engine.run()))
            if state_directory is not None:
                await async_exit_stack.enter_async_context(
                    ControlServer(engine, state_directory, tls_context))
            # tg.create_task(wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw()))

            now = datetime.datetime.now()
            print('{}: Started'.format(now.isoformat()), flush=True)
            sdnotify.SystemdNotifier().notify('READY=1')

            await wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw())
    except* wirely.misc.service.TerminateProgram:
        # Discard TerminateProgram exceptions

        # Print a newline to avoid leftover '^C' at beginning of line (because
        # python will return with exit code 0)
        print(flush=True)

        # Alternative: Kill python process
        # signal.signal(signal.SIGINT, signal.SIG_DFL)
        # signal.raise_signal(signal.SIGINT)


async def async_run(args: argparse.Namespace) -> int | None:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        binary_input = any(_is_binary_program(filename) for filename in args.input_filename)
    except OSError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    if binary_input and len(args.input_filename) != 1:
        print('Error: a binary program must be the only input file', file=sys.stderr)
        return 2

    if (args.control_tls_cert is None) != (args.control_tls_key is None):
        print('Error: --control-tls-cert and --control-tls-key must be used together', file=sys.stderr)
        return 2
    if args.control_tls_cert is not None and args.state_directory is None:
        print('Error: TLS options require --state-directory', file=sys.stderr)
        return 2
    tls_context = None
    if args.control_tls_cert is not None:
        tls_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls_context.load_cert_chain(args.control_tls_cert, args.control_tls_key)

    with contextlib.ExitStack() as exit_stack:
        # Lock before starting anything, so a second runtime fails before it starts native tasks.
        if args.state_directory is not None:
            try:
                exit_stack.enter_context(lock_state_directory(args.state_directory))
            except StateDirectoryLocked as error:
                print(f'Error: {error}', file=sys.stderr)
                return 1

        try:
            if binary_input:
                prog = load_program(Path(args.input_filename[0]).read_bytes())
            else:
                global_node_type = wirely.compiler.compile_files(args.input_filename)
                if global_node_type is None:
                    return 1
                compiled = program_compiler.compile(global_node_type)
                if compiled is None:
                    return 1
                prog = compiled
            eng = Engine(prog)
        except (OSError, ValueError, LoadFailure) as error:
            print(f'Error: {error}', file=sys.stderr)
            return 1

        if args.debug:
            eng.debug = True

        now = datetime.datetime.now()
        print('{}: Start'.format(now.isoformat()), flush=True)

        await run_engine(eng, args.state_directory, tls_context)
    return None
