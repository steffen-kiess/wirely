import ssl
import base64
import hashlib
import json
import asyncio
import contextlib
import time

from . import cert

import aiohttp

import logging

class LoggingMiddleware:
    def __init__(self):
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


def get_username_password(fn):
    with open(fn, 'r') as file:
        username = file.readline().strip()
        password = file.readline().strip()

    if username == '' or password == '':
        raise Exception('Failed to read username and password from {!r}'.format(fn))

    return username, password


class MyFingerprint(aiohttp.Fingerprint):
    def __init__(self, tlsa_records):
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


# TODO: Make sure that there is a timeout for requests, also for event pulls (http.client seems to have no timeout by default)
class Client:
    def __init__(self, *, hostname, port=443, tlsa, http_api_username_password=None, ajax_sid=None, ajax_username_password=None, enable_logging=False):
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

        self._context = self._enter()

        self.client_session = None

        self.tls_validation = MyFingerprint(tlsa)

    @contextlib.asynccontextmanager
    async def _enter(self):
        if self.client_session is not None:
            raise Exception('Trying to re-enter Client')
        # print('ENTER', flush=True)
        async with aiohttp.ClientSession() as session:
            try:
                self.client_session = session
                yield self
            finally:
                # print('EXIT', flush=True)
                self.client_session = None

    async def __aenter__(self):
        return await self._context.__aenter__()

    async def __aexit__(self, exc_type, exc_value, traceback):
        return await self._context.__aexit__(exc_type, exc_value, traceback)

    async def http_request(self, method, url, **kwargs):
        if self.client_session is None:
            raise Exception('Client is not entered')

        if not url.startswith('/'):
            raise Exception(f'Invalid URL: {url!r}')

        if 'middlewares' not in kwargs:
            kwargs['middlewares'] = ()
        if self.logging_middleware is not None:
            kwargs['middlewares'] += (self.logging_middleware,)

        return await self.client_session.request(method, f'https://{self.hostname}:{self.port}{url}', allow_redirects=False, ssl=self.tls_validation, **kwargs)

    async def api_request(self, method, url, *, do_authentication=True, **kwargs):
        kwargs = dict(kwargs)

        if do_authentication:
            if self.http_api_username_password is None:
                raise Exception('No HTTP API username/password given')

            # kwargs['auth'] = aiohttp.BasicAuth(login=username, password=password)
            # Note: This needs aiohttp >= 3.12
            kwargs['middlewares'] = (self.digest_middleware,)

        return await self.http_request(method, url, **kwargs)

    async def json_request(self, method, url, **kwargs):
        async with await self.api_request(method, url, **kwargs) as response:
            # print(response.headers)
            if response.status != 200:
                raise Exception(f'During {method} request to {url} got status code {response.status}')

            # print(response.status)
            # print(response.read().decode('utf-8'))

            content_type = response.headers['Content-Type']
            # if content_type != 'application/json':
            # TODO: Allow charset? Should not be there, but aiohttp web json_response() will add it.
            if content_type != 'application/json' and content_type != 'application/json; charset=utf-8':
                raise Exception(f'During {method} request to {url}: Got unexpected content type {content_type!r}')

            data = json.loads(await response.read())

            if not data['success']:
                code = data['error']['code']
                description = data['error']['description']
                param = data['error'].get('param')
                raise Exception(f'During {method} request to {url} got error {code!r}: {description!r} {param!r}')

            return data.get('result')

    async def ajax_login(self):
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

    async def ajax_request(self, sid, requests):
        response = await self.http_request('POST', '/ajax', params={'sid': sid}, json=requests)

        if response.status != 200:
            raise Exception(f'During AJAX request for {requests} got status code {response.status}')

        content_type = response.headers['Content-Type']
        if content_type != 'application/json':
            raise Exception(f'During AJAX request for {requests}: Got unexpected content type {content_type!r}')

        data = json.loads(await response.read())

        #print(json.dumps(response, indent=2))

        return data

    async def request(self, url):
        response = await self.json_request("GET", "/api/" + url)

        print(json.dumps(response, indent=2))

    async def do_ajax_request(self, requests_str=None):
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
    def __init__(self, client, *, include=None, filter=None, verbose=False):
        self.client = client
        self.include = include
        self.filter = filter
        self.verbose = verbose

        self.subscribe_params = {}
        if include is not None:
            self.subscribe_params['include'] = include
        if filter is not None:
            self.subscribe_params['filter'] = ','.join(filter)

        self.handlers = {}
        self.all_handlers = []

        self.subscription = None

    #     self.context = self._context()

    # @contextlib.asynccontextmanager
    # async def _context(self):
    #     pass

    @property
    def is_subscribed(self):
        return self.subscription is not None

    # TODO: Move somewhere else?
    @staticmethod
    @contextlib.asynccontextmanager
    async def start_task_context(task_group, coroutine):
        import wirely.misc.service

        task = task_group.create_task(wirely.misc.service.should_never_return(coroutine))
        try:
            yield task
        finally:
            # print('Canceling')
            task.cancel()
            # TODO: Should this wait for the task? Should there be a timeout for waiting?
            # TODO: How should this be done? (wait for future without throwing)
            try:
                await task
            except:
                pass
            # print('Canceling done')

    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        # TODO: Should this wait until the subscription has finished? Or until all initial events have been handled?

        # By doing this here instead of using "async with" in _run() this will wait until the subscription is finished
        # This is currently needed for IOState to be race-free.
        subscription = await async_exit_stack.enter_async_context(self.with_subscription())

        # await async_exit_stack.enter_async_context(self.context)
        task = await async_exit_stack.enter_async_context(LogReceiver.start_task_context(task_group, self._run()))

    # TODO: Allow removing handlers?
    def add_handler(self, event_names, handler):
        self.all_handlers.append(handler)
        for event_name in event_names:
            self.handlers.setdefault(event_name, []).append(handler)

    @contextlib.asynccontextmanager
    async def with_subscription(self):
        if self.verbose:
            print('Subscribing...', end='', flush=True)
        # TODO: Use POST instead of GET?
        self.subscription = await self.client.json_request('POST', '/api/log/subscribe', data=self.subscribe_params)
        if self.verbose:
            print(' done.', flush=True)
        # print(self.subscription)

        id = int(self.subscription['id'])

        try:
            yield self.subscription
        finally:
            if self.verbose:
                print('Unsubscribing...', end='', flush=True)
            await self.client.json_request('POST', '/api/log/unsubscribe', data={'id': id})
            self.subscription = None
            if self.verbose:
                print(' done.', flush=True)

    async def _run(self):
        timeout = 60
        http_timeout = aiohttp.ClientTimeout(total=timeout + 30, connect=30, sock_connect=30)

        assert self.subscription is not None
        id = int(self.subscription['id'])
        initial_handled = False

        while True:
            response = await self.client.json_request('POST', '/api/log/pull', data={'id': id, 'timeout': timeout if initial_handled else 0}, timeout=http_timeout)
            # print(response)
            events = response['events']
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


async def main():
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
                async with asyncio.TaskGroup() as tg, contextlib.AsyncExitStack() as async_exit_stack:
                    await log_receiver.start_tasks_async(tg, async_exit_stack)
                    await wirely.misc.service.should_never_return(wirely.misc.service.loop_until_sigint_and_throw())
            except* wirely.misc.service.TerminateProgram:
                # TODO: Handle SIGTERM differently?
                # print('Terminated.', flush=True)
                import signal
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                signal.raise_signal(signal.SIGINT)


if __name__ == '__main__':
    asyncio.run(main())
