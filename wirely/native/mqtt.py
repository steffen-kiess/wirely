import asyncio
import json
import dataclasses
import urllib
import contextlib
from typing import Any, Literal

import aiomqtt

import wirely
from wirely import Some
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.generated import mqtt as contracts
from wirely.native.support import check_state_type
from wirely.generated.enums import MQTTFormat
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.generated.structs import MQTTTemplateParts
from wirely.types.time import TICKS_PER_SECOND, Duration
from wirely.util import verbose_print


_JSON_FORMATS = {MQTTFormat.Default: None, MQTTFormat.SnakeCase: 'SnakeCase'}


def _json_format(option: Some[MQTTFormat] | None) -> str | None:
    if option is None:
        return None
    if option.value not in _JSON_FORMATS:
        raise ValueError(f'Unsupported MQTT format {int(option.value)}')
    return _JSON_FORMATS[option.value]


# Delay before the first reconnection attempt, doubled after each failed attempt up to the maximum.
RECONNECT_DELAY_MIN = 1.0
RECONNECT_DELAY_MAX = 60.0


def _keep_alive_seconds(keep_alive: Duration) -> int:
    """Round the keepalive interval up to whole seconds, as MQTT requires."""
    if keep_alive.ticks == 0:
        raise ValueError('MQTT keepalive must not be zero')
    seconds = -(-keep_alive.ticks // TICKS_PER_SECOND)
    if seconds > 65535:
        raise ValueError(f'MQTT keepalive {keep_alive.to_iso()} is longer than 65535 seconds')
    return seconds


class MQTTUriParseException(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class ParsedMQTTUri:
    transport: Literal['tcp', 'unix']
    use_tls : bool
    hostname: str
    port: int


def parse_mqtt_uri(uri: str) -> 'ParsedMQTTUri':
    split = urllib.parse.urlsplit(uri)
    if split.scheme == '':
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Not a URI (scheme is missing)'.format(uri))
    scheme = split.scheme.lower()

    if split.fragment != '':
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty fragment'.format(uri))
    query = urllib.parse.parse_qs(split.query, keep_blank_values=True, errors='strict', strict_parsing=False)

    def check_query(*allowed_keys: str) -> None:
        allowed = set(allowed_keys)
        for key in query:
            if key not in allowed:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got unknown parameter {!r}'.format(uri, key))

    def parse_path(path: str) -> str | None:
        if path == '':
            return None
        if not path.startswith('/'):
            raise Exception("not path.startswith('/')")
        res = ''
        for component in path[1:].split('/'):
            if ';' in component:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got semicolon in path'.format(uri))
            unq = urllib.parse.unquote(component, errors='strict')
            if '\0' in unq:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got encoded NUL in path'.format(uri))
            if '/' in unq:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got encoded slash in path'.format(uri))
            res += '/' + unq
        return res

    if scheme in ['mqtt', 'mqtts']:
        if scheme == 'mqtt':
            use_tls = False
        elif scheme == 'mqtts':
            use_tls = True
        else:
            raise Exception('Should not be reached')

        if split.path != '':
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty path'.format(uri))
        if split.netloc == '':
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Authority is missing'.format(uri))
        if split.username is not None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty username'.format(uri))
        if split.password is not None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty password'.format(uri))
        hostname = split.hostname
        if hostname is None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got no hostname'.format(uri))
        port = split.port
        if port is None:
            if use_tls:
                port = 8883
            else:
                port = 1883

        check_query()

        return ParsedMQTTUri(
            transport='tcp',
            use_tls=use_tls,
            hostname=hostname,
            port=port,
        )
    elif scheme in ['mqtt+unix']:
        path = parse_path(split.path)
        if path is None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Missing path'.format(uri))

        check_query()

        if split.netloc != '':
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got non-empty authority'.format(uri))

        return ParsedMQTTUri(
            transport='unix',
            use_tls=False,
            hostname=path,
            # Note: paho.mqtt does not like it if 0 is passed here
            #port=0,
            port=1,
        )
    else:
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Unknown scheme {!r}'.format(uri, scheme))


class ConnectionCreate(contracts.ConnectionCreate):
    def initialize(self, *, services: StateServices[Literal['broker']],
                   uri: str, keep_alive: Duration) -> contracts.ConnectionCreateResult:
        verbose_print('Create MQTT connection', flush=True)

        verbose_print(f'  Using URI: {uri!r}', flush=True)

        self.uri = uri
        self.parsed_uri = parse_mqtt_uri(uri)
        self.keep_alive = _keep_alive_seconds(keep_alive)

        # Clients register themselves while being initialized.
        self.receivers: dict[str, list[EventReceiverCreate]] = {}
        self.value_providers: list[ValueOptionProviderCreate] = []
        self.event_senders: list[EventSenderCreate] = []

        # The client while connected, otherwise None.
        self.client: aiomqtt.Client | None = None
        # Set while connected.
        self.connected_event = asyncio.Event()
        # Set on a disconnect until get_connected() has reported it.
        self.disconnect_pending = False

        # The last connection error and how often it has been logged.
        self.last_error: str | None = None
        self.last_error_count = 0
        return contracts.ConnectionCreateResult(broker=self)

    @property
    def connected(self) -> bool:
        return self.client is not None

    def create_client(self) -> aiomqtt.Client:
        # Note: Has to be called from the async loop so that asyncio.get_event_loop() is correct

        tls_params = None
        if self.parsed_uri.use_tls:
            tls_params = aiomqtt.TLSParameters()
        return aiomqtt.Client(protocol=aiomqtt.ProtocolVersion.V5, transport=self.parsed_uri.transport, hostname=self.parsed_uri.hostname, port=self.parsed_uri.port, tls_params=tls_params, keepalive=self.keep_alive)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        self.task = self.start_task(task_group, async_exit_stack, self.connection_task())

    async def connection_task(self) -> None:
        delay = RECONNECT_DELAY_MIN
        while True:
            verbose_print('Connecting to MQTT server', flush=True)
            try:
                # Note: Has to be done here so that asyncio.get_event_loop() is correct
                client = self.create_client()
                async with client:
                    try:
                        await self.restore(client)
                        self.client = client
                        self.connected_event.set()
                        self.services.trigger('broker')
                        print(f'Connected to MQTT server {self.uri!r}', flush=True)
                        self.last_error = None
                        delay = RECONNECT_DELAY_MIN

                        async for message in client.messages:
                            for receiver in self.receivers.get(message.topic.value, ()):
                                receiver.handle_message(message)
                    except aiomqtt.MqttError:
                        self.set_disconnected()
                        raise
            except aiomqtt.MqttError as e:
                self.log_error(str(e))
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_DELAY_MAX)

    async def restore(self, client: aiomqtt.Client) -> None:
        """Publish the current values and subscribe to all topics after connecting."""
        for provider in self.value_providers:
            # A pending value is replaced by the current one.
            provider.value_to_send = None
            if provider.current_payload is not None:
                await client.publish(provider.topic, payload=provider.current_payload)
        for topic in self.receivers:
            reason_codes = await client.subscribe(topic)
            for code in reason_codes:
                if not isinstance(code, int) and code.is_failure:
                    print(f'Warning: Subscribing to MQTT topic {topic!r} failed: {code}', flush=True)

    def set_disconnected(self) -> None:
        if self.client is None:
            return
        self.client = None
        self.connected_event.clear()
        self.disconnect_pending = True
        # Events are dropped instead of being sent when they might be outdated.
        for sender in self.event_senders:
            sender.values_to_send.clear()
        self.services.trigger('broker')

    def log_error(self, error: str) -> None:
        if error != self.last_error:
            self.last_error = error
            self.last_error_count = 1
            print(f'Warning: MQTT connection to {self.uri!r} failed: {error}', flush=True)
        elif self.last_error_count == 1:
            self.last_error_count = 2
            print(f'Warning: MQTT connection to {self.uri!r} failed: {error} '
                  '(further identical errors are suppressed)', flush=True)


class ConnectionGetConnected(contracts.ConnectionGetConnected):
    def run(self, *, services: ProcedureServices,
            broker: ConnectionCreate) -> contracts.ConnectionGetConnectedResult:
        if broker.disconnect_pending:
            # Report each disconnect for at least one step, even if the connection was restored since.
            broker.disconnect_pending = False
            services.trigger_self()
            return contracts.ConnectionGetConnectedResult(connected=False)
        return contracts.ConnectionGetConnectedResult(connected=broker.connected)


# TODO: Should this be persistent? If so, should it be cleared by the last will message?


class ValueOptionProviderCreate(contracts.ValueOptionProviderCreate):
    def initialize(self, *, services: StateServices[Literal['state']], broker: ConnectionCreate, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None,
                   template_parts: MQTTTemplateParts) -> contracts.ValueOptionProviderCreateResult:
        self.current_value = None
        # Task is needed for sending out values
        self.task: asyncio.Task | None = None
        self.value_to_send: bytes | None = None
        # The last value set, published again after each connect; None until a value is set.
        self.current_payload: bytes | None = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        self.value_type = type
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = template_parts.prefix
        self.template_suffix = template_parts.suffix

        verbose_print('Create MQTT ValueOptionProvider', flush=True)

        verbose_print(f'  Using topic: {self.topic!r}', flush=True)
        verbose_print(f'  Using type: {self.value_type!r}', flush=True)
        verbose_print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        verbose_print(f'  Using native server object: {self.native_server}', flush=True)
        broker.value_providers.append(self)
        return contracts.ValueOptionProviderCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    async def run_task(self) -> None:
        while True:
            while self.value_to_send is None:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            # While disconnected, the value stays pending; the connection publishes the current value after connecting.
            await self.native_server.connected_event.wait()
            client = self.native_server.client
            value = self.value_to_send
            if client is None or value is None:
                continue
            self.value_to_send = None

            verbose_print(f'Sending {value!r} to {self.topic}', flush=True)
            try:
                await client.publish(self.topic, payload=value)
            except aiomqtt.MqttError as e:
                # The connection publishes the current value again after reconnecting.
                verbose_print(f'Sending {value!r} to {self.topic} failed: {e}', flush=True)
                continue
            verbose_print(f'Sending {value!r} to {self.topic} done', flush=True)


class ValueOptionProviderSetValue(contracts.ValueOptionProviderSetValue):
    def prepare(self, *, services: PreparationServices, state: 'ValueOptionProviderCreate',
                type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'ValueOptionProviderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.ValueOptionProviderSetValueResult:
        # TODO: Add checks whether everything is running?

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if input is None:
            value_bytes = b''
        else:
            value = json.dumps(input.value.json_encode(state.format),
                               ensure_ascii=False, allow_nan=False, sort_keys=True)
            value = state.template_prefix + value + state.template_suffix
            value_bytes = bytes(value, 'utf-8')

        verbose_print(f'Setting output {state.topic} to {value_bytes!r}, old value_to_send is {state.value_to_send!r}', flush=True)

        # Note: This can overwrite a non-None value if sending is too slow. In this case, the overwritten value will be discarded and not sent at all.
        # TODO: Provide some notification if this is happening?
        state.value_to_send = value_bytes
        state.current_payload = value_bytes
        state.event.set()
        return contracts.ValueOptionProviderSetValueResult()


class EventSenderCreate(contracts.EventSenderCreate):
    def initialize(self, *, services: StateServices[Literal['state']], broker: ConnectionCreate, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None,
                   template_parts: MQTTTemplateParts) -> contracts.EventSenderCreateResult:
        self.current_value = None
        # Task is needed for sending out values
        self.task: asyncio.Task | None = None
        self.values_to_send: list[bytes] = []
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        self.value_type = type
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = template_parts.prefix
        self.template_suffix = template_parts.suffix

        verbose_print('Create MQTT EventSender', flush=True)

        verbose_print(f'  Using topic: {self.topic!r}', flush=True)
        verbose_print(f'  Using type: {self.value_type!r}', flush=True)
        verbose_print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        verbose_print(f'  Using native server object: {self.native_server}', flush=True)
        broker.event_senders.append(self)
        return contracts.EventSenderCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    async def run_task(self) -> None:
        while True:
            while len(self.values_to_send) == 0:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            client = self.native_server.client
            if client is None:
                self.values_to_send.clear()
                continue
            value = self.values_to_send.pop(0)

            verbose_print(f'Sending {value!r} to {self.topic}', flush=True)
            try:
                await client.publish(self.topic, payload=value)
            except aiomqtt.MqttError as e:
                # Events are dropped instead of being sent again when they might be outdated.
                verbose_print(f'Sending {value!r} to {self.topic} failed: {e}', flush=True)
                continue
            verbose_print(f'Sending {value!r} to {self.topic} done', flush=True)


class EventSenderSendValue(contracts.EventSenderSendValue):
    def prepare(self, *, services: PreparationServices, state: 'EventSenderCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'EventSenderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.EventSenderSendValueResult:
        # TODO: Add checks whether everything is running?

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if input is None:
            return contracts.EventSenderSendValueResult()
        else:
            value = json.dumps(input.value.json_encode(state.format),
                               ensure_ascii=False, allow_nan=False, sort_keys=True)
            value = state.template_prefix + value + state.template_suffix
            value_bytes = bytes(value, 'utf-8')

        if not state.native_server.connected:
            # Events are dropped instead of being sent after reconnecting when they might be outdated.
            verbose_print(f'Dropping event {state.topic} {value_bytes!r} while disconnected', flush=True)
        else:
            verbose_print(f'Sending event {state.topic} to {value_bytes!r}, old values_to_send is {state.values_to_send}', flush=True)

            # Note: If sending is too slow, there might be multiple events queued up.
            # TODO: Provide a notification in this case?
            state.values_to_send.append(value_bytes)
            state.event.set()

        # This makes sure that the value is sent again if the input does not change
        services.trigger_self()
        return contracts.EventSenderSendValueResult()


class EventReceiverCreate(contracts.EventReceiverCreate):
    def initialize(self, *, services: StateServices[Literal['state']], broker: ConnectionCreate, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None) -> contracts.EventReceiverCreateResult:
        self.received_values: list[DynamicValue] = []

        self.value_type = type
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = ''
        self.template_suffix = ''

        verbose_print('Create MQTT EventReceiver', flush=True)

        verbose_print(f'  Using topic: {self.topic!r}', flush=True)
        verbose_print(f'  Using type: {self.value_type!r}', flush=True)
        verbose_print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        verbose_print(f'  Using native server object: {self.native_server}', flush=True)
        broker.receivers.setdefault(self.topic, []).append(self)
        return contracts.EventReceiverCreateResult(state=self)

    def handle_message(self, message: aiomqtt.Message) -> None:
        try:
            verbose_print(f'Received MQTT message: topic {message.topic.value!r} data {message.payload!r}', flush=True)

            value_bytes = message.payload
            if not isinstance(value_bytes, bytes):
                raise Exception(f'Unexpected payload type {type(value_bytes)}')
            value = str(value_bytes, 'utf-8')

            # TODO: Do JSON parsing here?
            if not value.startswith(self.template_prefix):
                raise Exception(f'Message {value!r} does not start with prefix {self.template_prefix!r}')
            value = value[len(self.template_prefix):]
            if not value.endswith(self.template_suffix):
                raise Exception(f'Message {value!r} does not end with suffix {self.template_suffix!r}')
            if len(self.template_suffix):
                value = value[:-len(self.template_suffix)]

            self.received_values.append(self.value_type.json_decode(json.loads(value), self.format))
            self.services.trigger('state')
        except Exception as e:
            # TODO: Do something else here?
            # TODO: Print stack trace?
            print(f'Warning: Error while parsing received MQTT message topic {message.topic.value!r} data {message.payload!r}: {e}', flush=True)


class EventReceiverGetValue(contracts.EventReceiverGetValue):
    def prepare(self, *, services: PreparationServices, state: 'EventReceiverCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices,
            state: 'EventReceiverCreate', type: ValueType[Any]) -> contracts.EventReceiverGetValueResult:
        # TODO: Add checks whether everything is running?

        if len(state.received_values) == 0:
            return contracts.EventReceiverGetValueResult(output=None)
        value = state.received_values.pop(0)

        res_opt = wirely.Some(value)
        # print(f'get_value: {res_opt}')

        services.trigger_self()

        return contracts.EventReceiverGetValueResult(output=res_opt)


Broker = ConnectionCreate  # Used as a resource type.
ValueOptionProviderState = ValueOptionProviderCreate  # Used as a resource type.
EventSenderState = EventSenderCreate  # Used as a resource type.
EventReceiverState = EventReceiverCreate  # Used as a resource type.
