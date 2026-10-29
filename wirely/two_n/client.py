import ssl
import base64
import hashlib
import json
import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import wirely.misc.service

from . import cert

import aiohttp

import logging


# Timeout for requests other than event pulls, in seconds.
REQUEST_TIMEOUT = 30
# How long ending a subscription waits for the device, in seconds.
UNSUBSCRIBE_TIMEOUT = 1


class TwoNError(Exception):
    """An error response or an invalid response from the device."""


class TwoNAPIError(TwoNError):
    """An error the device reports for a request in a valid response."""

    def __init__(self, message: str, *, code: Any) -> None:
        super().__init__(message)
        # The error code reported by the device.
        self.code = code


class _Unauthorized(Exception):
    """A 401 response, with its WWW-Authenticate header, which tells why the device refused."""

    def __init__(self, authenticate: str | None) -> None:
        super().__init__(authenticate)
        self.authenticate = authenticate


class LoggingMiddleware:
    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)

    async def __call__(
        self,
        request: aiohttp.ClientRequest,
        handler: aiohttp.ClientHandlerType,
    ) -> aiohttp.ClientResponse:
        start_time = time.monotonic()

        # Log request
        self.logger.info("[REQUEST] %s %s", request.method, request.url)
        if request.headers:
            self.logger.debug("[REQUEST HEADERS] %s", request.headers)

        # Execute request
        response = await handler(request)

        # Log response
        duration = time.monotonic() - start_time
        self.logger.info(
            "[RESPONSE] %s %s - Status: %s - Duration: %.3fs",
            request.method,
            request.url,
            response.status,
            duration,
        )
        self.logger.debug("[RESPONSE HEADERS] %s", response.headers)

        return response


def get_username_password(fn: str) -> tuple[str, str]:
    with open(fn, 'r') as file:
        username = file.readline().strip()
        password = file.readline().strip()

    if username == '' or password == '':
        raise Exception('Failed to read username and password from {!r}'.format(fn))

    return username, password


class MyFingerprint(aiohttp.Fingerprint):
    def __init__(self, tlsa_records: Sequence[str]) -> None:
        super().__init__(b'0' * 32)
        self.tlsa_records = tlsa_records

    def check(self, transport: asyncio.Transport) -> None:
        if not transport.get_extra_info("sslcontext"):
            return
        sslobj = transport.get_extra_info("ssl_object")
        try:
            cert.check_certificate_fingerprint(None, sslobj, self.tlsa_records)
        except Exception as e:
            host, port, *_ = transport.get_extra_info("peername")
            got = hashlib.sha256(sslobj.getpeercert(binary_form=True) or b'').digest()
            raise aiohttp.ServerFingerprintMismatch(self.fingerprint, got, host, port) from e


