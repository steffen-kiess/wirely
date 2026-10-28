"""Services that runtime tests use: KNX, MQTT, a fake 2N device, and output files."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import pwd
import shlex
import signal
import socket
import subprocess
import tempfile
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from wirely.compiler import model

if TYPE_CHECKING:
    from wirely.two_n.fake_server import Server, ServerInfo


@contextlib.asynccontextmanager
async def with_maybe_knxd(socket_path: str | None) -> AsyncIterator[str | None]:
    if socket_path is None:
        yield None
        return

    knxd_path = os.environ.get('KNXD_PATH', 'knxd')

    config_path = os.path.join(os.path.dirname(socket_path), 'knxd.ini')

    config_data = f'''
[main]
addr = 1.1.5
client-addrs = 1.1.100:100
connections = A.dummy,server_unix
# systemd = systemd
debug = debug

[A.dummy]
driver = dummy

[server_unix]
server = unixtunsrv
debug = debug-server
tunnel = tunnel
path = {socket_path}
'''

    with open(config_path, 'w', encoding='utf-8') as file:
        file.write(config_data)

    print('Starting knxd...', flush=True)
    # TODO: Avoid non-async waiting?
    process = subprocess.Popen([knxd_path, config_path])
    try:
        print('Waiting for knxd to start up...', flush=True)
        for i in range(100):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError) as e:
                exit_code = process.poll()
                if exit_code is not None:
                    raise Exception(f'knxd has exited with exit code {exit_code}')

                print('Waiting', flush=True)
                await asyncio.sleep(0.05)
        else:
            raise TimeoutError('Timed out waiting for knxd to start')
        print('knxd is running.', flush=True)

        monitor_command = os.environ.get('WIRELY_TEST_KNX_MONITOR')
        if not monitor_command:
            yield socket_path
        else:
            print('Starting monitor...', flush=True)
            command = monitor_command.replace('%s', shlex.quote(socket_path))
            monitor_process = subprocess.Popen(
                ['sh', '-c', command], start_new_session=True
            )
            try:
                print('Waiting for monitor...', flush=True)
                # TODO: Find out whether it is running somehow?
                # await asyncio.sleep(0.1)
                # TODO: This is just a guess, with new xknx version (3.x.x) 0.1s seems to be too short
                await asyncio.sleep(0.3)
                # print('monitor is running.', flush=True)

                yield socket_path
            finally:
                print('Stopping monitor...', flush=True)
                try:
                    os.killpg(monitor_process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                monitor_process.wait()
                print('Done stopping monitor.', flush=True)
    finally:
        print('Stopping knxd...', flush=True)
        process.kill()
        process.wait()
        print('Done stopping knxd.', flush=True)


# Dynamic security client of the test broker that may change its access rules.
MQTT_ADMIN_USERNAME = 'admin'
MQTT_ADMIN_PASSWORD = 'admin'
# A topic that anonymous clients may not subscribe to.
MQTT_DENIED_TOPIC = 'test/denied'


def mqtt_dynamic_security_config() -> dict[str, object]:
    """Allow everything except subscribing to MQTT_DENIED_TOPIC for anonymous clients."""
    salt = b'wirely-tests'
    password = hashlib.pbkdf2_hmac('sha512', MQTT_ADMIN_PASSWORD.encode('utf-8'), salt, 101)
    return {
        'defaultACLAccess': {'publishClientSend': True, 'publishClientReceive': True,
                             'subscribe': True, 'unsubscribe': True},
        'clients': [{
            'username': MQTT_ADMIN_USERNAME,
            'password': base64.b64encode(password).decode('ascii'),
            'salt': base64.b64encode(salt).decode('ascii'),
            'iterations': 101,
            'roles': [{'rolename': 'admin'}],
        }],
        'groups': [{'groupname': 'anonymous', 'roles': [{'rolename': 'anonymous'}]}],
        'anonymousGroup': 'anonymous',
        'roles': [
            {'rolename': 'admin', 'acls': [
                {'acltype': acltype, 'topic': '$CONTROL/dynamic-security/#', 'allow': True}
                for acltype in ('publishClientSend', 'publishClientReceive', 'subscribePattern')
            ]},
            {'rolename': 'anonymous', 'acls': [
                {'acltype': 'subscribePattern', 'topic': MQTT_DENIED_TOPIC, 'priority': 1, 'allow': False},
            ]},
        ],
    }


@contextlib.asynccontextmanager
async def with_maybe_mqtt(socket_path: str | None) -> AsyncIterator[str | None]:
    if socket_path is None:
        yield None
        return

    mosquitto_path = 'mosquitto'

    tmp = os.path.dirname(socket_path)
    config_path = os.path.join(tmp, 'mosquitto.conf')
    dynamic_security_path = os.path.join(tmp, 'dynamic-security.json')

    config_data = f'''
pid_file {tmp}/mosquitto.pid
persistence false
log_dest file {tmp}/mosquitto.log
listener 0 {socket_path}
allow_anonymous true
plugin mosquitto_dynamic_security.so
plugin_opt_config_file {dynamic_security_path}
'''

    with open(config_path, 'w', encoding='utf-8') as file:
        file.write(config_data)
    # The plugin writes this file after changes; mosquitto warns about access files readable by others.
    fd = os.open(dynamic_security_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        json.dump(mqtt_dynamic_security_config(), file)

    if os.geteuid() == 0:
        # Mosquitto drops root privileges before writing in this directory.
        try:
            broker_user = pwd.getpwnam('mosquitto')
        except KeyError:
            broker_user = pwd.getpwnam('nobody')
        os.chown(tmp, broker_user.pw_uid, broker_user.pw_gid)
        os.chown(config_path, broker_user.pw_uid, broker_user.pw_gid)
        os.chown(dynamic_security_path, broker_user.pw_uid, broker_user.pw_gid)

    print('Starting mosquitto...', flush=True)
    # TODO: Avoid non-async waiting?
    process = subprocess.Popen([mosquitto_path, '-c', config_path])
    try:
        print('Waiting for mosquitto to start up...', flush=True)
        for i in range(100):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError) as e:
                exit_code = process.poll()
                if exit_code is not None:
                    raise Exception(f'mosquitto has exited with exit code {exit_code}')

                print('Waiting', flush=True)
                await asyncio.sleep(0.05)
        else:
            raise TimeoutError('Timed out waiting for mosquitto to start')
        print('mosquitto is running.', flush=True)

        run_mqtt_sub = model.util.getEnvironmentBool('WIRELY_TEST_RUN_MQTT_SUB', False)
        if not run_mqtt_sub:
            yield socket_path
        else:
            print('Starting monitor...', flush=True)
            monitor_process = subprocess.Popen(['mosquitto_sub', '--unix', socket_path, '-v', '-t', '#'])
            try:
                print('Waiting for monitor...', flush=True)
                # TODO: Find out whether it is running somehow?
                await asyncio.sleep(0.3)
                # print('monitor is running.', flush=True)

                yield socket_path
            finally:
                print('Stopping monitor...', flush=True)
                monitor_process.kill()
                monitor_process.wait()
                print('Done stopping monitor.', flush=True)
    finally:
        print('Stopping mosquitto...', flush=True)
        process.kill()
        process.wait()
        print('Done stopping mosquitto.', flush=True)


@contextlib.asynccontextmanager
async def with_maybe_twon(server: Server | None) -> AsyncIterator[ServerInfo | None]:
    if server is None:
        yield None
        return

    async with server.run() as info:
        yield info


class TestResourceProvider:
    """Allocate fixtures for one compilation and test execution."""

    def __init__(self, exit_stack: contextlib.ExitStack) -> None:
        self.exit_stack = exit_stack
        self.knx_path: str | None = None
        self.mqtt_socket_path: str | None = None
        self.output_directory: str | None = None
        self.twon_server: Server | None = None

    def get_knx_path(self) -> str:
        if self.knx_path is None:
            directory = self.exit_stack.enter_context(
                tempfile.TemporaryDirectory(prefix='test_knxd.'),
            )
            self.knx_path = os.path.join(directory, 'knxnet')
        return self.knx_path

    def get_mqtt_uri(self) -> str:
        if self.mqtt_socket_path is None:
            directory = self.exit_stack.enter_context(
                tempfile.TemporaryDirectory(prefix='test_mosquitto.'),
            )
            self.mqtt_socket_path = os.path.join(directory, 'socket')
        return 'mqtt+unix://' + self.mqtt_socket_path

    def get_output_path(self, default: str) -> str:
        if self.output_directory is None:
            keep_directory = model.util.getEnvironmentBool('WIRELY_TEST_KEEP_TESTDIR', False)
            if keep_directory:
                self.output_directory = tempfile.mkdtemp(prefix='test_dir.')
                print(f"Created test dir in {self.output_directory} (won't be deleted)", flush=True)
            else:
                self.output_directory = self.exit_stack.enter_context(
                    tempfile.TemporaryDirectory(prefix='test_dir.'),
                )
                self.exit_stack.callback(print, 'Cleaning up test dir', flush=True)
                print(f'Created test dir in {self.output_directory}', flush=True)
        return os.path.join(self.output_directory, os.path.basename(default))

    def get_twon_info(self) -> 'ServerInfo':
        if self.twon_server is None:
            from wirely.two_n.fake_server import Server

            server = Server()
            self.exit_stack.callback(server.close)
            self.twon_server = server
        return self.twon_server.info
