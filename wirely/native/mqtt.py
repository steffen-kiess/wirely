import asyncio
import json
import dataclasses
import urllib
import typing
import contextlib

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


aiomqtt_module = None
def get_aiomqtt():
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


def parse_mqtt_uri(uri):
    split = urllib.parse.urlsplit(uri)
    if split.scheme == '':
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Not a URI (scheme is missing)'.format(uri))
    scheme = split.scheme.lower()

    if split.fragment != '':
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty fragment'.format(uri))
    query = urllib.parse.parse_qs(split.query, keep_blank_values=True, errors='strict', strict_parsing=False)

    def check_query(*allowed):
        allowed = set(allowed)
        for key in query:
            if key not in allowed:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got unknown parameter {!r}'.format(uri, key))

    def parse_path(path):
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


@dataclasses.dataclass(frozen=True)
class MqttData:
    value_type: blocks.Type
    topic: str
    format: typing.Optional[str]
    template_prefix: str
    template_suffix: str

    @staticmethod
    def parse_json_str_simple(s):
        in_str = False
        backslash_count = 0
        res = []
        cur_str = ''
        for c in s:
            if c == '\\':
                backslash_count += 1
                cur_str += c
            elif c == '"' and backslash_count % 2 == 0:
                if in_str:
                    cur_str += c
                if cur_str != '':
                    res.append((in_str, cur_str))
                in_str = not in_str
                cur_str = ''
                if in_str:
                    cur_str += c
            else:
                backslash_count = 0
                cur_str += c
        if cur_str != '':
            res.append((in_str, cur_str))
        return res

    @staticmethod
    def parse_template(st):
        prefix = ''
        suffix = ''
        found_ref = False
        for in_str, cur_str in MqttData.parse_json_str_simple(st):
            if in_str:
                if not found_ref:
                    prefix += cur_str
                else:
                    suffix += cur_str
                continue

            pos = 0
            while pos < len(cur_str):
                c = cur_str[pos]
                pos += 1

                if c == '%':
                    if pos == len(cur_str):
                        raise Exception('Found lone % at end of string')
                    c = cur_str[pos]
                    pos += 1

                    if c == '%':
                        pass
                    elif c == 's':
                        if found_ref:
                            raise Exception('Found multiple %s in template string')
                        found_ref = True
                        continue
                    else:
                        raise Exception('Unexpected sequence in template string: ' + repr('%' + c))

                if not found_ref:
                    prefix += c
                else:
                    suffix += c

        if not found_ref:
            raise Exception('Got template string without %s')

        return prefix, suffix

    @staticmethod
    def create(*, parameter_values):
        par_type = parameter_values['Type']
        par_topic = parameter_values['Topic'].value
        if 'Format' in parameter_values:
            # TODO: Verify format here
            par_format = parameter_values['Format'].value
        else:
            par_format = None

        if 'Template' in parameter_values:
            # TODO: Error messages with location?
            par_template = parameter_values['Template'].value
            template_prefix, template_suffix = MqttData.parse_template(par_template)
        else:
            template_prefix = template_suffix = ''

        if not par_type.is_json_serializable:
            raise Exception(f'Unsupported type for MQTT: {ty} (is not serializable as JSON)')
        # TODO: Verify format here

        return MqttData(value_type=par_type, topic=par_topic, format=par_format, template_prefix=template_prefix, template_suffix=template_suffix)

    def dump(self):
        print(f'  Using topic: {self.topic!r}', flush=True)
        print(f'  Using type: {self.value_type!r}', flush=True)
        print(f'  Using format: {self.format!r}', flush=True)


