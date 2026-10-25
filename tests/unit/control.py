import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import io
import os
import ssl
import tempfile
import unittest.mock
import zlib

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import wirely.cli.client
import wirely.compiler
import wirely.misc.service
import wirely.types
from wirely.cli import run as run_command
from wirely.compiler import program as program_compiler
from wirely.control import node_commands
from wirely.control.client import ControlClient
from wirely.control.controller import RuntimeController
from wirely.control.framing import SerialFraming, cobs_decode, cobs_encode, memory_framing_pair
from wirely.control.messages import ControlError, ErrorCode, Kind, Op, decode, encode
from wirely.control.server import ControlServer, serve_session
from wirely.misc.state_directory import StateDirectoryLocked, lock_state_directory
from wirely.program.program import procedure_instances
from wirely.program.writer import program_build_id, write_program
from wirely.program.loader import load_program
from wirely.runtime.engine import Engine

program_path = os.path.join(os.path.dirname(__file__), 'control.wrl')


def check_cobs():
    for data in [b'', b'\0', b'\0\0', b'a\0b', bytes(range(1, 255)), bytes(range(1, 256)) * 3,
                 bytes(range(256)) * 2]:
        encoded = cobs_encode(data)
        assert 0 not in encoded
        assert cobs_decode(encoded) == data, data


def write_certificate(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'runtime')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('runtime')]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = os.path.join(directory, 'cert.pem')
    key_path = os.path.join(directory, 'key.pem')
    with open(cert_path, 'wb') as file:
        file.write(certificate.public_bytes(serialization.Encoding.PEM))
    with open(key_path, 'wb') as file:
        file.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return cert_path, key_path


async def expect_error(code, awaitable):
    try:
        await awaitable
    except ControlError as error:
        assert error.code == code, error
    else:
        raise AssertionError(f'Expected {code.name}')


async def check_locked(server):
    # The lock conflicts even within the same process.
    try:
        with lock_state_directory(server.state_directory):
            raise AssertionError('Locked the state directory twice')
    except StateDirectoryLocked:
        pass
    client = await ControlClient.connect_unix(server.stream_path)
    try:
        assert (await client.request(Op.STATUS))[2] == engine.step_count
    finally:
        await client.close()


async def check_binding(server):
    build_id = program_build_id(engine.program)
    client = await ControlClient.connect_unix(server.stream_path)
    try:
        await expect_error(ErrorCode.NOT_BOUND, client.request(Op.SNAPSHOT))
        # Without a prefix, the runtime binds to the active program and returns a prefix of its ID.
        assert await client.request(Op.BIND_PROGRAM) == {1: build_id[:16]}
        snapshot = await client.request(Op.SNAPSHOT)
        assert await client.request(Op.RELEASE_SNAPSHOT, {1: snapshot[1]}) == {}
        for prefix in (b'', build_id[:4], build_id):
            assert await client.request(Op.BIND_PROGRAM, {1: prefix}) == {}
        # A failed attempt leaves the session unbound.
        await expect_error(ErrorCode.NOT_BOUND,
                           client.request(Op.BIND_PROGRAM, {1: bytes([build_id[0] ^ 1])}))
        await expect_error(ErrorCode.NOT_BOUND, client.request(Op.SNAPSHOT))
        await expect_error(ErrorCode.INVALID_ARGUMENT, client.request(Op.BIND_PROGRAM, {1: build_id + b'\0'}))
        await expect_error(ErrorCode.INVALID_ARGUMENT, client.request(Op.BIND_PROGRAM, {1: 'id'}))
    finally:
        await client.close()


async def read_message(client, timeout=2.0):
    async with asyncio.timeout(timeout):
        return decode(await client.framing.read())


async def expect_silence(client, duration):
    try:
        message = await read_message(client, duration)
    except TimeoutError:
        return
    raise AssertionError(f'Unexpected message {message}')


async def acknowledge(client, notification, body=None):
    await client.framing.write(encode(Kind.ACK, notification.seq, Op.STATUS, body or {}))


