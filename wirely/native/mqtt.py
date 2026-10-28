import asyncio
import collections
import json
import dataclasses
import contextlib
from typing import Any, Literal

import aiomqtt

import wirely
from wirely import Some
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.generated import mqtt as contracts
from wirely.native.support import check_state_type
from wirely.generated.enums import MQTTFormat
from wirely.runtime.native_api import NativeState, PreparationServices, ProcedureServices, StateServices
from wirely.generated.structs import MQTTTemplateParts
from wirely.misc.repeated_errors import RepeatedError
from wirely.mqtt.settings import check_client_id, check_topic, keep_alive_seconds, parse_uri
from wirely.types.time import Duration
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
# Delay before subscribing again to a topic that the broker rejected, in seconds. A rejection is almost always caused
# by the broker's configuration, so this does not back off.
SUBSCRIBE_RETRY_INTERVAL = 600.0


@dataclasses.dataclass(frozen=True)
class _Message:
    topic: str
    payload: bytes
    retain: bool
    # The provider of a value, None for an event.
    provider: 'ValueOptionProviderCreate | None'


class ConnectionCreate(contracts.ConnectionCreate):
    def initialize(self, *, services: StateServices[Literal['broker']],
                   uri: str, keep_alive: Duration, client_id: Some[str] | None) -> contracts.ConnectionCreateResult:
        verbose_print('Create MQTT connection', flush=True)

        verbose_print(f'  Using URI: {uri!r}', flush=True)

        self.uri = uri
        self.parsed_uri = parse_uri(uri)
        self.keep_alive = keep_alive_seconds(keep_alive)
        self.client_id = None if client_id is None else client_id.value
        if self.client_id is not None:
            check_client_id(self.client_id)

        # Clients register themselves while being initialized.
        self.receivers: dict[str, list[_Receiver]] = {}
        self.value_providers: list[ValueOptionProviderCreate] = []
        self.will_provider: ValueOptionProviderCreate | None = None

        # The client while connected, otherwise None.
        self.client: aiomqtt.Client | None = None
        # Set while connected.
        self.connected_event = asyncio.Event()
        # Set on a disconnect until get_connected() has reported it.
        self.disconnect_pending = False

        # Values and events to send, in order. Each provider has at most one entry.
        self.send_queue: collections.deque[_Message] = collections.deque()
        # Note: Use an event instead of a condition because setting an event does not require await
        self.send_event = asyncio.Event()

        # Whether the broker accepted the subscription of each topic, while connected.
        self.accepted: dict[str, bool] = {}
        # Set while the broker rejects a subscription of the current connection.
        self.subscription_retry = asyncio.Event()
        # Counts the connections, so that a retry can tell whether it still uses the same one.
        self.connection_number = 0

        self.errors = RepeatedError(f'MQTT connection to {uri!r}')
        self.subscription_errors: dict[str, RepeatedError] = {}
        return contracts.ConnectionCreateResult(broker=self)

    @property
    def connected(self) -> bool:
        return self.client is not None

    def create_client(self) -> aiomqtt.Client:
        # Note: Has to be called from the async loop so that asyncio.get_event_loop() is correct

        tls_params = None
        if self.parsed_uri.use_tls:
            tls_params = aiomqtt.TLSParameters()
        will = None
        if self.will_provider is not None:
            will = aiomqtt.Will(self.will_provider.topic, payload=self.will_provider.will_payload,
                                retain=self.will_provider.retain)
        return aiomqtt.Client(protocol=aiomqtt.ProtocolVersion.V5, transport=self.parsed_uri.transport, hostname=self.parsed_uri.hostname, port=self.parsed_uri.port, tls_params=tls_params, keepalive=self.keep_alive, identifier=self.client_id, will=will)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        self.task = self.start_task(task_group, async_exit_stack, self.connection_task())
        self.sender = self.start_task(task_group, async_exit_stack, self.sender_task())
        self.subscription_retrier = self.start_task(task_group, async_exit_stack, self.subscription_retry_task())

    async def connection_task(self) -> None:
        delay = RECONNECT_DELAY_MIN
        while True:
            verbose_print('Connecting to MQTT server', flush=True)
            try:
                # Note: Has to be done here so that asyncio.get_event_loop() is correct
                client = self.create_client()
                # TODO: If this task is cancelled while connecting, aiomqtt's __aenter__ does not clean up: the socket
                # and its background task stay until paho gives up or the process exits. This matters once engines are
                # torn down without the process exiting (transitions, restarting failed native objects).
                async with client:
                    try:
                        await self.restore(client)
                        self.errors.end()
                        print(f'Connected to MQTT server {self.uri!r}', flush=True)
                        delay = RECONNECT_DELAY_MIN

                        # TODO: Consider preserving the order of received messages across topics, by delivering at
                        # most one event per step for the whole connection instead of one per receiver. This keeps
                        # the order a broker such as mosquitto keeps for one publisher, but handling a burst of
                        # messages takes more steps and is slower.
                        async for message in client.messages:
                            for receiver in self.receivers.get(message.topic.value, ()):
                                receiver.handle_message(message)
                    except aiomqtt.MqttError:
                        self.set_disconnected()
                        raise
                    except asyncio.CancelledError:
                        # Cancelling makes the client disconnect cleanly, which suppresses the will, also when the
                        # runtime stops because of an exception. Publish the will value explicitly instead.
                        await self.publish_will(client)
                        raise
            except aiomqtt.MqttError as e:
                self.log_error(e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_DELAY_MAX)

    async def restore(self, client: aiomqtt.Client) -> None:
        """Publish the current values, subscribe to all topics, and start sending after connecting."""
        self.send_queue.clear()
        providers = [provider for provider in self.value_providers if provider is not self.will_provider]
        if self.will_provider is not None:
            # The will value is sent after the other values, so that it marks them as valid.
            providers.append(self.will_provider)
        published: list[tuple[ValueOptionProviderCreate, bytes | None]] = []
        for provider in providers:
            provider.queued = False
            payload = provider.current_payload
            if payload is not None:
                await client.publish(provider.topic, payload=payload, retain=provider.retain)
            published.append((provider, payload))
        accepted = {topic: await self.subscribe(client, topic) for topic in self.receivers}

        # The subscription states change together with Connected.
        self.accepted = accepted
        self.client = client
        self.connection_number += 1
        self.connected_event.set()
        self.services.trigger('broker')
        for receivers in self.receivers.values():
            for receiver in receivers:
                receiver.services.trigger('state')
        if not all(accepted.values()):
            self.subscription_retry.set()
        # Send values set while publishing or subscribing.
        for provider, payload in published:
            if provider.current_payload != payload:
                self.enqueue_value(provider)

    async def subscribe(self, client: aiomqtt.Client, topic: str) -> bool:
        """Subscribe to a topic and log a rejection; return whether the broker accepted it."""
        errors = self.subscription_errors.setdefault(
            topic, RepeatedError(f'Subscribing to MQTT topic {topic!r} on {self.uri!r}'))
        reason_codes = await client.subscribe(topic)
        for code in reason_codes:
            if not isinstance(code, int) and code.is_failure:
                errors.report(str(code))
                return False
        errors.end()
        return True

    async def subscription_retry_task(self) -> None:
        while True:
            await self.subscription_retry.wait()
            number = self.connection_number
            await asyncio.sleep(SUBSCRIBE_RETRY_INTERVAL)
            client = self.client
            if client is None or self.connection_number != number:
                continue
            try:
                for topic, accepted in list(self.accepted.items()):
                    if accepted:
                        continue
                    if await self.subscribe(client, topic) and self.connection_number == number:
                        self.accepted[topic] = True
                        print(f'Subscribed to MQTT topic {topic!r} on {self.uri!r}', flush=True)
                        for receiver in self.receivers[topic]:
                            receiver.services.trigger('state')
            except aiomqtt.MqttError as e:
                # The connection notices the disconnect.
                verbose_print(f'Subscribing again on {self.uri!r} failed: {e}', flush=True)
            if self.connection_number == number and all(self.accepted.values()):
                self.subscription_retry.clear()

    def set_disconnected(self) -> None:
        if self.client is None:
            return
        self.client = None
        self.connected_event.clear()
        self.disconnect_pending = True
        # Report each disconnect for at least one step on all receivers, like Connected, so that Subscribed is never
        # true while Connected reports the disconnect.
        for receivers in self.receivers.values():
            for receiver in receivers:
                receiver.disconnect_pending = True
                receiver.disconnected()
                receiver.services.trigger('state')
        self.accepted = {}
        self.subscription_retry.clear()
        # Events are dropped instead of being sent when they might be outdated. Values are published again
        # after connecting.
        self.send_queue.clear()
        for provider in self.value_providers:
            provider.queued = False
        self.services.trigger('broker')

    def enqueue(self, message: _Message) -> None:
        queue = self.send_queue
        if (self.will_provider is not None and message.provider is not self.will_provider
                and len(queue) > 0 and queue[-1].provider is self.will_provider):
            # Keep the will value after the other values.
            queue.insert(len(queue) - 1, message)
        else:
            queue.append(message)
        self.send_event.set()

    def enqueue_value(self, provider: 'ValueOptionProviderCreate') -> None:
        """Queue the current value of a provider unless it is already queued."""
        if provider.queued or not self.connected or provider.current_payload is None:
            return
        provider.queued = True
        self.enqueue(_Message(provider.topic, provider.current_payload, provider.retain, provider))

    async def sender_task(self) -> None:
        while True:
            while len(self.send_queue) == 0:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.send_event.clear()
                await self.send_event.wait()
            await self.connected_event.wait()
            client = self.client
            if client is None or len(self.send_queue) == 0:
                continue
            message = self.send_queue.popleft()

            verbose_print(f'Sending {message.payload!r} to {message.topic}', flush=True)
            sent = False
            try:
                await client.publish(message.topic, payload=message.payload, retain=message.retain)
                sent = True
                verbose_print(f'Sending {message.payload!r} to {message.topic} done', flush=True)
            except aiomqtt.MqttError as e:
                # Events are dropped instead of being sent again when they might be outdated.
                verbose_print(f'Sending {message.payload!r} to {message.topic} failed: {e}', flush=True)
            provider = message.provider
            if provider is not None:
                provider.queued = False
                # Send a value set while this one was queued.
                if not sent or provider.current_payload != message.payload:
                    self.enqueue_value(provider)

    async def publish_will(self, client: aiomqtt.Client) -> None:
        provider = self.will_provider
        if provider is None:
            return
        try:
            await client.publish(provider.topic, payload=provider.will_payload, retain=provider.retain, timeout=1)
        except aiomqtt.MqttError as e:
            verbose_print(f'Publishing the will to {provider.topic} failed: {e}', flush=True)

    def log_error(self, e: aiomqtt.MqttError) -> None:
        error = str(e)
        cause = e.__cause__
        if cause is not None:
            error += f': {cause}'
        if isinstance(cause, aiomqtt.MqttCodeError) and cause.rc == 0x8E:
            error += ' (another client probably uses the same ClientID)'
        self.errors.report(error)