@native_block_type(native_block_types, has_state=True)
class Connection(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        aiomqtt = get_aiomqtt()

        print('Create MQTT connection', flush=True)

        # TODO: Clean up
        par_values = engine.program.instances[path].parameter_values
        par_uri = None
        for par in par_values:
            if par.name.id_str == 'URI':
                par_uri = par
        if par_uri is None:
            raise Exception('par_uri is None')
        uri = par_values[par_uri].value
        print(f'  Using URI: {uri!r}', flush=True)

        self.parsed_uri = parse_mqtt_uri(uri)

        self.all_subscriptions = {}
        self.message_queues = {}

    def create_client(self):
        # Note: Has to be called from the async loop so that asyncio.get_event_loop() is correct

        aiomqtt = get_aiomqtt()

        tls_params = None
        if self.parsed_uri.use_tls:
            tls_params = aiomqtt.TLSParameters()
        return aiomqtt.Client(protocol=aiomqtt.ProtocolVersion.V5, transport=self.parsed_uri.transport, hostname=self.parsed_uri.hostname, port=self.parsed_uri.port, tls_params=tls_params)


    # TODO: Do start() later, while everything is already running?
    # TODO: Clean up self.client somewhere (probably using async_exit_stack)?
    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        await super().start_tasks_async(task_group=task_group, async_exit_stack=async_exit_stack)

        print('Connecting to MQTT server', flush=True)
        # Note: Has to be done here so that asyncio.get_event_loop() is correct
        self.client = self.create_client()
        await async_exit_stack.enter_async_context(self.client)
        print('Connecting to MQTT server done', flush=True)

        self.task = self.start_task(task_group, async_exit_stack, self.read_messages_task())

        # TODO: Cleanup

    async def add_subscription(self, topic):
        if topic in self.all_subscriptions:
            self.all_subscriptions[topic] += 1
        else:
            self.all_subscriptions[topic] = 1
            # TODO: Error handling / locking?
            await self.client.subscribe(topic)

    async def remove_subscription(self, topic):
        if self.all_subscriptions[topic] > 1:
            self.all_subscriptions[topic] -= 1
            return
        del self.all_subscriptions[topic]

        return  # TODO: This will hang if this is called after disconnection

        # TODO: Error handling / locking?
        await self.client.unsubscribe(topic)

    @contextlib.asynccontextmanager
    async def subscription(self, topic):
        await self.add_subscription(topic)
        try:
            yield
        finally:
            await self.remove_subscription(topic)

    def add_message_queue(self, topic):
        if topic not in self.message_queues:
            self.message_queues[topic] = []
        queue = asyncio.Queue()
        self.message_queues[topic].append(queue)
        return queue

    def remove_message_queue(self, topic, queue):
        li = self.message_queues[topic]
        li.remove(queue)

    @staticmethod
    async def get_queue_reader(queue):
        while True:
            data = await queue.get()
            yield data
            queue.task_done()

    # TODO: Combine this with subscription()?
    # TODO: Should this be async?
    @contextlib.asynccontextmanager
    async def register_topic_queue(self, topic):
        queue = self.add_message_queue(topic)
        try:
            yield self.get_queue_reader(queue)
        finally:
            self.remove_message_queue(topic, queue)

    async def read_messages_task(self):
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
@native_block_type(native_block_types, has_state=True, data_type=MqttData)
class ValueOptionProvider(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)
        self.current_value = None
        # Task is needed for sending out values
        self.task = None
        self.value_to_send = None
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        self.data = static_block_context.parsed_native_instance

        aiomqtt = get_aiomqtt()

        print('Create MQTT ValueOptionProvider', flush=True)

        self.data.dump()

        # TODO: Clean up
        server_path, server_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Broker'])
        print(f'  Using server object: {server_path}', flush=True)

        self.native_server = engine.native_states[server_path]
        print(f'  Using native server object: {self.native_server}', flush=True)

        self.json_encode = self.data.value_type.json_encode

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @native_procedure
    def set_value(self, *, context):
        # TODO: Add checks whether everything is running?

        value_opt = context.inputs['input']

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if value_opt is None:
            value_bytes = b''
        else:
            value = value_opt[0]
            value = self.json_encode(value, self.data.format)
            value = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
            value = self.data.template_prefix + value + self.data.template_suffix
            value_bytes = bytes(value, 'utf-8')

        print(f'Setting output {self.data.topic} to {value_bytes}, old value_to_send is {self.value_to_send}', flush=True)

        # Note: This can overwrite a non-None value if sending is too slow. In this case, the overwritten value will be discarded and not sent at all.
        # TODO: Provide some notification if this is happening?
        self.value_to_send = value_bytes
        self.event.set()

    async def run_task(self):
        while True:
            while self.value_to_send is None:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.value_to_send
            self.value_to_send = None

            print(f'Sending {value} to {self.data.topic}', flush=True)
            await self.native_server.client.publish(self.data.topic, payload=value)
            print(f'Sending {value} to {self.data.topic} done', flush=True)


@native_block_type(native_block_types, has_state=True, data_type=MqttData)
class EventEmitter(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)
        self.current_value = None
        # Task is needed for sending out values
        self.task = None
        self.values_to_send = []
        # Note: Use an event instead of a condition because setting an event does not require await
        # self.condition_var = asyncio.Condition()
        self.event = asyncio.Event()

        self.data = static_block_context.parsed_native_instance

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'send_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        aiomqtt = get_aiomqtt()

        print('Create MQTT EventEmitter', flush=True)

        self.data.dump()

        # TODO: Clean up
        server_path, server_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Broker'])
        print(f'  Using server object: {server_path}', flush=True)

        self.native_server = engine.native_states[server_path]
        print(f'  Using native server object: {self.native_server}', flush=True)

        self.json_encode = self.data.value_type.json_encode

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @native_procedure
    def send_value(self, *, context):
        # TODO: Add checks whether everything is running?

        data = context.static.block.parsed_native_instance

        value_opt = context.inputs['input']

        # TODO: Is this correct or should this distinguish between a value which has not yet been sent/received and an empty string?
        if value_opt is None:
            return
        else:
            value = value_opt[0]
            value = self.json_encode(value, data.format)
            value = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
            value = data.template_prefix + value + data.template_suffix
            value_bytes = bytes(value, 'utf-8')

        print(f'Sending event {data.topic} to {value_bytes}, old values_to_send is {self.values_to_send}', flush=True)

        # Note: If sending is too slow, there might be multiple events queued up.
        # TODO: Provide a notification in this case?
        self.values_to_send.append(value_bytes)
        self.event.set()

        # This makes sure that the value is sent again if the input does not change
        self.engine.trigger(self.block_path, self.proc)

    async def run_task(self):
        while True:
            while len(self.values_to_send) == 0:
                # Note: This relies on the fact that everything is running on one thread and therefore everything without await is atomic with regard to other tasks
                self.event.clear()
                await self.event.wait()
            value = self.values_to_send.pop(0)

            print(f'Sending {value} to {self.data.topic}', flush=True)
            await self.native_server.client.publish(self.data.topic, payload=value)
            print(f'Sending {value} to {self.data.topic} done', flush=True)


@native_block_type(native_block_types, has_state=True, data_type=MqttData)
class EventReceiver(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)

        self.task = None
        self.received_values = []

        self.data = static_block_context.parsed_native_instance

        aiomqtt = get_aiomqtt()

        print('Create MQTT EventReceiver', flush=True)

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'get_value':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        self.data.dump()

        # TODO: Clean up
        server_path, server_var = blocks.resolve_variable(path, path[-1].block_inputs_byname['Broker'])
        print(f'  Using server object: {server_path}', flush=True)

        self.native_server = engine.native_states[server_path]
        print(f'  Using native server object: {self.native_server}', flush=True)

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        if self.task is not None:
            raise Exception('self.task is not None')

        self.task = self.start_task(task_group, async_exit_stack, self.run_task())

    @native_procedure
    def get_value(self, *, context):
        # TODO: Add checks whether everything is running?

        if len(self.received_values) == 0:
            context.set_result('output', None)
            return
        value = self.received_values.pop(0)

        res_opt = (value,)
        # print(f'get_value: {res_opt}')

        context.set_result('output', res_opt)

        self.engine.trigger(self.block_path, self.proc)

    async def run_task(self):
        # TODO: Should subscribing be done here or in start_tasks()?
        async with self.native_server.subscription(self.data.topic):
            async with self.native_server.register_topic_queue(self.data.topic) as queue:
                async for message in queue:
                    try:
                        print(f'Received MQTT message: topic {message.topic.value!r} data {message.payload!r}', flush=True)

                        if message.topic.value == self.data.topic:
                            value_bytes = message.payload
                            value = str(value_bytes, 'utf-8')

                            # TODO: Do JSON parsing here?
                            if not value.startswith(self.data.template_prefix):
                                raise Exception(f'Message {value!r} does not start with prefix {self.data.template_prefix!r}')
                            value = value[len(self.data.template_prefix):]
                            if not value.endswith(self.data.template_suffix):
                                raise Exception(f'Message {value!r} does not end with suffix {self.data.template_suffix!r}')
                            if len(self.data.template_suffix):
                                value = value[:-len(self.data.template_suffix)]

                            value = json.loads(value)
                            value = self.data.value_type.json_decode(value, self.data.format)

                            self.received_values.append(value)
                            self.engine.trigger(self.block_path, self.proc)
                    except Exception as e:
                        # TODO: Do something else here?
                        # TODO: Print stack trace?
                        print(f'Warning: Error while parsing received MQTT message topic {message.topic.value!r} data {message.payload!r}: {e}', flush=True)