async def check_status(server):
    build_id = program_build_id(engine.program)
    watcher = await ControlClient.connect_unix(server.stream_path)
    other = await ControlClient.connect_unix(server.stream_path)
    try:
        # Bit n of the mask selects key n.
        assert await watcher.request(Op.STATUS, {1: 1 << 1}) == {1: 0}
        assert await watcher.request(Op.STATUS, {1: 1 << 3}) == {3: build_id[:16]}
        assert await watcher.request(Op.STATUS) == {1: 0, 2: engine.step_count}

        assert await watcher.request(Op.STATUS, {2: True}) == {1: 0, 2: engine.step_count}
        await other.request(Op.PAUSE)
        notification = await read_message(watcher)
        assert (notification.kind, notification.op, notification.body) == (Kind.NOTIFICATION, Op.STATUS, {1: 1})
        # Further changes wait for the acknowledgement and arrive merged.
        steps = engine.step_count
        await other.request(Op.STEP, {1: 2})
        await other.request(Op.STEP)
        await expect_silence(watcher, 0.15)
        await acknowledge(watcher, notification)
        update = await read_message(watcher)
        assert (update.seq, update.body) == ((notification.seq + 1) & 0xff, {2: steps + 3})
        await acknowledge(watcher, update)
        # Status without key 2 keeps the subscription.
        assert await watcher.request(Op.STATUS, {1: 1 << 1}) == {1: 1}
        await other.request(Op.RESUME)
        update = await read_message(watcher)
        # Resuming may also run a step.
        assert update.body[1] == 0
        # The acknowledgement can cancel the subscription.
        await acknowledge(watcher, update, {1: True})
        await other.request(Op.PAUSE)
        await expect_silence(watcher, 0.15)

        # Hello ends the subscription, too.
        assert await watcher.request(Op.STATUS, {2: True}) == {1: 1, 2: engine.step_count}
        await watcher.hello()
        await other.request(Op.RESUME)
        await expect_silence(watcher, 0.15)

        # watch_status() acknowledges notifications and merges their deltas.
        updates = watcher.watch_status(1 << 1 | 1 << 3)
        assert await anext(updates) == {1: 0, 3: build_id[:16]}
        await other.request(Op.PAUSE)
        assert await anext(updates) == {1: 1, 3: build_id[:16]}
        await other.request(Op.RESUME)
        assert await anext(updates) == {1: 0, 3: build_id[:16]}
        await updates.aclose()
    finally:
        await watcher.close()
        await other.close()


async def check_status_serial(server):
    """An unacknowledged notification is resent with the next Seq."""
    # A shorter resend timeout keeps the test fast.
    with unittest.mock.patch('wirely.control.server.SERIAL_NOTIFICATION_TIMEOUT', 0.1):
        watcher = await ControlClient.connect_unix(server.serial_path, 'serial')
        other = await ControlClient.connect_unix(server.stream_path)
        try:
            assert await watcher.request(Op.STATUS, {1: 1 << 1, 2: True}) == {1: 0}
            await other.request(Op.PAUSE)
            first = await read_message(watcher)
            assert first.body == {1: 1}
            resent = await read_message(watcher)
            assert (resent.seq, resent.body) == ((first.seq + 1) & 0xff, {1: 1})
            # Only the latest notification can be acknowledged.
            await acknowledge(watcher, first)
            latest = await read_message(watcher)
            assert latest.seq == (resent.seq + 1) & 0xff
            await acknowledge(watcher, latest)
            await expect_silence(watcher, 0.4)
        finally:
            await other.request(Op.RESUME)
            await watcher.close()
            await other.close()


class QueuedFraming:
    """Framing that returns queued messages and records written ones."""

    reliable = True
    max_message_size = 0xffff

    def __init__(self, messages):
        self.messages = list(messages)
        self.written = []

    async def read(self):
        return self.messages.pop(0) if self.messages else None

    async def write(self, message):
        self.written.append(message)


async def check_client_ignores_notifications():
    # A notification whose Seq and Op match the outstanding request is not its response.
    framing = QueuedFraming([encode(Kind.NOTIFICATION, 1, Op.STATUS, {1: 1}),
                             encode(Kind.RESPONSE, 1, Op.STATUS, {1: 0})])
    assert await ControlClient(framing).request(Op.STATUS) == {1: 0}


async def check_memory_framing():
    """An in-process connection carries a session, which ends when the client closes."""
    client_end, server_end = memory_framing_pair()
    server_task = asyncio.create_task(serve_session(RuntimeController(engine, program_build_id(engine.program)),
                                                    server_end))
    client = ControlClient(client_end)
    await client.hello()
    assert await client.request(Op.STATUS) == {1: 0, 2: engine.step_count}
    await client.close()
    await asyncio.wait_for(server_task, 2)


