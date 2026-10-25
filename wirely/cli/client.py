"""Command line client for the control protocol."""

import argparse
import asyncio
import copy
import shlex
import ssl
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Never

from ..control.client import ControlClient
from ..control.messages import BUILD_ID_PREFIX_LENGTH, Bank, ControlError, ErrorCode, Op, RunState

if TYPE_CHECKING:
    from ..program import Program

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
    parser.add_argument('--program', action='append', metavar='FILE',
                        help='.wrl file of the running program, for names; repeat for several files')
    commands = parser.add_subparsers(title='commands', dest='client_command',
                                     description='without a command, read commands interactively')
    _add_commands(commands)
    # Only on the command line: it runs until interrupted.
    watch_parser = commands.add_parser('watch', help='print the run state and program whenever they change')
    watch_parser.add_argument('--steps', action='store_true', help='also print the step count, after each step')


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
    call_parser = add('call', 'send a command to the native state of an initializer')
    call_parser.add_argument('instance', type=int, help='index of the initializer instance')
    call_parser.add_argument('command', type=int, help='command number, as documented by the library')
    call_parser.add_argument('argument', nargs='?', type=_diagnostic_value,
                             help="argument in CBOR diagnostic notation, such as h'0700'")
    debug_parser = add('debug', 'show or switch the runtime printing each step and procedure run')
    debug_parser.add_argument('state', nargs='?', choices=['on', 'off'])
    option_parser = add('option', 'list, show, or set runtime options')
    option_parser.add_argument('key', nargs='?', type=_option_key, help='option name, or number')
    option_parser.add_argument('value', nargs='?', type=_diagnostic_value,
                               help='new value in CBOR diagnostic notation, such as true')
    # Commands that need --program.
    list_parser = add('list', 'list the nodes of the program with their types, without contacting the runtime')
    list_parser.add_argument('node', nargs='?', help='list only this node and the nodes inside it')
    get_parser = add('get', 'print the value of a debug source, or its queued event')
    get_parser.add_argument('node')
    set_parser = add('set', 'set the value of a debug value source')
    set_parser.add_argument('node')
    set_parser.add_argument('value', help='value in the value text format')
    send_parser = add('send', 'send an event from a debug event source')
    send_parser.add_argument('node')
    send_parser.add_argument('value', help='payload in the value text format')


def _option_key(text: str) -> int | str:
    """Return an option key: an integer if the text is one, otherwise the text."""
    try:
        return int(text)
    except ValueError:
        return text


