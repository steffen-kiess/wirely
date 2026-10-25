"""Command line client for the control protocol."""

import argparse
import asyncio
import copy
import shlex
import ssl
import sys
from collections.abc import Mapping
from typing import Any, Never

from ..control.client import ControlClient
from ..control.messages import Bank, ControlError, Op, RunState

# Response bytes besides the data of a ReadSnapshot response: header, map, key, byte string header.
READ_OVERHEAD = 3 + 1 + 1 + 3


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--unix-path', required=True, help='path of the control socket')
    parser.add_argument('--protocol', choices=['stream', 'serial'], default='stream',
                        help='framing used by the socket (default: stream)')
    parser.add_argument('--tls', action='store_true', help='use TLS (stream protocol only)')
    parser.add_argument('--tls-ca', help='CA certificates for verifying the runtime')
    parser.add_argument('--tls-server-name', help='expected name in the runtime certificate')
    parser.add_argument('--timeout', type=float, default=1.0,
                        help='retransmission timeout in seconds for the serial protocol')
    commands = parser.add_subparsers(title='commands', dest='client_command',
                                     description='without a command, read commands interactively')
    _add_commands(commands)


def _add_commands(commands: Any) -> None:
    def add(name: str, description: str) -> argparse.ArgumentParser:
        parser: argparse.ArgumentParser = commands.add_parser(name, help=description, description=description)
        return parser

    add('status', 'show the run state and step count')
    add('pause', 'stop starting steps')
    add('resume', 'continue running steps')
    step_parser = add('step', 'run steps while paused')
    step_parser.add_argument('--count', type=int, default=1)
    dump_parser = add('dump', 'print the state and temporaries')
    dump_parser.add_argument('--bank', choices=['state', 'temporaries', 'all'], default='all')
    dump_parser.add_argument('--program', nargs='+', metavar='FILE',
                             help='.wrl files of the running program, for printing values by name')