async def check_service_interactive():
    """run -i reads commands over an internal session until quit, which stops the service."""
    async def wait_forever(**kwargs):
        await asyncio.Event().wait()

    program = engine.program
    runtime = Engine(program)
    output = io.StringIO()
    with (contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
          unittest.mock.patch('sys.stdin', io.StringIO('pause\nset value 5\nget value\nquit\nresume\n')),
          unittest.mock.patch.object(wirely.misc.service, 'loop_until_sigint_and_throw', wait_forever)):
        controller = RuntimeController(runtime, program_build_id(program))
        assert await asyncio.wait_for(run_command.run_engine(runtime, controller, commands='batch', program=program),
                                      5) == 0
    lines = output.getvalue().splitlines()
    assert lines[1].startswith('paused, ') and lines[2] == '5' and 'running' not in output.getvalue(), lines


def instance_index(name):
    return [procedure.name for procedure in procedure_instances(engine.program)].index(name)


async def check_native_commands(server):
    value = instance_index('value.create')
    events = instance_index('events.create')
    client = await ControlClient.connect_unix(server.stream_path)

    def command(index, number, *argument):
        return client.request(Op.NATIVE_COMMAND, {1: index, 2: number, **({3: argument[0]} if argument else {})})

    try:
        await expect_error(ErrorCode.NOT_BOUND, command(value, 0))
        await client.request(Op.BIND_PROGRAM)
        # Negative command numbers are valid integers, but these targets do not have them.
        for index, number in [(value, 9), (value, -1), (instance_index('c.run'), 0),
                              (len(procedure_instances(engine.program)), 0)]:
            await expect_error(ErrorCode.NOT_FOUND, command(index, number))

        # DebugValueSource: 0 gets and 1 sets the value, in its binary representation in key 1.
        assert await command(value, 0) == {1: {1: (5).to_bytes(2, 'little')}}
        await client.request(Op.PAUSE)
        assert await command(value, 1, {1: (7).to_bytes(2, 'little')}) == {1: {}}
        await client.request(Op.STEP)
        assert engine.get_value('forward') == 7
        assert await command(value, 0, {}) == {1: {1: (7).to_bytes(2, 'little')}}
        for argument in ({1: b'\x07'}, {1: 'text'}, {}, b'\x07\x00'):
            await expect_error(ErrorCode.INVALID_ARGUMENT, command(value, 1, argument))
        await expect_error(ErrorCode.INVALID_ARGUMENT, command(value, 0, b''))

        # DebugEventSource: 0 gets the queued payload and 1 sends an event.
        assert await command(events, 0) == {1: {}}
        assert await command(events, 1, {1: b'\x03'}) == {1: {}}
        assert await command(events, 0) == {1: {1: b'\x03'}}
        try:
            await command(events, 1, {1: b'\x04'})
        except ControlError as error:
            assert (error.code, error.reason, error.detail) == (ErrorCode.COMMAND_FAILED, 1, None), error
        else:
            raise AssertionError('Expected COMMAND_FAILED')
        await client.request(Op.STEP)
        assert await command(events, 0) == {1: {}}
        await client.request(Op.RESUME)
    finally:
        await client.close()


async def check_unnamed_native_commands():
    program = load_program(write_program(engine.program))
    runtime = Engine(program)
    controller = RuntimeController(runtime, program_build_id(program))
    value = instance_index('value.create')
    assert controller.handle(Op.NATIVE_COMMAND, {1: value, 2: 1, 3: {1: b'\x09\x00'}}, bound=True) == {1: {}}
    assert controller.handle(Op.NATIVE_COMMAND, {1: value, 2: 0}, bound=True) == {1: {1: b'\x09\x00'}}
    runtime.step()
    with unittest.TestCase().assertRaises(ControlError) as caught:
        controller.handle(Op.NATIVE_COMMAND, {1: instance_index('c.run'), 2: 0}, bound=True)
    assert caught.exception.code == ErrorCode.NOT_FOUND

    async def request(op, body=None):
        if op == Op.BIND_PROGRAM:
            return controller.bind(body or {})[0]
        return controller.handle(op, body or {}, bound=True)

    async def requests(items):
        return [await request(op, body) for op, body in items]

    client = unittest.mock.Mock()
    client.framing.max_message_size = 4096
    client.request = request
    client.requests = requests
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert await wirely.cli.client._dump(client, argparse.Namespace(compiled_program=program, bank='all')) == 0
    assert 'temporary[0]' in output.getvalue() and 'None:' not in output.getvalue(), output.getvalue()


