# A fake 2N device for tests. Its replies follow the HTTP API manual for firmware version 2.48 and the behavior of a
# 2N IP Verso 2.0 running 2.48.1.

import ssl
import base64
import json
import asyncio
import collections
import contextlib
import time
import tempfile
import datetime
import math
import secrets
import socket
from collections.abc import AsyncIterator
from typing import Any

from . import cert

import wirely.misc.service
from wirely.util import verbose_print

import OpenSSL
import cryptography.x509

import aiohttp
import aiohttp.web
import multidict


# The most events one /api/log/pull returns.
MAX_PULLED_EVENTS = 128


class ServerInfo:
    def __init__(self, server: 'Server', sockname: Any) -> None:
        self.server = server
        self.sockname = sockname

        if len(sockname) != 2:
            raise Exception('len(sockname) != 2')
        self.host = sockname[0]
        if self.host != '127.0.0.1':
            raise Exception("self.host != '127.0.0.1'")
        self.port = sockname[1]


class Server:
    def __init__(self) -> None:
        # Create key pair
        key = OpenSSL.crypto.PKey()
        key.generate_key(OpenSSL.crypto.TYPE_RSA, 2048)

        # Create self-signed cert
        certificate = OpenSSL.crypto.X509()
        # certificate.get_subject().CN = 'cn'
        certificate.set_serial_number(cryptography.x509.random_serial_number())
        certificate.gmtime_adj_notBefore(0)
        certificate.gmtime_adj_notAfter(10*365*24*60*60)
        certificate.set_issuer(certificate.get_subject())
        certificate.set_pubkey(key)
        certificate.sign(key, 'sha256')

        self.tls_cert = OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_PEM, certificate)
        self.tls_cert_asn1 = OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_ASN1, certificate)
        self.tls_key = OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, key)
        self.tlsa = cert.build_certificate_fingerprint(self.tls_cert_asn1)

        # While False, every request is answered with an error, as if the device were unavailable.
        self.available = True
        # The number of requests to answer with 401 by path, as devices occasionally do despite correct credentials.
        # Each such 401 contains a new digest challenge, so the client's digest middleware sends the request again.
        self.unauthorized: dict[str, int] = {}
        # Paths whose requests get no answer until release_hanging(), as if the device stopped responding.
        self.hanging: set[str] = set()
        self.hanging_requests: list[asyncio.Future[None]] = []
        # The number of requests by path that are not refused, so it does not depend on whether the client
        # authenticates preemptively.
        self.requests: collections.Counter[str] = collections.Counter()
        # The timeouts of the pulls, in order.
        self.pull_timeouts: list[int] = []

        self.app = aiohttp.web.Application(middlewares=[self.availability_middleware])
        self.app.add_routes([
            aiohttp.web.get('/', self.hello),
            aiohttp.web.get('/api/log/subscribe', self.api_log_subscribe),
            aiohttp.web.post('/api/log/subscribe', self.api_log_subscribe),
            aiohttp.web.get('/api/log/unsubscribe', self.api_log_unsubscribe),
            aiohttp.web.post('/api/log/unsubscribe', self.api_log_unsubscribe),
            aiohttp.web.get('/api/log/pull', self.api_log_pull),
            aiohttp.web.post('/api/log/pull', self.api_log_pull),
            aiohttp.web.get('/api/io/status', self.api_io_status),
            aiohttp.web.get('/api/io/ctrl', self.api_io_ctrl),
            aiohttp.web.post('/api/io/ctrl', self.api_io_ctrl),
            aiohttp.web.get('/api/switch/status', self.api_switch_status),
        ])

        self.subscriptions: dict[int, dict[str, Any]] = {}
        self.next_subscription_id = 1

        self.wait_event = asyncio.Event()

        self.next_event_id = 1
        self.startup_time = time.monotonic_ns()

        self.next_session = 1

        # The ports of a 2N IP Verso 2.0 with an extension module, in the order /api/io/status lists them.
        self.ports = {
            name: {
                'type': type,
                'state': 0,
            }
            for name, type in [
                ('relay1', 'output'),
                ('output1', 'output'),
                ('input1', 'input'),
                ('led_secured', 'output'),
                ('ext3.relay1', 'output'),
                ('ext3.relay2', 'output'),
                ('ext3.input1', 'input'),
                ('ext3.input2', 'input'),
                ('ext3.tamper', 'input'),
            ]
        }

        self.switches = {
            number: {
                'active': False,
                'locked': False,
                'held': False,
            }
            for number in range(1, 5)
        }

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.bind(('127.0.0.1', 0))
            self.info = ServerInfo(self, self.socket.getsockname())
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release the reserved socket before startup or after shutdown."""
        self.socket.close()

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[ServerInfo]:
        try:
            with tempfile.TemporaryDirectory(prefix='test_https.') as tmp:
                crt_fn = tmp + '/crt.crt'
                key_fn = tmp + '/key.key'
                with open(crt_fn, 'wb') as file:
                    file.write(self.tls_cert)
                with open(key_fn, 'wb') as file:
                    file.write(self.tls_key)

                runner = aiohttp.web.AppRunner(self.app, handler_cancellation=True)
                try:
                    await runner.setup()
                    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ssl_context.load_cert_chain(certfile=crt_fn, keyfile=key_fn)
                    site = aiohttp.web.SockSite(runner, self.socket, ssl_context=ssl_context)
                    await site.start()
                    yield self.info
                finally:
                    await runner.cleanup()
        finally:
            self.close()

    async def run_and_wait(self) -> None:
        try:
            async with self.run() as info:
                print(f'https://{info.host}:{info.port}/')
                await wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw())
        except* wirely.misc.service.TerminateProgram:
            # Print a newline to avoid leftover '^C' at beginning of line.
            print(flush=True)
        finally:
            self.close()

    @aiohttp.web.middleware
    async def availability_middleware(self, request: aiohttp.web.Request,
                                      handler: aiohttp.typedefs.Handler) -> aiohttp.web.StreamResponse:
        if not self.available:
            return aiohttp.web.Response(status=503, text='Unavailable')
        # API requests without digest authentication get a challenge. The digest itself is not verified.
        if request.path.startswith('/api/') and not request.headers.get('Authorization', '').startswith('Digest '):
            return self.unauthorized_response()
        if self.unauthorized.get(request.path, 0) > 0:
            self.unauthorized[request.path] -= 1
            return self.unauthorized_response()
        self.requests[request.path] += 1
        if request.path in self.hanging:
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self.hanging_requests.append(future)
            await future
        return await handler(request)

    def release_hanging(self) -> None:
        """Answer the hanging requests; the server is shared by the tests of a process."""
        self.hanging.clear()
        for future in self.hanging_requests:
            if not future.done():
                future.set_result(None)
        self.hanging_requests.clear()

    @staticmethod
    def json_reply(data: Any, *, status: int = 200, headers: dict[str, str] | None = None) -> aiohttp.web.Response:
        """Reply with JSON like the device, whose Content-Type has no charset."""
        return aiohttp.web.Response(status=status, body=json.dumps(data, indent=2).encode('utf-8'),
                                    headers={**(headers or {}), 'Content-Type': 'application/json'})

    @staticmethod
    def error_reply(code: int, description: str, param: str | None = None, *, status: int = 200,
                    headers: dict[str, str] | None = None) -> aiohttp.web.Response:
        error: dict[str, Any] = {
            'code': code,
            'description': description,
        }
        if param is not None:
            error['param'] = param
        return Server.json_reply({'success': False, 'error': error}, status=status, headers=headers)

    @staticmethod
    def unauthorized_response() -> aiohttp.web.Response:
        challenge = f'Digest realm="HTTP API (3/00000020)", nonce="{secrets.token_hex(16)}", qop="auth"'
        return Server.error_reply(9, 'authorization required', status=401, headers={'WWW-Authenticate': challenge})

    @staticmethod
    def success_reply(result: Any = None) -> aiohttp.web.Response:
        """Reply with success, and with a result unless the function returns no parameters."""
        if result is None:
            return Server.json_reply({'success': True})
        return Server.json_reply({'success': True, 'result': result})

    @staticmethod
    def get_int(params: Any, name: str, default: int | None = None) -> int | aiohttp.web.Response:
        """Get an integer parameter, or an error reply if it is missing or invalid."""
        if name not in params:
            if default is not None:
                return default
            return Server.error_reply(11, 'missing mandatory parameter', name)
        try:
            value = int(params[name])
        except ValueError:
            return Server.error_reply(12, 'invalid parameter value', name)
        if value < 0:
            return Server.error_reply(12, 'invalid parameter value', name)
        return value

    def drop_subscriptions(self) -> None:
        """Forget all subscriptions, as a restarting device does; pending pulls fail."""
        self.subscriptions.clear()
        self.wait_event.set()

    @staticmethod
    def unknown_subscription() -> aiohttp.web.Response:
        return Server.error_reply(12, 'invalid subscription id', 'id')

    async def hello(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        return aiohttp.web.Response(text="Hello, world")

    @staticmethod
    async def get_params(request: aiohttp.web.Request) -> Any:
        pars: multidict.MultiDict[Any] = multidict.MultiDict(request.query)
        post_data = await request.post()
        for key, value in post_data.items():
            pars.add(key, value)
        return pars

    async def api_log_subscribe(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        params = await self.get_params(request)
        par_filter = params.get('filter')
        par_include = params.get('include')
        verbose_print('api_log_subscribe', par_filter, par_include)
        id = self.next_subscription_id
        self.next_subscription_id += 1
        reply = {
            'success': True,
            'result': {
                'id': id,
            },
        }
        # TODO: Close subscriptions that are not pulled for their duration (default 90 s), as the device does.
        self.subscriptions[id] = {
            'pending': [],
        }
        verbose_print(f'Added {list(self.subscriptions)}')
        return self.json_reply(reply)

    async def api_log_unsubscribe(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        params = await self.get_params(request)
        par_id = self.get_int(params, 'id')
        if isinstance(par_id, aiohttp.web.Response):
            return par_id
        verbose_print('api_log_unsubscribe', par_id)
        verbose_print(f'Subscriptions {list(self.subscriptions)!r}')
        if par_id not in self.subscriptions:
            return self.unknown_subscription()
        del self.subscriptions[par_id]
        return self.success_reply()

    async def api_log_pull(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        params = await self.get_params(request)
        par_id = self.get_int(params, 'id')
        if isinstance(par_id, aiohttp.web.Response):
            return par_id
        par_timeout = self.get_int(params, 'timeout', 0)
        if isinstance(par_timeout, aiohttp.web.Response):
            return par_timeout
        self.pull_timeouts.append(par_timeout)

        verbose_print('api_log_pull', par_id, par_timeout)
        verbose_print(f'Lookup {par_id!r} {list(self.subscriptions)}')
        if par_id not in self.subscriptions:
            return self.unknown_subscription()
        subscription = self.subscriptions[par_id]
        remaining_timeout = par_timeout
        while remaining_timeout > 0 and len(subscription['pending']) == 0:
            # TODO: Filter events?
            try:
                async with asyncio.timeout(remaining_timeout):
                    await self.wait_event.wait()

                    # TODO: Is this correct it there are multiple subscribers?
                    self.wait_event.clear()
            except TimeoutError:
                break
            if par_id not in self.subscriptions:
                return self.unknown_subscription()
        # Larger amounts of events are pulled in batches.
        events = subscription['pending'][:MAX_PULLED_EVENTS]
        del subscription['pending'][:MAX_PULLED_EVENTS]
        return self.success_reply({'events': events})

    async def api_io_status(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        params = await self.get_params(request)
        par_port = params.get('port')

        def format_data(key: Any) -> dict[str, Any]:
            return {
                'port': key,
                'state': self.ports[key]['state'],
            }

        if par_port is None:
            results = [format_data(key) for key in self.ports]
        elif par_port in self.ports:
            results = [format_data(par_port)]
        else:
            return self.error_reply(12, 'invalid parameter value', 'port')
        return self.success_reply({'ports': results})

    async def api_switch_status(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        params = await self.get_params(request)
        par_switch: int | None = None
        if 'switch' in params:
            value = self.get_int(params, 'switch')
            if isinstance(value, aiohttp.web.Response):
                return value
            par_switch = value

        def format_data(key: int) -> dict[str, Any]:
            return {
                'switch': key,
                **self.switches[key],
            }

        if par_switch is None:
            results = [format_data(key) for key in self.switches]
        elif par_switch in self.switches:
            results = [format_data(par_switch)]
        else:
            return self.error_reply(12, 'invalid parameter value', 'switch')
        return self.success_reply({'switches': results})

    async def api_io_ctrl(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        params = await self.get_params(request)
        for name in ('port', 'action'):
            if name not in params:
                return self.error_reply(11, 'missing mandatory parameter', name)
        par_port = params['port']
        action = params['action']

        if par_port not in self.ports:
            return self.error_reply(12, 'invalid parameter value', 'port')
        if action == 'off':
            new_value = False
        elif action == 'on':
            new_value = True
        else:
            return self.error_reply(12, 'invalid parameter value', 'action')

        self.set_io_state(par_port, new_value)
        return self.success_reply()

    def emit_event_raw(self, event: dict[str, Any]) -> None:
        for subscription in self.subscriptions.values():
            subscription['pending'].append(event)
        verbose_print(f'Emitted for {len(self.subscriptions.values())} listeners: {event}', flush=True)
        self.wait_event.set()

    def emit_event(self, event_type: str, params: dict[str, Any]) -> None:
        event_id = self.next_event_id
        self.next_event_id += 1

        now_utc = datetime.datetime.now(datetime.timezone.utc)

        self.emit_event_raw({
            'id': event_id,
            'hash': secrets.token_hex(8),
            'tzShift': 60,
            'utcTime': math.floor(now_utc.timestamp()),
            'upTime': (time.monotonic_ns() - self.startup_time) // 1000000000,
            'event': event_type,
            'params': params,
        })

    def emit_key_press(self, key_name: str) -> None:
        self.emit_event('KeyPressed', {
            'key': key_name,
        })

    def emit_key_release(self, key_name: str) -> None:
        self.emit_event('KeyReleased', {
            'key': key_name,
        })

    def set_io_state(self, port_name: str, value: bool) -> None:
        if port_name not in self.ports:
            raise Exception('port_name not in self.ports')
        if type(value) != bool:
            raise Exception('type(value) != bool')

        port = self.ports[port_name]
        port['state'] = int(value)
        if port['type'] == 'input':
            event_name = 'InputChanged'
        elif port['type'] == 'output':
            event_name = 'OutputChanged'
        else:
            raise Exception('Unknown port type')
        self.emit_event(event_name, {
            'port': port_name,
            'state': value,
        })

    def set_switch_state(self, switch_number: int, value: bool) -> None:
        if switch_number not in self.switches:
            raise Exception('switch_number not in self.switches')
        if type(value) != bool:
            raise Exception('type(value) != bool')

        switch = self.switches[switch_number]
        switch['active'] = value
        self.emit_event('SwitchStateChanged', {
            'switch': switch_number,
            'state': value,
        })

    def emit_code_entered(self, code: str) -> None:
        session = self.next_session
        self.next_session += 1
        self.emit_event('CodeEntered', {
            'session': session,
            'direction': 'in',
            'code': code,
            'input': 'keypad',
            'valid': False,
        })


def main() -> None:
    server = Server()
    try:
        print(server.tlsa)
        asyncio.run(server.run_and_wait())
    finally:
        server.close()


if __name__ == '__main__':
    main()