class ConnectionGetConnected(contracts.ConnectionGetConnected):
    def run(self, *, services: ProcedureServices,
            broker: ConnectionCreate) -> contracts.ConnectionGetConnectedResult:
        if broker.disconnect_pending:
            # Report each disconnect for at least one step, even if the connection was restored since.
            broker.disconnect_pending = False
            services.trigger_self()
            return contracts.ConnectionGetConnectedResult(connected=False)
        return contracts.ConnectionGetConnectedResult(connected=broker.connected)


def _encode(value: Some[DynamicValue] | None, format: str | None, template_prefix: str, template_suffix: str) -> bytes:
    """Encode a value as JSON in its template; None is an empty payload."""
    if value is None:
        return b''
    text = json.dumps(value.value.json_encode(format), ensure_ascii=False, allow_nan=False, sort_keys=True)
    return bytes(template_prefix + text + template_suffix, 'utf-8')


class ValueOptionProviderCreate(contracts.ValueOptionProviderCreate):
    def initialize(self, *, services: StateServices[Literal['state']], broker: ConnectionCreate, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None, template_parts: MQTTTemplateParts,
                   retain: bool, last_will: Some[Some[DynamicValue] | None] | None,
                   ) -> contracts.ValueOptionProviderCreateResult:
        self.current_value = None
        # The last value set, published again after each connect; None until a value is set.
        self.current_payload: bytes | None = None
        # Whether the send queue contains a value of this provider.
        self.queued = False

        self.value_type = type
        check_topic(topic)
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = template_parts.prefix
        self.template_suffix = template_parts.suffix
        self.retain = retain

        verbose_print('Create MQTT ValueOptionProvider', flush=True)

        verbose_print(f'  Using topic: {self.topic!r}', flush=True)
        verbose_print(f'  Using type: {self.value_type!r}', flush=True)
        verbose_print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        verbose_print(f'  Using native server object: {self.native_server}', flush=True)
        broker.value_providers.append(self)

        if last_will is not None:
            # TODO: Reconsider requiring a ClientID. With a random ID, after a half-open connection the broker can
            # publish the will of the old session after the new connection has published its values, which leaves
            # the will value until the next reconnect. Subscribing to the will topic and publishing the current value
            # again when our own will payload arrives within 1.5 * KeepAlive after connecting would avoid that
            # without an ID.
            if broker.client_id is None:
                raise ValueError(f'MQTT LastWill on topic {topic!r} requires a ClientID on its connection')
            if broker.will_provider is not None:
                raise ValueError(f'MQTT connection already has a LastWill on topic {broker.will_provider.topic!r}, '
                                 f'cannot add one on topic {topic!r}')
            broker.will_provider = self
            self.will_payload = self.encode(last_will.value)
        return contracts.ValueOptionProviderCreateResult(state=self)

    def encode(self, value: Some[DynamicValue] | None) -> bytes:
        return _encode(value, self.format, self.template_prefix, self.template_suffix)