def check_node_commands():
    """Typed commands find nodes by name and encode values with their node type's types."""
    target = node_commands.resolve(engine.program, 'value')
    assert (target.instance, target.node_type) == (instance_index('value.create'), 'Std.DebugValueSource')
    assert target.encode('9') == {1: b'\x09\x00'} and target.decode({1: b'\x09\x00'}) == '9'
    events = node_commands.resolve(engine.program, 'events')
    assert str(events.value_type) == 'uint8' and events.decode({}) is None
    assert node_commands.command_targets(engine.program) == {
        'get': {'value', 'events'}, 'set': {'value'}, 'send': {'events'}}
    for action, message in [
        (lambda: events.command('set'), '`events` (`Std.DebugEventSource`) has no command `set`; it has `get`, `send`'),
        (lambda: node_commands.resolve(engine.program, 'c'), '`c` has no typed commands'),
        (lambda: node_commands.resolve(engine.program, 'missing'), 'Cannot find node `missing`'),
        (lambda: target.encode('x'), "Invalid `uint16` value 'x'"),
        (lambda: dataclasses.replace(target, value_type=wirely.types.boolean).encode('2'),
         "Invalid `boolean` value '2'"),
    ]:
        try:
            action()
        except node_commands.NodeCommandError as error:
            assert str(error).startswith(message), error
        else:
            raise AssertionError(f'Expected {message}')


def check_node_tree():
    """The node tree lists nested nodes in declaration order with their types."""
    graph = wirely.compiler.compile_strs([('<tree>', '''
node type Inner {
  default output: value boolean;
  node source: Std.DebugValueSource { Type: boolean, Initial: false }
  connect source -> default output;
}
namespace Lib {
  singleton node type Shared {
    node source: Std.DebugValueSource { Type: boolean, Initial: false }
  }
  node type Outer {
    node inner: Inner {}
    node count: Std.DebugValueSource { Type: uint8, Initial: 3 }
    default output: value boolean;
    connect inner -> default output;
  }
}
node top: Lib.Outer {}
node shared: Lib.Shared {}
node repeated: Lib.Shared {}
state seen: boolean = false;
connect top -> seen;
''')], add_std=True)
    program = program_compiler.compile(graph)
    tree = [(node.depth, node.name, node.path, node.node_type, node.target is not None)
            for node in node_commands.node_tree(program)]
    assert tree == [(0, 'top', 'top', 'Lib.Outer', False),
                    (1, 'inner', 'top.inner', 'Inner', False),
                    (2, 'source', 'top.inner.source', 'Std.DebugValueSource', True),
                    (1, 'count', 'top.count', 'Std.DebugValueSource', True),
                    (0, 'shared', 'shared', 'Lib.Shared', False),
                    (1, 'source', 'shared.source', 'Std.DebugValueSource', True),
                    (0, 'repeated', 'repeated', 'Lib.Shared', False),
                    (1, 'source', 'repeated.source', 'Std.DebugValueSource', True)]
    assert [(node.depth, node.path) for node in node_commands.node_tree(program, 'top.inner')] == [
        (0, 'top.inner'), (1, 'top.inner.source')]

    assert [(node.depth, node.path) for node in node_commands.node_tree(program, 'shared')] == [
        (0, 'shared'), (1, 'shared.source')]
    first = node_commands.resolve(program, 'shared.source')
    repeated = node_commands.resolve(program, 'repeated.source')
    canonical = node_commands.resolve(program, 'Lib.Shared:source')
    assert first.instance == repeated.instance == canonical.instance
    assert {'shared.source', 'repeated.source'} <= node_commands.command_targets(program)['set']


async def check_options(server):
    client = await ControlClient.connect_unix(server.stream_path)
    try:
        assert await client.request(Op.GET_OPTIONS) == {1: {'debug': False}}
        assert await client.request(Op.SET_OPTIONS, {1: {'debug': True}}) == {}
        assert engine.debug
        assert await client.request(Op.GET_OPTIONS, {1: ['debug']}) == {1: {'debug': True}}
        await expect_error(ErrorCode.INVALID_ARGUMENT, client.request(Op.SET_OPTIONS, {1: {'debug': 1}}))
        # A request with an unknown option changes nothing; negative keys are reserved for the protocol.
        for options in ({'debug': False, 'nope': 1}, {-1: True}):
            await expect_error(ErrorCode.NOT_FOUND, client.request(Op.SET_OPTIONS, {1: options}))
        assert engine.debug
        await expect_error(ErrorCode.NOT_FOUND, client.request(Op.GET_OPTIONS, {1: ['nope']}))
        await client.request(Op.SET_OPTIONS, {1: {'debug': False}})
        assert not engine.debug
    finally:
        await client.close()


