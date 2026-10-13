"""Command line client for the control protocol."""

import ssl
import sys

from ..control.client import ControlClient
from ..control.messages import Bank, ControlError, Op, RunState

# Response bytes besides the data of a ReadSnapshot response: header, map, key, byte string header.
READ_OVERHEAD = 3 + 1 + 1 + 3


def add_arguments(parser):
    parser.add_argument('--unix-path', required=True, help='path of the control socket')
    parser.add_argument('--protocol', choices=['stream', 'serial'], default='stream',
                        help='framing used by the socket (default: stream)')
    parser.add_argument('--tls', action='store_true', help='use TLS (stream protocol only)')
    parser.add_argument('--tls-ca', help='CA certificates for verifying the runtime')
    parser.add_argument('--tls-server-name', help='expected name in the runtime certificate')
    parser.add_argument('--timeout', type=float, default=1.0,
                        help='retransmission timeout in seconds for the serial protocol')
    commands = parser.add_subparsers(title='commands', dest='client_command', required=True)
    commands.add_parser('status', help='show the run state and step count')
    commands.add_parser('pause', help='stop starting steps')
    commands.add_parser('resume', help='continue running steps')
    step_parser = commands.add_parser('step', help='run steps while paused')
    step_parser.add_argument('--count', type=int, default=1)
    dump_parser = commands.add_parser('dump', help='print the state and temporaries')
    dump_parser.add_argument('--bank', choices=['state', 'temporaries', 'all'], default='all')
    dump_parser.add_argument('--program', nargs='+', metavar='FILE',
                             help='.wrl files of the running program, for printing values by name')


async def async_run(args):
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
        return await _commands[args.client_command](client, args)
    except (OSError, ControlError, TimeoutError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    finally:
        await client.close()


def _print_status(body):
    print(f'{RunState(body[1]).name.lower()}, {body[2]} steps completed')
    return 0


async def _status(client, args):
    return _print_status(await client.request(Op.STATUS))


async def _pause(client, args):
    return _print_status(await client.request(Op.PAUSE))


async def _resume(client, args):
    return _print_status(await client.request(Op.RESUME))


async def _step(client, args):
    body = await client.request(Op.STEP, {1: args.count})
    print(f'{body[1]} steps, {body[2]} procedures run')
    return 0


async def _dump(client, args):
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


def _print_hex(data):
    for offset in range(0, len(data), 16):
        print(f'  {offset:08x}  {data[offset:offset + 16].hex(" ")}')


_commands = {
    'status': _status,
    'pause': _pause,
    'resume': _resume,
    'step': _step,
    'dump': _dump,
}