def _diagnostic_value(text: str) -> Any:
    from ..control import diagnostic
    try:
        return diagnostic.parse(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


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
    args.compiled_program = None
    if args.program is not None:
        from ..compiler import compile_files
        from ..compiler import program as program_compiler

        global_node_type = compile_files(args.program)
        args.compiled_program = None if global_node_type is None else program_compiler.compile(global_node_type)
        if args.compiled_program is None:
            return 1
    if args.client_command == 'list':
        return await _list(None, args)
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
    from prompt_toolkit.completion import NestedCompleter, WordCompleter
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.shortcuts import CompleteStyle

    completions: dict[str, Any] = dict.fromkeys(choices)
    if args.compiled_program is not None:
        from ..control.node_commands import command_targets
        # Typed commands complete the names of the nodes that support them, and list all names.
        # Node paths contain dots, so words end only at whitespace.
        names = dict(command_targets(args.compiled_program), list=set(args.compiled_program.symbols.node_aliases))
        completions.update((command, WordCompleter(sorted(nodes), WORD=True)) for command, nodes in names.items())
    session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=NestedCompleter.from_nested_dict(completions),
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


async def _watch(client: ControlClient, args: argparse.Namespace) -> int:
    # Run state and program, and with --steps the step count.
    mask = 1 << 1 | 1 << 3 | (1 << 2 if args.steps else 0)
    async for values in client.watch_status(mask):
        parts = [RunState(values[1]).name.lower()]
        if 2 in values:
            parts.append(f'{values[2]} steps completed')
        if 3 in values:
            parts.append(f'program {values[3].hex()}')
        print(', '.join(parts), flush=True)
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


async def _bind(client: ControlClient, program: 'Program | None') -> bool:
    """Bind to the program, or without one to the active program; report a mismatch and return False."""
    if program is None:
        await client.request(Op.BIND_PROGRAM)
        return True
    from ..program.writer import program_build_id

    try:
        await client.request(Op.BIND_PROGRAM, {1: program_build_id(program)[:BUILD_ID_PREFIX_LENGTH]})
    except ControlError as error:
        if error.code != ErrorCode.NOT_BOUND:
            raise
        print('Error: The running program does not match the program files', file=sys.stderr)
        return False
    return True


async def _call(client: ControlClient, args: argparse.Namespace) -> int:
    if not await _bind(client, args.compiled_program):
        return 1
    request = {1: args.instance, 2: args.command}
    if args.argument is not None:
        request[3] = args.argument
    body = await client.request(Op.NATIVE_COMMAND, request)
    if 1 in body:
        from ..control import diagnostic
        print(diagnostic.format(body[1]))
    return 0


async def _debug(client: ControlClient, args: argparse.Namespace) -> int:
    if args.state is None:
        body = await client.request(Op.GET_OPTIONS, {1: ['debug']})
        print('on' if body[1]['debug'] else 'off')
    else:
        await client.request(Op.SET_OPTIONS, {1: {'debug': args.state == 'on'}})
    return 0


async def _option(client: ControlClient, args: argparse.Namespace) -> int:
    from ..control import diagnostic

    if args.value is not None:
        await client.request(Op.SET_OPTIONS, {1: {args.key: args.value}})
        return 0
    body = await client.request(Op.GET_OPTIONS, {} if args.key is None else {1: [args.key]})
    if args.key is not None:
        print(diagnostic.format(body[1][args.key]))
    else:
        for key, value in body[1].items():
            print(f'{diagnostic.format(key)}: {diagnostic.format(value)}')
    return 0


async def _list(client: ControlClient | None, args: argparse.Namespace) -> int:
    """Print the node tree of the program; this needs no connection."""
    from ..control.node_commands import NodeCommandError, node_tree

    if args.compiled_program is None:
        print('Error: `list` requires --program', file=sys.stderr)
        return 1
    try:
        nodes = node_tree(args.compiled_program, args.node)
    except NodeCommandError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    rows = [['  ' * node.depth + node.name, node.node_type]
            + ([str(node.target.value_type), ', '.join(node.target.commands)] if node.target is not None else [])
            for node in nodes]
    widths = [max((len(row[column]) for row in rows if column < len(row)), default=0) for column in range(3)]
    for row in rows:
        print('  '.join(cell.ljust(widths[column]) if column < 3 else cell
                        for column, cell in enumerate(row)).rstrip())
    return 0


async def _typed_command(client: ControlClient, args: argparse.Namespace) -> int:
    """Run get, set, or send for a node, using its node type's native commands."""
    from ..control.node_commands import NodeCommandError, resolve

    name = args.client_command
    program = args.compiled_program
    if program is None:
        print(f'Error: `{name}` requires --program', file=sys.stderr)
        return 1
    try:
        target = resolve(program, args.node)
        command = target.command(name)
        argument = target.encode(args.value) if command.takes_value else {}
    except NodeCommandError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    if not await _bind(client, program):
        return 1
    body = await client.request(Op.NATIVE_COMMAND, {1: target.instance, 2: command.number, 3: argument})
    if not command.takes_value:
        value = target.decode(body.get(1))
        print('none' if value is None else value)
    return 0


async def _dump(client: ControlClient, args: argparse.Namespace) -> int:
    program = args.compiled_program
    if not await _bind(client, program):
        return 1
    variable_tables = None
    if program is not None:
        variable_tables = {Bank.STATE: program.state_variables, Bank.TEMPORARIES: program.temporaries}

    banks = list(Bank) if args.bank == 'all' else [Bank[args.bank.upper()]]
    snapshot = await client.request(Op.SNAPSHOT)
    snapshot_id = snapshot[1]
    print(f'Snapshot after {snapshot[2]} steps')
    chunk_size = client.framing.max_message_size - READ_OVERHEAD
    try:
        for bank in banks:
            size = snapshot[3][bank]
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
    'call': _call,
    'list': _list,
    'debug': _debug,
    'option': _option,
    'get': _typed_command,
    'set': _typed_command,
    'send': _typed_command,
    'watch': _watch,
}