async def check_stream(server):
    client = await ControlClient.connect_unix(server.stream_path)
    try:
        assert client.window > 1
        status = await client.request(Op.STATUS)
        assert status == {1: 0, 2: engine.step_count}
        await expect_error(ErrorCode.WRONG_STATE, client.request(Op.STEP))

        steps = engine.step_count
        assert await client.request(Op.PAUSE) == {1: 1, 2: steps}
        assert await client.request(Op.STEP, {1: 2}) == {1: 2, 2: 0}
        assert engine.step_count == steps + 2

        await client.request(Op.BIND_PROGRAM)
        snapshot = await client.request(Op.SNAPSHOT)
        assert snapshot[2] == steps + 2
        assert snapshot[3] == [len(engine.state.data), len(engine.cache.data)]
        state = await client.request(Op.READ_SNAPSHOT, {1: snapshot[1], 2: 0, 3: [[0, 4]]})
        assert state == {1: (42).to_bytes(4, 'little')}
        assert state[1] == bytes(engine.state.data)
        # Snapshots keep their data while the runtime continues.
        assert await client.request(Op.STEP) == {1: 1, 2: 0}
        parts = await client.request(Op.READ_SNAPSHOT, {1: snapshot[1], 2: 1, 3: [[2, 2], [0, 2]]})
        assert parts == {1: bytes(engine.cache.data[2:4] + engine.cache.data[0:2])}
        await expect_error(ErrorCode.INVALID_ARGUMENT,
                           client.request(Op.READ_SNAPSHOT, {1: snapshot[1], 2: 0, 3: [[2, 4]]}))
        await expect_error(ErrorCode.INVALID_ARGUMENT,
                           client.request(Op.READ_SNAPSHOT, {1: snapshot[1], 2: 2, 3: []}))
        assert await client.request(Op.RELEASE_SNAPSHOT, {1: snapshot[1]}) == {}
        await expect_error(ErrorCode.NOT_FOUND,
                           client.request(Op.READ_SNAPSHOT, {1: snapshot[1], 2: 0, 3: []}))
        await expect_error(ErrorCode.UNKNOWN_OP, client.request(Op.UPLOAD_BEGIN))

        # Requests sent before earlier responses arrive are answered in order.
        results = await client.requests([(Op.STEP, {1: count}) for count in range(1, 21)])
        assert [result[1] for result in results] == list(range(1, 21))

        assert (await client.request(Op.RESUME))[1] == 0
        assert not engine.paused
    finally:
        await client.close()

    # Connections must start with the preface.
    reader, writer = await asyncio.open_unix_connection(server.stream_path)
    writer.write(b'XXXX\0\0')
    assert await reader.read() == b''
    writer.close()


async def check_tls(server, cert_path):
    context = ssl.create_default_context(cafile=cert_path)
    client = await ControlClient.connect_unix(server.stream_path, ssl=context, server_hostname='runtime')
    try:
        assert (await client.request(Op.STATUS))[1] == 0
    finally:
        await client.close()