class Client:
    """A connection to the HTTP API of a 2N device; it can be entered again after exiting."""

    def __init__(self, *, hostname: str, port: int = 443, tlsa: Sequence[str],
                 http_api_username_password: tuple[str, str] | None = None, ajax_sid: str | None = None,
                 ajax_username_password: tuple[str, str] | None = None, enable_logging: bool = False) -> None:
        self.hostname = hostname
        self.port = port
        self.http_api_username_password = http_api_username_password
        self.ajax_sid = ajax_sid
        self.ajax_username_password = ajax_username_password

        if enable_logging:
            self.logging_middleware: LoggingMiddleware | None = LoggingMiddleware()
        else:
            self.logging_middleware = None

        if self.http_api_username_password is not None:
            username, password = self.http_api_username_password
            self.digest_middleware: aiohttp.DigestAuthMiddleware | None = aiohttp.DigestAuthMiddleware(
                login=username, password=password)
        else:
            self.digest_middleware = None

        self._context: contextlib.AbstractAsyncContextManager['Client'] | None = None

        self.client_session: aiohttp.ClientSession | None = None

        self.tls_validation = MyFingerprint(tlsa)

    @contextlib.asynccontextmanager
    async def _enter(self) -> AsyncIterator['Client']:
        if self.client_session is not None:
            raise Exception('Trying to re-enter Client')
        # print('ENTER', flush=True)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as session:
            try:
                self.client_session = session
                yield self
            finally:
                # print('EXIT', flush=True)
                self.client_session = None

    async def __aenter__(self) -> 'Client':
        if self._context is not None:
            raise Exception('Trying to re-enter Client')
        self._context = self._enter()
        return await self._context.__aenter__()

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool | None:
        assert self._context is not None
        context = self._context
        self._context = None
        return await context.__aexit__(exc_type, exc_value, traceback)

    async def http_request(self, method: str, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        if self.client_session is None:
            raise Exception('Client is not entered')

        if not url.startswith('/'):
            raise Exception(f'Invalid URL: {url!r}')

        if 'middlewares' not in kwargs:
            kwargs['middlewares'] = ()
        if self.logging_middleware is not None:
            kwargs['middlewares'] += (self.logging_middleware,)

        return await self.client_session.request(method, f'https://{self.hostname}:{self.port}{url}', allow_redirects=False, ssl=self.tls_validation, **kwargs)

    async def api_request(self, method: str, url: str, *, do_authentication: bool = True,
                          **kwargs: Any) -> aiohttp.ClientResponse:
        kwargs = dict(kwargs)

        if do_authentication:
            if self.http_api_username_password is None:
                raise Exception('No HTTP API username/password given')

            # kwargs['auth'] = aiohttp.BasicAuth(login=username, password=password)
            # Note: This needs aiohttp >= 3.12
            kwargs['middlewares'] = (self.digest_middleware,)

        return await self.http_request(method, url, **kwargs)

    async def json_request(self, method: str, url: str, **kwargs: Any) -> Any:
        # Devices occasionally reject a request with 401 although the credentials are correct, so a request with
        # authentication is sent once more after a 401.
        attempts = 2 if kwargs.get('do_authentication', True) else 1
        for attempt in range(attempts):
            try:
                return await self._json_request(method, url, **kwargs)
            except _Unauthorized as e:
                message = (f'During {method} request to {url} got status code 401 '
                           f'(WWW-Authenticate: {e.authenticate!r})')
                if attempt + 1 == attempts:
                    raise TwoNError(message) from None
                print(f'Warning: {message}; retrying', flush=True)
        raise AssertionError('unreachable')

    async def _json_request(self, method: str, url: str, **kwargs: Any) -> Any:
        async with await self.api_request(method, url, **kwargs) as response:
            # print(response.headers)
            if response.status == 401:
                raise _Unauthorized(response.headers.get('WWW-Authenticate'))
            if response.status != 200:
                raise TwoNError(f'During {method} request to {url} got status code {response.status}')

            # print(response.status)
            # print(response.read().decode('utf-8'))

            content_type = response.headers['Content-Type']
            # if content_type != 'application/json':
            # TODO: Allow charset? Should not be there, but aiohttp web json_response() will add it.
            if content_type != 'application/json' and content_type != 'application/json; charset=utf-8':
                raise TwoNError(f'During {method} request to {url}: Got unexpected content type {content_type!r}')

            try:
                data = json.loads(await response.read())
                success = data['success']
                if not success:
                    code = data['error']['code']
                    description = data['error']['description']
                    param = data['error'].get('param')
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                raise TwoNError(f'During {method} request to {url}: Got invalid response: {e!r}') from e
            if not success:
                raise TwoNAPIError(f'During {method} request to {url} got error {code!r}: {description!r} {param!r}',
                                   code=code)

            return data.get('result')

    async def ajax_login(self) -> None:
        if self.ajax_username_password is None:
            raise Exception('No AJAX username/password given')
        username, password = self.ajax_username_password

        json_data = {
            'user': username,
            'password': password,
        }
        json_str = json.dumps(json_data)

        with aiohttp.MultipartWriter("form-data") as mp:
            mp.append(json_str).set_content_disposition('form-data', name='blob-json')
            response = await self.json_request('POST', '/api/auth/login', do_authentication=False, data=mp)
        # print(response)
        self.ajax_sid = response['sid']

    async def ajax_request(self, sid: str, requests: Any) -> Any:
        response = await self.http_request('POST', '/ajax', params={'sid': sid}, json=requests)

        if response.status != 200:
            raise Exception(f'During AJAX request for {requests} got status code {response.status}')

        content_type = response.headers['Content-Type']
        if content_type != 'application/json':
            raise Exception(f'During AJAX request for {requests}: Got unexpected content type {content_type!r}')

        data = json.loads(await response.read())

        #print(json.dumps(response, indent=2))

        return data

    async def request(self, url: str) -> None:
        response = await self.json_request("GET", "/api/" + url)

        print(json.dumps(response, indent=2))

    async def do_ajax_request(self, requests_str: str | None = None) -> None:
        if self.ajax_sid is None:
            raise Exception('No AJAX SID given')
        sid = self.ajax_sid

        if requests_str is None:
            requests = [
                {
                    "command": "db.get",
                    "path": "State.Vbus.Device[0].ConfigString",
                },
                {
                    "command": "db.get",
                    "path": "State.Vbus.Device[1].ConfigString",
                },
                {
                    "command": "db.get",
                    "path": "State.Vbus.Device[2].ConfigString",
                },
            ]
        else:
            requests = json.loads(requests_str)

        response = await self.ajax_request(sid, requests)

        print(json.dumps(response, indent=2))


class LogReceiver:
    def __init__(self, client: Client, *, include: Sequence[str] | None = None,
                 filter: Sequence[str] | None = None, verbose: bool = False) -> None:
        self.client = client
        self.include = include
        self.filter = filter
        self.verbose = verbose

        self.subscribe_params: dict[str, Any] = {}
        if include is not None:
            self.subscribe_params['include'] = include
        if filter is not None:
            self.subscribe_params['filter'] = ','.join(filter)

        self.handlers: dict[str, list[Callable[[Any], object]]] = {}
        self.all_handlers: list[Callable[[Any], object]] = []

        self.subscription: Any = None

    #     self.context = self._context()

    # @contextlib.asynccontextmanager
    # async def _context(self):
    #     pass

    @property
    def is_subscribed(self) -> bool:
        return self.subscription is not None

    # TODO: Allow removing handlers?
    def add_handler(self, event_names: Sequence[str], handler: Callable[[Any], object]) -> None:
        self.all_handlers.append(handler)
        for event_name in event_names:
            self.handlers.setdefault(event_name, []).append(handler)

    async def receive(self, on_subscribed: Callable[[], Awaitable[object]] | None = None) -> None:
        """Subscribe, await on_subscribed(), and pass events to the handlers until a request fails.

        Reading states in on_subscribed() does not miss changes, because changes after subscribing are pulled
        afterwards. The subscription is ended when this is cancelled; after a failed request, the device is probably
        unreachable, so it is left to expire on the device.
        """
        if self.verbose:
            print('Subscribing...', end='', flush=True)
        # TODO: Use POST instead of GET?
        subscription = await self.client.json_request('POST', '/api/log/subscribe', data=self.subscribe_params)
        if self.verbose:
            print(' done.', flush=True)
        # print(subscription)
        try:
            id = int(subscription['id'])
        except (KeyError, TypeError, ValueError) as e:
            raise TwoNError(f'Got invalid subscription: {subscription!r}') from e
        self.subscription = subscription
        try:
            if on_subscribed is not None:
                await on_subscribed()
            await self._run()
        except asyncio.CancelledError:
            if self.verbose:
                print('Unsubscribing...', end='', flush=True)
            with contextlib.suppress(aiohttp.ClientError, TimeoutError, TwoNError):
                async with asyncio.timeout(UNSUBSCRIBE_TIMEOUT):
                    await self.client.json_request('POST', '/api/log/unsubscribe', data={'id': id})
            if self.verbose:
                print(' done.', flush=True)
            raise
        finally:
            self.subscription = None

    async def _run(self) -> None:
        timeout = 60
        http_timeout = aiohttp.ClientTimeout(total=timeout + 30, connect=30, sock_connect=30)

        assert self.subscription is not None
        id = int(self.subscription['id'])
        initial_handled = False

        while True:
            response = await self.client.json_request('POST', '/api/log/pull', data={'id': id, 'timeout': timeout if initial_handled else 0}, timeout=http_timeout)
            # print(response)
            try:
                events = response['events']
            except (KeyError, TypeError) as e:
                raise TwoNError(f'Got invalid events: {response!r}') from e
            for event in events:
                event['_type'] = 'new' if initial_handled else 'old'
                name = event['event']
                if self.verbose:
                    print(f'Got {name!r} event: {event}')
                if name in self.handlers:
                    for handler in self.handlers[name]:
                        handler(event)
            if len(events) == 0 and not initial_handled:
                initial_handled = True
                for handler in self.all_handlers:
                    handler(None)


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--hostname', type=str, required=True)
    parser.add_argument('--tlsa', type=str, nargs='+', required=True)
    parser.add_argument('--password-file', type=str)
    parser.add_argument('--ajax', action='store_true')
    parser.add_argument('--ajax-sid', type=str)
    parser.add_argument('--ajax-password-file', type=str)
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('url', type=str, nargs='?')
    args = parser.parse_args()

    enable_logging = False
    if args.trace:
        logging.basicConfig(level=logging.DEBUG)
        enable_logging = True

    http_api_username_password = None
    if args.password_file is not None:
        http_api_username_password = get_username_password(args.password_file)

    ajax_username_password = None
    if args.ajax_password_file is not None:
        ajax_username_password = get_username_password(args.ajax_password_file)

    async with Client(hostname=args.hostname, tlsa=args.tlsa, http_api_username_password=http_api_username_password, ajax_sid=args.ajax_sid, ajax_username_password=ajax_username_password, enable_logging=enable_logging) as client:
        if args.ajax:
            if client.ajax_sid is None:
                await client.ajax_login()
            if args.url is not None:
                await client.do_ajax_request(args.url)
            else:
                await client.do_ajax_request()
        elif args.url is not None:
            await client.request(args.url)
        else:
            log_receiver = LogReceiver(client, verbose=args.verbose, include='all')
            log_receiver.add_handler(['KeyPressed', 'KeyReleased'], lambda event: print('--STARTUP--' if event is None else f'{event["event"]} {event["params"]["key"]!r} {event["_type"]}'))
            import wirely.misc.service
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(log_receiver.receive())
                    await wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw())
            except* wirely.misc.service.TerminateProgram:
                # TODO: Handle SIGTERM differently?
                # print('Terminated.', flush=True)
                import signal
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                signal.raise_signal(signal.SIGINT)


if __name__ == '__main__':
    asyncio.run(main())
