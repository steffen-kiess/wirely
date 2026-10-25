import argparse
import asyncio
import contextlib
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
from wirely.control.client import ControlClient
from wirely.control.framing import SerialFraming, cobs_decode, cobs_encode
from wirely.control.messages import ControlError, ErrorCode, Kind, Op, decode, encode
from wirely.control.server import ControlServer
from wirely.misc.state_directory import StateDirectoryLocked, lock_state_directory
from wirely.program.writer import program_build_id

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
        for body in [b'\xff', b'\x5f', b'\xa0\x00', b'\x20', b'\xf9\x00\x00']:
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
    output = await run_client_command('--unix-path', server.stream_path, 'dump', '--program', program_path)
    assert '  d: 42\n' in output
    assert '  c:run.output: 42\n' in output
    output = await run_client_command(*serial, 'dump', '--bank', 'state')
    assert '  00000000  2a 00 00 00\n' in output
    # Program files must match the running program.
    with tempfile.TemporaryDirectory(prefix='test_control.') as directory:
        other_path = os.path.join(directory, 'other.wrl')
        with open(program_path, encoding='utf-8') as source, open(other_path, 'w', encoding='utf-8') as other:
            other.write(source.read().replace('Value: 42', 'Value: 43'))
        output = await run_client_command('--unix-path', server.stream_path, 'dump', '--program', other_path,
                                          status=1)
        assert output == 'Error: The running program does not match the program files\n'
    assert 'running' in await run_client_command(*serial, 'resume')

    # Without a command, commands are read from stdin.
    output = await run_client_command(*serial, stdin='pause\nstep --count 2\nresume\n')
    assert 'paused' in output and '2 steps' in output and 'running' in output
    # Input that is not a terminal stops at the first failing command.
    output = await run_client_command(*serial, stdin='step\nstatus\n', status=1)
    assert output == 'Error: WRONG_STATE: Runtime is not paused\n'


async def run_test():
    check_cobs()
    await check_client_ignores_notifications()
    run_task = asyncio.create_task(engine.run())
    try:
        with tempfile.TemporaryDirectory(prefix='test_control.') as directory:
            cert_path, key_path = write_certificate(directory)
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(cert_path, key_path)
            state_directory = os.path.join(directory, 'state')
            with lock_state_directory(state_directory):
                async with ControlServer(engine, state_directory, context,
                                         build_id=program_build_id(engine.program)) as server:
                    assert sorted(os.listdir(state_directory)) == ['control', 'control-serial', 'lock']
                    await check_locked(server)
                    await check_stream(server)
                    await check_binding(server)
                    await check_status(server)
                    await check_status_serial(server)
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