async def check_serial(server):
    reader, writer = await asyncio.open_unix_connection(server.serial_path)
    framing = SerialFraming(reader, writer)

    async def exchange(message):
        await framing.write(message)
        return await framing.read()

    def hello(nonce=b'12345678'):
        return encode(Kind.REQUEST, 0, Op.HELLO, {1: 64, 2: nonce, 3: [0]})

    try:
        response = decode(await exchange(encode(Kind.REQUEST, 1, Op.STATUS, {})))
        assert (response.kind, response.body[1]) == (Kind.ERROR, ErrorCode.NO_SESSION)

        hello_response = await exchange(hello())
        assert decode(hello_response).body[5] == 1
        assert Op.BIND_PROGRAM in decode(hello_response).body[8]
        assert await exchange(hello()) == hello_response

        # Invalid CBOR, data after the body, and items outside the subset are malformed.
        for body in [b'\xff', b'\x5f', b'\xa0\x00', b'\xf7', b'\xf9\x00\x00']:
            response = decode(await exchange(bytes([Kind.REQUEST, 1, Op.STATUS]) + body))
            assert (response.kind, response.body[1]) == (Kind.ERROR, ErrorCode.MALFORMED), body

        await exchange(encode(Kind.REQUEST, 1, Op.PAUSE, {}))
        steps = engine.step_count
        step = encode(Kind.REQUEST, 2, Op.STEP, {})
        response = await exchange(step)
        # A retransmitted request gets the same response and does not run again.
        assert await exchange(step) == response
        assert engine.step_count == steps + 1

        # Frames with an invalid CRC are dropped without a response.
        status = encode(Kind.REQUEST, 3, Op.STATUS, {})
        data = status + (zlib.crc32(status) ^ 1).to_bytes(4, 'little')
        writer.write(cobs_encode(data) + b'\0')
        response = decode(await exchange(status))
        assert (response.kind, response.seq) == (Kind.RESPONSE, 3)

        response = decode(await exchange(encode(Kind.REQUEST, 5, Op.STATUS, {})))
        assert (response.kind, response.body[1]) == (Kind.ERROR, ErrorCode.BAD_SEQUENCE)

        # Responses that exceed the client's limit of 64 bytes are refused.
        await exchange(encode(Kind.REQUEST, 4, Op.BIND_PROGRAM, {1: b''}))
        snapshot = decode(await exchange(encode(Kind.REQUEST, 5, Op.SNAPSHOT, {}))).body
        response = decode(await exchange(encode(
            Kind.REQUEST, 6, Op.READ_SNAPSHOT, {1: snapshot[1], 2: 0, 3: [[0, 4]] * 20})))
        assert (response.kind, response.body[1]) == (Kind.ERROR, ErrorCode.TOO_LARGE)

        # A new Hello starts a new session, without the binding.
        await exchange(hello(b'abcdefgh'))
        response = decode(await exchange(encode(Kind.REQUEST, 1, Op.SNAPSHOT, {})))
        assert (response.kind, response.body[1]) == (Kind.ERROR, ErrorCode.NOT_BOUND)
        response = decode(await exchange(encode(Kind.REQUEST, 2, Op.RESUME, {})))
        assert (response.kind, response.body[1]) == (Kind.RESPONSE, 0)
    finally:
        writer.close()


async def run_client_command(*arguments, stdin='', status=0):
    parser = argparse.ArgumentParser()
    wirely.cli.client.add_arguments(parser)
    output = io.StringIO()
    with (contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
          unittest.mock.patch('sys.stdin', io.StringIO(stdin))):
        assert await wirely.cli.client.async_run(parser.parse_args(arguments)) == status
    return output.getvalue()