async def async_run(args: argparse.Namespace) -> int:
    if args.tls_ca is not None or args.tls_server_name is not None:
        args.tls = True
    if args.tls and args.protocol != 'stream':
        print('Error: --tls requires --protocol stream', file=sys.stderr)
        return 2
    ssl_context = None
    if args.tls:
        ssl_context = ssl.create_default_context(cafile=args.tls_ca)
        if args.tls_server_name is None:
            ssl_context.check_hostname = False
    try:
        client = await ControlClient.connect_unix(
            args.unix_path, args.protocol, ssl=ssl_context,
            server_hostname=args.tls_server_name, timeout=args.timeout,
        )
    except (OSError, ControlError, TimeoutError) as error:
        print(f'Error: Cannot connect to {args.unix_path}: {error}', file=sys.stderr)
        return 1
    try:
        if args.client_command is None:
            return await _interactive(client, args)
        return await _commands[args.client_command](client, args)
    except (OSError, ControlError, TimeoutError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    finally:
        await client.close()


class _CommandLineError(Exception):
    def __init__(self, status: int, message: str | None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _CommandParser(argparse.ArgumentParser):
    """Parser for interactive commands that reports errors instead of exiting."""

    def error(self, message: str) -> Never:
        raise _CommandLineError(2, f'{self.format_usage()}Error: {message}\n')

    def exit(self, status: int = 0, message: str | None = None) -> Never:
        raise _CommandLineError(status, message)


def _interactive_parser() -> tuple[_CommandParser, Mapping[str, argparse.ArgumentParser]]:
    parser = _CommandParser(add_help=False)
    commands = parser.add_subparsers(dest='client_command', required=True)
    _add_commands(commands)
    commands.add_parser('help', description='list the commands')
    commands.add_parser('quit', aliases=['exit'], description='leave interactive mode')
    choices: Mapping[str, argparse.ArgumentParser] = commands.choices
    for name, command_parser in choices.items():
        command_parser.prog = name
    return parser, choices


def _print_help(choices: Mapping[str, argparse.ArgumentParser]) -> None:
    print('Commands:')
    printed = set()
    for name, command_parser in choices.items():
        if command_parser not in printed:
            printed.add(command_parser)
            print(f'  {name:8} {command_parser.description}')
    print('Enter COMMAND -h for the options of a command.')


async def _interactive(client: ControlClient, args: argparse.Namespace) -> int:
    """Run commands from stdin until EOF or quit.

    On a terminal, commands are read with line editing, and failing commands
    do not end the session. Other input stops at the first failing command.
    """
    parser, choices = _interactive_parser()

    async def run_line(line: str) -> int | None:
        """Run one command line; return its status, or None for quit."""
        try:
            words = shlex.split(line)
        except ValueError as error:
            print(f'Error: {error}', file=sys.stderr)
            return 1
        if not words:
            return 0
        if words[0] not in choices:
            print(f'Error: Unknown command {words[0]!r}; enter help for a list', file=sys.stderr)
            return 1
        try:
            command_args = parser.parse_args(words, namespace=copy.copy(args))
        except _CommandLineError as error:
            if error.message:
                print(error.message, end='', file=sys.stderr if error.status else sys.stdout)
            return error.status
        if command_args.client_command in ('quit', 'exit'):
            return None
        if command_args.client_command == 'help':
            _print_help(choices)
            return 0
        try:
            return await _commands[command_args.client_command](client, command_args)
        except ControlError as error:
            print(f'Error: {error}', file=sys.stderr)
            return 1

    if not sys.stdin.isatty():
        while line := await asyncio.to_thread(sys.stdin.readline):
            status = await run_line(line)
            if status is None:
                break
            if status != 0:
                return status
        return 0

    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import NestedCompleter
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.shortcuts import CompleteStyle

    session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=NestedCompleter.from_nested_dict(dict.fromkeys(choices)),
        complete_style=CompleteStyle.READLINE_LIKE,
    )
    # Print output of other tasks above the prompt.
    with patch_stdout():
        while True:
            try:
                line = await session.prompt_async('wirely> ')
            except KeyboardInterrupt:
                continue  # Ctrl-C discards the line
            except EOFError:
                return 0
            if await run_line(line) is None:
                return 0


def _print_status(body: Mapping[int, Any]) -> int:
    print(f'{RunState(body[1]).name.lower()}, {body[2]} steps completed')
    return 0


async def _status(client: ControlClient, args: argparse.Namespace) -> int:
    return _print_status(await client.request(Op.STATUS))


async def _pause(client: ControlClient, args: argparse.Namespace) -> int:
    return _print_status(await client.request(Op.PAUSE))


async def _resume(client: ControlClient, args: argparse.Namespace) -> int:
    return _print_status(await client.request(Op.RESUME))


async def _step(client: ControlClient, args: argparse.Namespace) -> int:
    body = await client.request(Op.STEP, {1: args.count})
    print(f'{body[1]} steps, {body[2]} procedures run')
    return 0


async def _dump(client: ControlClient, args: argparse.Namespace) -> int:
    variable_tables = None
    if args.program is not None:
        from ..compiler import compile_files
        from ..compiler import program as program_compiler

        global_node_type = compile_files(args.program)
        program = None if global_node_type is None else program_compiler.compile(global_node_type)
        if program is None:
            return 1
        variable_tables = {Bank.STATE: program.state_variables, Bank.TEMPORARIES: program.temporaries}

    banks = list(Bank) if args.bank == 'all' else [Bank[args.bank.upper()]]
    snapshot = await client.request(Op.SNAPSHOT)
    snapshot_id = snapshot[1]
    print(f'Snapshot after {snapshot[2]} steps')
    chunk_size = client.framing.max_message_size - READ_OVERHEAD
    try:
        for bank in banks:
            size = snapshot[4][bank]
            chunks = await client.requests(
                (Op.READ_SNAPSHOT, {1: snapshot_id, 2: int(bank), 3: [[offset, min(chunk_size, size - offset)]]})
                for offset in range(0, size, chunk_size)
            )
            data = b''.join(chunk[1] for chunk in chunks)
            print()
            print(f'{bank.name.lower()} ({size} bytes):')
            if variable_tables is None:
                _print_hex(data)
                continue
            variable_table = variable_tables[bank]
            if variable_table.size != size:
                print(f'Error: Program has {variable_table.size} bytes for this bank; '
                      'it does not match the running program', file=sys.stderr)
                return 1
            # Without program identities, matching sizes are the only available check.
            for name, variable in sorted(variable_table.variables.items()):
                offset = variable_table.offsets[variable]
                value = variable.var_type.deserialize(data[offset:offset + variable.var_type.data_size])
                print(f'  {name}: {variable.var_type.to_text(value)}')
    finally:
        await client.request(Op.RELEASE_SNAPSHOT, {1: snapshot_id})
    return 0


def _print_hex(data: bytes) -> None:
    for offset in range(0, len(data), 16):
        print(f'  {offset:08x}  {data[offset:offset + 16].hex(" ")}')


_commands = {
    'status': _status,
    'pause': _pause,
    'resume': _resume,
    'step': _step,
    'dump': _dump,
}
