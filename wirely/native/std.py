import wirely as blocks
from wirely.native import native_block_type
from wirely.runtime.contexts import NativeState

native_block_types = {}


@native_block_type(native_block_types, has_native_state=False)
class Constant:
    @staticmethod
    def run(*, context):
        par_value = context.values['Value']

        context.set_result('output', par_value)


@native_block_type(native_block_types, has_native_state=False)
class And:
    @staticmethod
    def run(*, context):
        res = True

        for val in context.inputs['input']:
            if not isinstance(val, bool):
                raise Exception(f'{val!r} is not a bool')
            if not val:
                res = False

        context.set_result('output', res)


@native_block_type(native_block_types, has_native_state=False)
class DelayOneStep:
    @staticmethod
    def copy_from_input(*, context):
        # print('COPY_FROM_INPUT')
        # print(context)

        value = context.inputs['input']
        context.set_result('value', value)

    @staticmethod
    def copy_to_output(*, context):
        # print('COPY_TO_OUTPUT')
        # print(context)

        value = context.inputs['value_old']
        context.set_result('output', value)
        # print(value)


@native_block_type(native_block_types, has_native_state=False)
class DebugValuePrinter:
    @staticmethod
    def run(*, context):
        value = context.inputs['input']

        print(f'[{context.name}]: Got value {value}', flush=True)


@native_block_type(native_block_types, has_native_state=False)
class DebugEventPrinter:
    @staticmethod
    def run(*, context):
        value = context.inputs['input']

        print(f'[{context.name}]: Got event {value}', flush=True)


@native_block_type(native_block_types, has_native_state=True)
class DebugValueSource(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        self.current_value = None

        self.out_type = context.values['Type']

    def run(self, *, context):
        context.set_result('output', self.current_value)

    def set_value(self, value):
        self.current_value = value

        self.trigger('run')

    def debug_command(self, parameters):
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'set':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.values['Type'].parse(parameters[1])
            self.set_value(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.current_value
            print(f'Current value of {self.context.name} is {value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@native_block_type(native_block_types, has_native_state=True)
class DebugEventSource(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        # TODO: Allow a queue of values?
        self.next_value = None

        self.out_type = context.values['Type']

    def run(self, *, context):
        context.set_result('output', self.next_value)
        if self.next_value is not None:
            self.next_value = None
            self.trigger('run')

    def send_event(self, value):
        if self.next_value is not None:
            raise Exception(f'DebugEventSource already has an event set: {self.next_value!r}, trying to set {value!r}')

        self.next_value = value

        self.trigger('run')

    def debug_command(self, parameters):
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'send':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = blocks.Some(self.values['Type'].parse(parameters[1]))
            self.send_event(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.next_value
            print(f'Next value of {self.context.name} is {value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@native_block_type(native_block_types, has_native_state=False)
class OptionNone:
    @staticmethod
    def run(*, context):
        val = None

        context.set_result('output', val)


@native_block_type(native_block_types, has_native_state=False)
class OptionSome:
    @staticmethod
    def run(*, context):
        val = context.inputs['input']
        # Pack value
        val = blocks.Some(val)

        context.set_result('output', val)


@native_block_type(native_block_types, has_native_state=False)
class OptionUnwrapOr:
    @staticmethod
    def run(*, context):
        par_default = context.values['Default']

        val = context.inputs['input']

        if val is None:
            res = par_default
        else:
            res = val.value

        context.set_result('output', res)


@native_block_type(native_block_types, has_native_state=False)
class OptionIsSome:
    @staticmethod
    def run(*, context):
        context.set_result('output', context.inputs['input'] is not None)


@native_block_type(native_block_types, has_native_state=False)
class OptionCreate:
    @staticmethod
    def run(*, context):
        if context.inputs['HasValue']:
            result = blocks.Some(context.inputs['Value'])
        else:
            result = None

        context.set_result('output', result)


@native_block_type(native_block_types, has_native_state=False)
class DetectEdge:
    @staticmethod
    def run(*, context):
        last_value = context.inputs['last_value_old']
        edge_type = context.values['EdgeType'].name

        value = context.inputs['input']

        # print(f'Last={last_value} Now={value}')
        if edge_type == 'Falling':
            do_trigger = (last_value == blocks.Some(True)) and (value == False)
        else:
            raise Exception(f'TODO: Implement edge type {edge_type}')

        context.set_result('last_value', blocks.Some(value))
        if do_trigger:
            context.set_result('output', blocks.Some(value))
        else:
            context.set_result('output', None)
