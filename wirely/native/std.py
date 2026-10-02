import dataclasses

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


@native_block_type(native_block_types, has_state=False)
class Constant:
    @native_procedure
    @staticmethod
    def run(*, context):
        par_value = context.parameters['Value']

        context.set_result('output', par_value)


@native_block_type(native_block_types, has_state=False)
class And:
    @native_procedure
    @staticmethod
    def run(*, context):
        res = True

        for val in context.inputs['input']:
            if not isinstance(val, bool):
                raise Exception(f'{val!r} is not a bool')
            if not val:
                res = False

        context.set_result('output', res)


@native_block_type(native_block_types, has_state=False)
class Delay:
    @native_procedure
    @staticmethod
    def copy_from_input(*, context):
        # print('COPY_FROM_INPUT')
        # print(context)

        value = context.inputs['input']
        context.set_result('value', value)

    @native_procedure
    @staticmethod
    def copy_to_output(*, context):
        # print('COPY_TO_OUTPUT')
        # print(context)

        value = context.inputs['value_old']
        context.set_result('output', value)
        # print(value)


@native_block_type(native_block_types, has_state=False)
class TwoStageValueForward:
    @native_procedure
    @staticmethod
    def run1(*, context):
        context.set_result('temp', context.inputs['input'])

    @native_procedure
    @staticmethod
    def run2(*, context):
        context.set_result('output', context.inputs['temp'])


@native_block_type(native_block_types, has_state=False)
class DebugValuePrinter:
    @native_procedure
    @staticmethod
    def run(*, context):
        value = context.inputs['input']

        print(f'[{context.static.name}]: Got value {value}', flush=True)


@native_block_type(native_block_types, has_state=False)
class DebugEventPrinter:
    @native_procedure
    @staticmethod
    def run(*, context):
        value = context.inputs['input']

        print(f'[{context.static.name}]: Got event {value}', flush=True)


@native_block_type(native_block_types, has_state=True)
class DebugValueSource(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        self.current_value = None

        self.out_type = context.parameters['Type']

    @native_procedure
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
            value = self.parameters['Type'].parse(parameters[1])
            self.set_value(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.current_value
            print(f'Current value of {self.context.name} is {value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@native_block_type(native_block_types, has_state=True)
class DebugEventSource(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        # TODO: Allow a queue of values?
        self.next_value = None

        self.out_type = context.parameters['Type']

    @native_procedure
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
            value = blocks.Some(self.parameters['Type'].parse(parameters[1]))
            self.send_event(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.next_value
            print(f'Next value of {self.context.name} is {value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@native_block_type(native_block_types, has_state=False)
class OptionNone:
    @native_procedure
    @staticmethod
    def run(*, context):
        val = None

        context.set_result('output', val)


@native_block_type(native_block_types, has_state=False)
class OptionSome:
    @native_procedure
    @staticmethod
    def run(*, context):
        val = context.inputs['input']
        # Pack value
        val = blocks.Some(val)

        context.set_result('output', val)


@native_block_type(native_block_types, has_state=False)
class OptionUnwrapOrDefault:
    @native_procedure
    @staticmethod
    def run(*, context):
        par_default = context.parameters['Default']

        val = context.inputs['input']

        if val is None:
            res = par_default
        else:
            res = val.value

        context.set_result('output', res)


@native_block_type(native_block_types, has_state=False)
class OptionIsSome:
    @native_procedure
    @staticmethod
    def run(*, context):
        context.set_result('output', context.inputs['input'] is not None)


@native_block_type(native_block_types, has_state=False)
class OptionCreate:
    @native_procedure
    @staticmethod
    def run(*, context):
        if context.inputs['HasValue']:
            result = blocks.Some(context.inputs['Value'])
        else:
            result = None

        context.set_result('output', result)


@dataclasses.dataclass(frozen=True)
class EdgeData:
    edge_type: str

    @staticmethod
    def create(*, parameter_values):
        par_edge_type = parameter_values['EdgeType'].value

        if par_edge_type not in ['Rising', 'Falling', 'Both']:
            raise Exception(f'Invalid edge type: {par_edge_type!r}')

        return EdgeData(edge_type=par_edge_type)


@native_block_type(native_block_types, has_state=False, data_type=EdgeData)
class DetectEdge:
    @native_procedure
    @staticmethod
    def run(*, context):
        last_value = context.inputs['last_value_old']
        data = context.static.block.parsed_native_instance

        value = context.inputs['input']

        # print(f'Last={last_value} Now={value}')
        if data.edge_type == 'Falling':
            do_trigger = (last_value == blocks.Some(True)) and (value == False)
        else:
            raise Exception(f'TODO: Implement edge type {data.edge_type}')

        context.set_result('last_value', blocks.Some(value))
        if do_trigger:
            context.set_result('output', blocks.Some(value))
        else:
            context.set_result('output', None)