class ValueOptionProviderSetValue(contracts.ValueOptionProviderSetValue):
    def prepare(self, *, services: PreparationServices, state: 'ValueOptionProviderCreate',
                type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'ValueOptionProviderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.ValueOptionProviderSetValueResult:
        # TODO: Add checks whether everything is running?

        value_bytes = state.encode(input)

        verbose_print(f'Setting output {state.topic} to {value_bytes!r}, already queued: {state.queued}', flush=True)

        # Note: If sending is too slow, a value set while another value of this provider is queued is only sent when
        # it is still current after the queued value has been sent. Values in between are not sent at all.
        # TODO: Provide some notification if this is happening?
        state.current_payload = value_bytes
        state.native_server.enqueue_value(state)
        return contracts.ValueOptionProviderSetValueResult()


class EventSenderCreate(contracts.EventSenderCreate):
    def initialize(self, *, services: StateServices[Literal['state']], broker: ConnectionCreate, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None,
                   template_parts: MQTTTemplateParts) -> contracts.EventSenderCreateResult:
        self.current_value = None

        self.value_type = type
        check_topic(topic)
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
        return contracts.EventSenderCreateResult(state=self)


class EventSenderSendValue(contracts.EventSenderSendValue):
    def prepare(self, *, services: PreparationServices, state: 'EventSenderCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices, state: 'EventSenderCreate',
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.EventSenderSendValueResult:
        # TODO: Add checks whether everything is running?

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if input is None:
            return contracts.EventSenderSendValueResult()
        value_bytes = _encode(input, state.format, state.template_prefix, state.template_suffix)

        if not state.native_server.connected:
            # Events are dropped instead of being sent after reconnecting when they might be outdated.
            verbose_print(f'Dropping event {state.topic} {value_bytes!r} while disconnected', flush=True)
        else:
            verbose_print(f'Sending event {state.topic} to {value_bytes!r}, {len(state.native_server.send_queue)} messages queued', flush=True)

            # Note: If sending is too slow, there might be multiple events queued up.
            # TODO: Provide a notification in this case?
            state.native_server.enqueue(_Message(state.topic, value_bytes, False, None))

        # This makes sure that the value is sent again if the input does not change
        services.trigger_self()
        return contracts.EventSenderSendValueResult()


class _Receiver(NativeState[Literal['state']]):
    """The state of a node that receives messages on a topic."""

    def setup(self, kind: str, broker: ConnectionCreate, type: ValueType[Any], topic: str,
              format: Some[MQTTFormat] | None, template_parts: MQTTTemplateParts) -> None:
        self.value_type = type
        check_topic(topic)
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = template_parts.prefix
        self.template_suffix = template_parts.suffix

        verbose_print(f'Create MQTT {kind}', flush=True)

        verbose_print(f'  Using topic: {self.topic!r}', flush=True)
        verbose_print(f'  Using type: {self.value_type!r}', flush=True)
        verbose_print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        verbose_print(f'  Using native server object: {self.native_server}', flush=True)
        broker.receivers.setdefault(self.topic, []).append(self)
        # Set on a disconnect until the Subscribed output has reported it.
        self.disconnect_pending = False

    @property
    def subscribed(self) -> bool:
        return self.native_server.connected and self.native_server.accepted.get(self.topic, False)

    def handle_message(self, message: aiomqtt.Message) -> None:
        raise NotImplementedError

    def disconnected(self) -> None:
        """Called when the connection is lost."""

    def decode(self, message: aiomqtt.Message) -> DynamicValue:
        value_bytes = message.payload
        if not isinstance(value_bytes, bytes):
            raise Exception(f'Unexpected payload type {type(value_bytes)}')
        value = str(value_bytes, 'utf-8')

        # TODO: Parse the message as JSON and take the value from the template's position, instead of matching the
        # text around it exactly. A publisher with different whitespace or key order does not match the template.
        if not value.startswith(self.template_prefix):
            raise Exception(f'Message {value!r} does not start with prefix {self.template_prefix!r}')
        value = value[len(self.template_prefix):]
        if not value.endswith(self.template_suffix):
            raise Exception(f'Message {value!r} does not end with suffix {self.template_suffix!r}')
        if len(self.template_suffix):
            value = value[:-len(self.template_suffix)]

        return self.value_type.json_decode(json.loads(value), self.format)

    @staticmethod
    def warn(message: aiomqtt.Message, error: Exception) -> None:
        # TODO: Do something else here?
        # TODO: Print stack trace?
        print(f'Warning: Error while parsing received MQTT message topic {message.topic.value!r} data {message.payload!r}: {error}', flush=True)


class EventReceiverCreate(contracts.EventReceiverCreate, _Receiver):
    def initialize(self, *, services: StateServices[Literal['state']], broker: ConnectionCreate, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None,
                   template_parts: MQTTTemplateParts) -> contracts.EventReceiverCreateResult:
        self.received_values: list[DynamicValue] = []
        self.setup('EventReceiver', broker, type, topic, format, template_parts)
        return contracts.EventReceiverCreateResult(state=self)

    def handle_message(self, message: aiomqtt.Message) -> None:
        if message.retain:
            # The broker sends retained messages when subscribing; they are not events.
            verbose_print(f'Ignoring retained MQTT message: topic {message.topic.value!r} data {message.payload!r}', flush=True)
            return
        verbose_print(f'Received MQTT message: topic {message.topic.value!r} data {message.payload!r}', flush=True)
        try:
            value = self.decode(message)
        except Exception as e:
            self.warn(message, e)
            return
        self.received_values.append(value)
        self.services.trigger('state')


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


class EventReceiverGetSubscribed(contracts.EventReceiverGetSubscribed):
    def run(self, *, services: ProcedureServices,
            state: 'EventReceiverCreate') -> contracts.EventReceiverGetSubscribedResult:
        if state.disconnect_pending:
            # Report each disconnect for at least one step, even if the subscription was restored since.
            state.disconnect_pending = False
            services.trigger_self()
            return contracts.EventReceiverGetSubscribedResult(subscribed=False)
        return contracts.EventReceiverGetSubscribedResult(subscribed=state.subscribed)


class ValueReceiverCreate(contracts.ValueReceiverCreate, _Receiver):
    def initialize(self, *, services: StateServices[Literal['state']], broker: ConnectionCreate, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None,
                   template_parts: MQTTTemplateParts) -> contracts.ValueReceiverCreateResult:
        # The last value received on the current connection, None if there is none.
        self.value: DynamicValue | None = None
        self.setup('ValueReceiver', broker, type, topic, format, template_parts)
        return contracts.ValueReceiverCreateResult(state=self)

    def handle_message(self, message: aiomqtt.Message) -> None:
        verbose_print(f'Received MQTT message: topic {message.topic.value!r} data {message.payload!r}', flush=True)
        if message.payload == b'':
            # The publisher says that there is no value.
            self.value = None
        else:
            try:
                self.value = self.decode(message)
            except Exception as e:
                # Like after a reconnect, which would receive the same message again.
                self.warn(message, e)
                self.value = None
        self.services.trigger('state')

    def disconnected(self) -> None:
        self.value = None


class ValueReceiverGetValue(contracts.ValueReceiverGetValue):
    def prepare(self, *, services: PreparationServices, state: 'ValueReceiverCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices,
            state: 'ValueReceiverCreate', type: ValueType[Any]) -> contracts.ValueReceiverGetValueResult:
        if state.disconnect_pending:
            # Report each disconnect for at least one step, even if the subscription was restored since.
            state.disconnect_pending = False
            services.trigger_self()
            return contracts.ValueReceiverGetValueResult(output=None, subscribed=False)
        subscribed = state.subscribed
        output = wirely.Some(state.value) if subscribed and state.value is not None else None
        return contracts.ValueReceiverGetValueResult(output=output, subscribed=subscribed)


Broker = ConnectionCreate  # Used as a resource type.
ValueOptionProviderState = ValueOptionProviderCreate  # Used as a resource type.
EventSenderState = EventSenderCreate  # Used as a resource type.
EventReceiverState = EventReceiverCreate  # Used as a resource type.
ValueReceiverState = ValueReceiverCreate  # Used as a resource type.
