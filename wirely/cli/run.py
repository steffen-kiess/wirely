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
import wirely.compiler.compilation

import wirely.cli.client
import wirely.misc.service
from wirely.control.client import ControlClient
from wirely.control.controller import RuntimeController
from wirely.control.framing import memory_framing_pair
from wirely.control.server import ControlServer, serve_session
from wirely.misc.state_directory import StateDirectoryLocked, lock_state_directory
from wirely.runtime.engine import Engine
from wirely.runtime.loading import LoadedProgram
from wirely.runtime.procedures import LoadError
from wirely.program.loader import load_program, read_build_id
from wirely.program.writer import program_build_id
from wirely.program.container import SectionList
from wirely.program.program import Program

# TODO: Handle stopping restarting properly, i.e. write data to CSV log files etc.


def _is_binary_program(filename: str) -> bool:
    with open(filename, 'rb') as file:
        return file.read(4) == SectionList.MAGIC.to_bytes(4, 'little')


async def run_engine(engine: Engine, controller: RuntimeController | None = None, state_directory: str | None = None,
                     tls_context: ssl.SSLContext | None = None, *, commands: str | None = None,
                     program: Program | None = None) -> int | None:
    """Run an engine; with a state directory, serve the control protocol on its sockets.

    With commands, 'interactive' or 'batch', read client commands from stdin over an internal
    session, with names from program, and return their exit status when they end.
    """
    status = None
    try:
        # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
        async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
            engine.activate(tg, async_exit_stack)
            tg.create_task(wirely.misc.service.should_never_return(engine.run()))
            if state_directory is not None:
                assert controller is not None
                await async_exit_stack.enter_async_context(ControlServer(controller, state_directory, tls_context))
            # tg.create_task(wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw()))

            now = datetime.datetime.now()
            print('{}: Started'.format(now.isoformat()), flush=True)
            sdnotify.SystemdNotifier().notify('READY=1')

            if commands is None:
                await wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw())
            assert controller is not None
            tg.create_task(wirely.misc.service.loop_until_sigint_and_throw(announce=False))
            client_end, server_end = memory_framing_pair()
            tg.create_task(serve_session(controller, server_end))
            client = ControlClient(client_end)
            try:
                await client.hello()
                status = await wirely.cli.client.read_commands(client, program, engine.native_types, commands)
            finally:
                await client.close()
            raise wirely.misc.service.TerminateProgram()
    except* wirely.misc.service.TerminateProgram:
        # Discard TerminateProgram exceptions

        # Print a newline to avoid leftover '^C' at beginning of line (because
        # python will return with exit code 0)
        print(flush=True)

        # Alternative: Kill python process
        # signal.signal(signal.SIGINT, signal.SIG_DFL)
        # signal.raise_signal(signal.SIGINT)
    return status


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
            build_id = None
            names = None  # The program for names in interactive commands; binary programs have none.
            if binary_input:
                data = Path(args.input_filename[0]).read_bytes()
                prog = load_program(data)
                build_id = read_build_id(data)
            else:
                global_node_type = wirely.compiler.compilation.compile_files(args.input_filename)
                if global_node_type is None:
                    return 1
                compiled = program_compiler.compile(global_node_type)
                if compiled is None:
                    return 1
                prog = compiled
                names = compiled
                if args.state_directory is not None or args.commands is not None:
                    build_id = program_build_id(prog)
            eng = Engine(LoadedProgram.load(prog))
        except (OSError, ValueError, LoadError) as error:
            print(f'Error: {error}', file=sys.stderr)
            return 1

        if args.debug:
            eng.debug = True

        now = datetime.datetime.now()
        print('{}: Start'.format(now.isoformat()), flush=True)

        controller = None if build_id is None else RuntimeController(eng, build_id)
        return await run_engine(eng, controller, args.state_directory, tls_context, commands=args.commands,
                                program=names)
