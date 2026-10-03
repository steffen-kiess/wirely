import datetime
import contextlib

import sdnotify

import asyncio
import sys
import logging

import wirely as blocks
from wirely import program_compiler
from wirely.util import check_parameters

# TODO: Where should this import be done?
import wirely.compiler

import wirely.misc.service

# TODO: Handle stopping restarting properly, i.e. write data to CSV log files etc.


async def run_engine(engine) -> None:
    try:
        # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
        async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
            await engine.start_tasks_async(tg, async_exit_stack)
            tg.create_task(wirely.misc.service.should_never_return(engine.run()))
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


async def async_run(args) -> None:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    global_block_type = blocks.compiler.compile_files(args.input_filename)
    if global_block_type is None:
        sys.exit(1)

    prog = program_compiler.compile(global_block_type)
    if prog is None:
        sys.exit(1)

    eng = blocks.Engine(prog)

    if args.debug:
        eng.debug = True

    now = datetime.datetime.now()
    print('{}: Start'.format(now.isoformat()), flush=True)

    await run_engine(eng)