async def check_cli(server):
    serial = ['--unix-path', server.serial_path, '--protocol', 'serial']
    assert 'paused' in await run_client_command(*serial, 'pause')
    assert '2 steps' in await run_client_command(*serial, 'step', '--count', '2')
    output = await run_client_command('--unix-path', server.stream_path, '--program', program_path, 'dump')
    assert '  d: 42\n' in output
    assert '  c.run.output: 42\n' in output
    output = await run_client_command(*serial, 'dump', '--bank', 'state')
    assert '  00000000  2a 00 00 00\n' in output
    # Program files must match the running program.
    with tempfile.TemporaryDirectory(prefix='test_control.') as directory:
        other_path = os.path.join(directory, 'other.wrl')
        with open(program_path, encoding='utf-8') as source, open(other_path, 'w', encoding='utf-8') as other:
            other.write(source.read().replace('Value: 42', 'Value: 43'))
        output = await run_client_command('--unix-path', server.stream_path, '--program', other_path, 'dump',
                                          status=1)
        assert output == 'Error: The running program does not match the program files\n'
    assert 'running' in await run_client_command(*serial, 'resume')

    # Native commands take and print CBOR diagnostic notation.
    value = str(instance_index('value.create'))
    events = str(instance_index('events.create'))
    assert await run_client_command(*serial, 'call', value, '1', "{1: h'0900'}") == '{}\n'
    assert await run_client_command(*serial, '--program', program_path, 'call', value, '0') == "{1: h'0900'}\n"
    # While paused, the first event stays queued.
    await run_client_command(*serial, 'pause')
    assert await run_client_command(*serial, 'call', events, '1', "{1: h'05'}") == '{}\n'
    output = await run_client_command(*serial, 'call', events, '1', "{1: h'06'}", status=1)
    assert output == 'Error: COMMAND_FAILED: An event is already queued (reason 1)\n'
    await run_client_command(*serial, 'resume')

    # Typed commands use node names and the value text format. One script compiles the program once.
    script = 'set value 11\nget value\npause\nget events\nsend events 6\nget events\nsend events 7\n'
    output = await run_client_command(*serial, '--program', program_path, stdin=script, status=1)
    lines = output.splitlines()
    assert (lines[0], lines[2:]) == ('11', ['none', '6', 'Error: COMMAND_FAILED: An event is already queued (reason 1)'])
    await run_client_command(*serial, 'resume')
    # Unexpected exceptions in a command print a traceback, as a failing command.
    with unittest.mock.patch.object(node_commands, 'resolve', side_effect=RuntimeError('unexpected')):
        output = await run_client_command(*serial, '--program', program_path, stdin='get value\n', status=1)
    assert 'Traceback' in output and output.endswith('RuntimeError: unexpected\n'), output
    assert await run_client_command(*serial, 'get', 'value', status=1) == "Error: `get` requires the program's .wrl files\n"

    # Runtime options.
    assert await run_client_command(*serial, 'debug') == 'off\n'
    assert await run_client_command(*serial, 'debug', 'on') == ''
    assert await run_client_command(*serial, 'debug') == 'on\n'
    assert await run_client_command(*serial, 'option') == '"debug": true\n'
    assert await run_client_command(*serial, 'option', 'debug', 'false') == ''
    assert await run_client_command(*serial, 'option', 'debug') == 'false\n'

    # list is local: it works without a runtime at the socket path.
    offline = ['--unix-path', os.path.join(os.path.dirname(server.stream_path), 'missing'), '--program', program_path]
    assert await run_client_command(*offline, 'list') == (
        'c           CachedConstant\n'
        'value       Std.DebugValueSource  uint16  get, set\n'
        'forward     Forward\n'
        'events      Std.DebugEventSource  uint8   get, send\n'
        'last_event  LastEvent\n')
    assert await run_client_command(*offline, 'list', 'missing', status=1) == 'Error: Cannot find node `missing`\n'
    assert await run_client_command(*offline[:2], 'list', status=1) == "Error: `list` requires the program's .wrl files\n"

    # Without a command, commands are read from stdin.
    output = await run_client_command(*serial, stdin='pause\nstep --count 2\nresume\n')
    assert 'paused' in output and '2 steps' in output and 'running' in output
    # Input that is not a terminal stops at the first failing command, as with --batch.
    for mode in ([], ['--batch']):
        output = await run_client_command(*serial, *mode, stdin='step\nstatus\n', status=1)
        assert output == 'Error: WRONG_STATE: Runtime is not paused\n'
    # With -i, reading continues after failing commands.
    output = await run_client_command(*serial, '-i', stdin='step\nstatus\n')
    assert output.startswith('Error: WRONG_STATE: Runtime is not paused\nrunning, '), output
    output = await run_client_command(*serial, '--batch', 'status', status=2)
    assert output == 'Error: --interactive and --batch read commands from stdin; omit the command\n'


async def run_test():
    check_cobs()
    check_node_commands()
    check_node_tree()
    await check_unnamed_native_commands()
    await check_client_ignores_notifications()
    await check_memory_framing()
    await check_service_interactive()
    run_task = asyncio.create_task(engine.run())
    try:
        with tempfile.TemporaryDirectory(prefix='test_control.') as directory:
            cert_path, key_path = write_certificate(directory)
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(cert_path, key_path)
            state_directory = os.path.join(directory, 'state')
            with lock_state_directory(state_directory):
                controller = RuntimeController(engine, program_build_id(engine.program))
                async with ControlServer(controller, state_directory, context) as server:
                    assert sorted(os.listdir(state_directory)) == ['control', 'control-serial', 'lock']
                    await check_locked(server)
                    await check_stream(server)
                    await check_binding(server)
                    await check_status(server)
                    await check_status_serial(server)
                    await check_native_commands(server)
                    await check_options(server)
                    await check_tls(server, cert_path)
                    await check_serial(server)
                    await check_cli(server)
                assert os.listdir(state_directory) == ['lock']
            with lock_state_directory(state_directory):
                pass
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
