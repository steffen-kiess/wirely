import asyncio
import json
import dataclasses
import urllib
import contextlib
from collections.abc import AsyncIterator
from typing import Any, Literal

import wirely
from wirely import DynamicValue, Some, ValueType
from wirely.contracts import mqtt as contracts
from wirely.native import check_state_type
from wirely.runtime.enums import MQTTFormat
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.runtime.structs import MQTTTemplateParts


_JSON_FORMATS = {MQTTFormat.Default: None, MQTTFormat.SnakeCase: 'SnakeCase'}


def _json_format(option: Some[MQTTFormat] | None) -> str | None:
    if option is None:
        return None
    if option.value not in _JSON_FORMATS:
        raise ValueError(f'Unsupported MQTT format {int(option.value)}')
    return _JSON_FORMATS[option.value]


aiomqtt_module = None


def get_aiomqtt() -> Any:
    global aiomqtt_module
    if aiomqtt_module is not None:
        return aiomqtt_module

    import aiomqtt
    aiomqtt_module = aiomqtt

    return aiomqtt_module


class MQTTUriParseException(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class ParsedMQTTUri:
    transport: str
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
        raise Exception('Unknown KNX URI scheme: {!r}'.format(scheme))


class Connection(contracts.ConnectionInitializer):
    def initialize(self, *, services: StateServices[Literal['broker']],
                   uri: str) -> contracts.ConnectionInitializerResult:
        aiomqtt = get_aiomqtt()

        print('Create MQTT connection', flush=True)

        print(f'  Using URI: {uri!r}', flush=True)

        self.parsed_uri = parse_mqtt_uri(uri)

        self.all_subscriptions: dict[str, int] = {}
        self.message_queues: dict[str, list[asyncio.Queue]] = {}
        return contracts.ConnectionInitializerResult(broker=self)

    def create_client(self) -> Any:
        # Note: Has to be called from the async loop so that asyncio.get_event_loop() is correct

        aiomqtt = get_aiomqtt()

        tls_params = None
        if self.parsed_uri.use_tls:
            tls_params = aiomqtt.TLSParameters()
        return aiomqtt.Client(protocol=aiomqtt.ProtocolVersion.V5, transport=self.parsed_uri.transport, hostname=self.parsed_uri.hostname, port=self.parsed_uri.port, tls_params=tls_params)


    # TODO: Do start() later, while everything is already running?
    # TODO: Clean up self.client somewhere (probably using async_exit_stack)?
    async def start_tasks_async(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        print('Connecting to MQTT server', flush=True)
        # Note: Has to be done here so that asyncio.get_event_loop() is correct
        self.client = self.create_client()
        await async_exit_stack.enter_async_context(self.client)
        print('Connecting to MQTT server done', flush=True)

        self.task = self.start_task(task_group, async_exit_stack, self.read_messages_task())

        # TODO: Cleanup

    async def add_subscription(self, topic: str) -> None:
        if topic in self.all_subscriptions:
            self.all_subscriptions[topic] += 1
        else:
            self.all_subscriptions[topic] = 1
            # TODO: Error handling / locking?
            await self.client.subscribe(topic)

    async def remove_subscription(self, topic: str) -> None:
        if self.all_subscriptions[topic] > 1:
            self.all_subscriptions[topic] -= 1
            return
        del self.all_subscriptions[topic]

        return  # TODO: This will hang if this is called after disconnection

        # TODO: Error handling / locking?
        await self.client.unsubscribe(topic)

    @contextlib.asynccontextmanager
    async def subscription(self, topic: str) -> AsyncIterator[None]:
        await self.add_subscription(topic)
        try:
            yield
        finally:
            await self.remove_subscription(topic)

    def add_message_queue(self, topic: str) -> asyncio.Queue:
        if topic not in self.message_queues:
            self.message_queues[topic] = []
        queue: asyncio.Queue = asyncio.Queue()
        self.message_queues[topic].append(queue)
        return queue

    def remove_message_queue(self, topic: str, queue: asyncio.Queue) -> None:
        li = self.message_queues[topic]
        li.remove(queue)

    @staticmethod
    async def get_queue_reader(queue: asyncio.Queue) -> AsyncIterator[Any]:
        while True:
            data = await queue.get()
            yield data
            queue.task_done()

    # TODO: Combine this with subscription()?
    # TODO: Should this be async?
    @contextlib.asynccontextmanager
    async def register_topic_queue(self, topic: str) -> AsyncIterator[AsyncIterator[Any]]:
        queue = self.add_message_queue(topic)
        try:
            yield self.get_queue_reader(queue)
        finally:
            self.remove_message_queue(topic, queue)

    async def read_messages_task(self) -> None:
        async for message in self.client.messages:
            # print('QQQ', message)
            # Note: Accessing self.message_queues should be safe here because this does not call any client code and does not suspend the async task
            # TODO: Performance: Avoid looping over non-wildcard topics?
            for topic in self.message_queues:
                if not message.topic.matches(topic):
                    continue
                for queue in self.message_queues[topic]:
                    queue.put_nowait(message)


# TODO: Should this be persistent? If so, should it be cleared by the last will message?


class ValueOptionProvider(contracts.ValueOptionProviderInitializer):
    def initialize(self, *, services: StateServices[Literal['state']], broker: Connection, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None,
                   template_parts: MQTTTemplateParts) -> contracts.ValueOptionProviderInitializerResult:
        self.current_value = None
        # Task is needed for sending out values
        self.task: asyncio.Task | None = None
        self.value_to_send: bytes | None = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        self.value_type = type
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = template_parts.prefix
        self.template_suffix = template_parts.suffix

        aiomqtt = get_aiomqtt()

        print('Create MQTT ValueOptionProvider', flush=True)

        print(f'  Using topic: {self.topic!r}', flush=True)
        print(f'  Using type: {self.value_type!r}', flush=True)
        print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        print(f'  Using native server object: {self.native_server}', flush=True)
        return contracts.ValueOptionProviderInitializerResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    class SetValue(contracts.ValueOptionProviderSetValue):
        def prepare(self, *, services: PreparationServices, state: 'ValueOptionProvider',
                    type: ValueType[Any]) -> None:
            check_state_type(services.name, type, state.value_type)

        def run(self, *, services: ProcedureServices, state: 'ValueOptionProvider',
                input: Some[DynamicValue] | None) -> contracts.ValueOptionProviderSetValueResult:
            # TODO: Add checks whether everything is running?

            # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
            if input is None:
                value_bytes = b''
            else:
                value = json.dumps(input.value.json_encode(state.format),
                                   ensure_ascii=False, allow_nan=False, sort_keys=True)
                value = state.template_prefix + value + state.template_suffix
                value_bytes = bytes(value, 'utf-8')

            print(f'Setting output {state.topic} to {value_bytes!r}, old value_to_send is {state.value_to_send!r}', flush=True)

            # Note: This can overwrite a non-None value if sending is too slow. In this case, the overwritten value will be discarded and not sent at all.
            # TODO: Provide some notification if this is happening?
            state.value_to_send = value_bytes
            state.event.set()
            return contracts.ValueOptionProviderSetValueResult()

    async def run_task(self) -> None:
        while True:
            while self.value_to_send is None:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.value_to_send
            self.value_to_send = None

            print(f'Sending {value!r} to {self.topic}', flush=True)
            await self.native_server.client.publish(self.topic, payload=value)
            print(f'Sending {value!r} to {self.topic} done', flush=True)


class EventSender(contracts.EventSenderInitializer):
    def initialize(self, *, services: StateServices[Literal['state']], broker: Connection, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None,
                   template_parts: MQTTTemplateParts) -> contracts.EventSenderInitializerResult:
        self.current_value = None
        # Task is needed for sending out values
        self.task: asyncio.Task | None = None
        self.values_to_send: list[object] = []
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        self.value_type = type
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = template_parts.prefix
        self.template_suffix = template_parts.suffix

        aiomqtt = get_aiomqtt()

        print('Create MQTT EventSender', flush=True)

        print(f'  Using topic: {self.topic!r}', flush=True)
        print(f'  Using type: {self.value_type!r}', flush=True)
        print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        print(f'  Using native server object: {self.native_server}', flush=True)
        return contracts.EventSenderInitializerResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    class SendValue(contracts.EventSenderSendValue):
        def prepare(self, *, services: PreparationServices, state: 'EventSender', type: ValueType[Any]) -> None:
            check_state_type(services.name, type, state.value_type)

        def run(self, *, services: ProcedureServices, state: 'EventSender',
                input: Some[DynamicValue] | None) -> contracts.EventSenderSendValueResult:
            # TODO: Add checks whether everything is running?

            # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
            if input is None:
                return contracts.EventSenderSendValueResult()
            else:
                value = json.dumps(input.value.json_encode(state.format),
                                   ensure_ascii=False, allow_nan=False, sort_keys=True)
                value = state.template_prefix + value + state.template_suffix
                value_bytes = bytes(value, 'utf-8')

            print(f'Sending event {state.topic} to {value_bytes!r}, old values_to_send is {state.values_to_send}', flush=True)

            # Note: If sending is too slow, there might be multiple events queued up.
            # TODO: Provide a notification in this case?
            state.values_to_send.append(value_bytes)
            state.event.set()

            # This makes sure that the value is sent again if the input does not change
            services.trigger_self()
            return contracts.EventSenderSendValueResult()

    async def run_task(self) -> None:
        while True:
            while len(self.values_to_send) == 0:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.values_to_send.pop(0)

            print(f'Sending {value} to {self.topic}', flush=True)
            await self.native_server.client.publish(self.topic, payload=value)
            print(f'Sending {value} to {self.topic} done', flush=True)


class EventReceiver(contracts.EventReceiverInitializer):
    def initialize(self, *, services: StateServices[Literal['state']], broker: Connection, type: ValueType[Any],
                   topic: str, format: Some[MQTTFormat] | None) -> contracts.EventReceiverInitializerResult:
        self.task: asyncio.Task | None = None
        self.received_values: list[DynamicValue] = []

        self.value_type = type
        self.topic = topic
        self.format = _json_format(format)
        self.template_prefix = ''
        self.template_suffix = ''

        aiomqtt = get_aiomqtt()

        print('Create MQTT EventReceiver', flush=True)

        print(f'  Using topic: {self.topic!r}', flush=True)
        print(f'  Using type: {self.value_type!r}', flush=True)
        print(f'  Using format: {self.format!r}', flush=True)

        self.native_server = broker
        print(f'  Using native server object: {self.native_server}', flush=True)
        return contracts.EventReceiverInitializerResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    class GetValue(contracts.EventReceiverGetValue):
        def prepare(self, *, services: PreparationServices, state: 'EventReceiver', type: ValueType[Any]) -> None:
            check_state_type(services.name, type, state.value_type)

        def run(self, *, services: ProcedureServices,
                state: 'EventReceiver') -> contracts.EventReceiverGetValueResult:
            # TODO: Add checks whether everything is running?

            if len(state.received_values) == 0:
                return contracts.EventReceiverGetValueResult(output=None)
            value = state.received_values.pop(0)

            res_opt = wirely.Some(value)
            # print(f'get_value: {res_opt}')

            services.trigger_self()

            return contracts.EventReceiverGetValueResult(output=res_opt)

    async def run_task(self) -> None:
        # TODO: Should subscribing be done here or in start_tasks()?
        async with self.native_server.subscription(self.topic):
            async with self.native_server.register_topic_queue(self.topic) as queue:
                async for message in queue:
                    try:
                        print(f'Received MQTT message: topic {message.topic.value!r} data {message.payload!r}', flush=True)

                        if message.topic.value == self.topic:
                            value_bytes = message.payload
                            value = str(value_bytes, 'utf-8')

                            # TODO: Do JSON parsing here?
                            if not value.startswith(self.template_prefix):
                                raise Exception(f'Message {value!r} does not start with prefix {self.template_prefix!r}')
                            value = value[len(self.template_prefix):]
                            if not value.endswith(self.template_suffix):
                                raise Exception(f'Message {value!r} does not end with suffix {self.template_suffix!r}')
                            if len(self.template_suffix):
                                value = value[:-len(self.template_suffix)]

                            value = json.loads(value)
                            self.received_values.append(self.value_type.json_decode(value, self.format))
                            self.services.trigger('state')
                    except Exception as e:
                        # TODO: Do something else here?
                        # TODO: Print stack trace?
                        print(f'Warning: Error while parsing received MQTT message topic {message.topic.value!r} data {message.payload!r}: {e}', flush=True)


Broker = Connection  # Used as a resource type.
ValueOptionProviderState = ValueOptionProvider  # Used as a resource type.
EventSenderState = EventSender  # Used as a resource type.
EventReceiverState = EventReceiver  # Used as a resource type.
