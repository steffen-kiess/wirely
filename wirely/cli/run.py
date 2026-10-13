import asyncio
import sys
import os
import traceback
import contextlib

from wirely.compiler import program as program_compiler
from wirely.util import check_parameters

import wirely.misc.service

# TODO: Where should this import be done?
import wirely.compiler
from wirely.runtime.engine import Engine


class CommandException(Exception):
    pass


async def execute(engine, line):
    parts = line.split(' ')
    # print(parts)

    if line == 'list':
        print('Got nodes:')
        for node_name in engine.program.node_aliases:
            if '.' in node_name:
                continue
            print(f'  {node_name}')
        print('---', flush=True)
    elif line == 'graph':
        import wirely.cli.graphviz
        if engine.program.graph is None:
            raise CommandException('Graph visualization requires the compiler graph')
        wirely.cli.graphviz.get_graph(engine.program.graph, engine).view()
    elif parts[0] == 'debug':
        if len(parts) == 1:
            print(f'Debug is {"on" if engine.debug else "off"}', flush=True)
        elif parts[1].lower() == 'on':
            print('Setting debug to on', flush=True)
            engine.debug = True
        elif parts[1].lower() == 'off':
            print('Setting debug to off', flush=True)
            engine.debug = False
        else:
            raise CommandException('Unknown debug command')
    else:
        node_path = engine.get_node_path(parts[0])
        native_state = engine.native_states[node_path]

        node_type_name = engine.program.instances[node_path].type_name

        if native_state is None:
            raise CommandException(f'Node "{parts[0]}" ({node_type_name}) has no native state')
        if not hasattr(native_state, 'debug_command'):
            raise CommandException(f'Node "{parts[0]}" ({node_type_name}) has no debug_command function')
        native_state.debug_command(parts[1:])


async def read_loop(engine):
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        print('> ', end='', flush=True)
        line = await reader.readline()
        if line == b'':
            raise wirely.misc.service.TerminateProgram()

        line = str(line, 'utf-8').strip()
        if line == '':
            continue

        # print(f'Got {line!r}', flush=True)
        try:
            await execute(engine, line)
        except Exception as e:
            print(f'Error executing {line!r}: {e}', flush=True)
            if not isinstance(e, CommandException):
                traceback.print_exception(e)


async def run(engine) -> None:
    try:
        # Note: async_exit_stack must be exited before tg (and therefore entered after tg), otherwise destroying exiting tg might hang because there are still some tasks running
        async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
            await engine.start_tasks_async(tg, async_exit_stack)
            tg.create_task(wirely.misc.service.should_never_return(engine.run()))
            tg.create_task(wirely.misc.service.should_never_return(read_loop(engine)))

            # Note: When this is a task, the teardown of the other tasks will start too early
            # tg.create_task(wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw()))
            await wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw())
    except* wirely.misc.service.TerminateProgram:
        # Discard TerminateProgram exceptions

        # Print a newline to avoid leftover '^C' at beginning of line (because
        # python will return with exit code 0)
        print(flush=True)

        # Alternative: Kill python process
        # signal.signal(signal.SIGINT, signal.SIG_DFL)
        # signal.raise_signal(signal.SIGINT)


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 -m wirely.cli.run <file.wrl> [...]')
        sys.exit(1)
    input_filenames = sys.argv[1:]

    global_node_type = wirely.compiler.compile_files(input_filenames)
    if global_node_type is None:
        sys.exit(1)

    prog = program_compiler.compile(global_node_type)
    if prog is None:
        sys.exit(1)

    eng = Engine(prog)

    for node_id, instance in prog.instances.items():
        if instance.type_name == ('Std', 'DebugValueSource'):
            ty = instance.parameters['Type']._type
            native_inst = eng.native_states[node_id]
            if ty == wirely.types.boolean:
                native_inst.set_value(False)
            elif isinstance(ty, wirely.types.TypeOption):
                native_inst.set_value(None)
            else:
                raise Exception(f'Unknown type for debug input: {ty}')

    # eng.debug = True
    asyncio.run(run(eng))


if __name__ == '__main__':
    main()
