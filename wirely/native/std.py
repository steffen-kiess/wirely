import dataclasses

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


@native_block_type(native_block_types, has_state=False)
class Constant:
    @native_procedure
    @staticmethod
    def run(*, context):
        par_value = context.static.block_parameter_values['Value'].value

        output_var = context.static.targets['']

        context.results[output_var] = par_value


@native_block_type(native_block_types, has_state=False)
class And:
    @native_procedure
    @staticmethod
    def run(*, context):
        input_var = context.static.sources['implicit input']
        output_var = context.static.targets['']

        res = True

        for inp in input_var.members:
            val = context.dep_values[inp]
            if not isinstance(val, bool):
                raise Exception(f'{val!r} is not a bool')
            if not val:
                res = False

        context.results[output_var] = res


@native_block_type(native_block_types, has_state=False)
class Delay:
    @native_procedure
    @staticmethod
    def copy_from_input(*, context):
        # print('COPY_FROM_INPUT')
        # print(context)
        value_var = context.static.state['value']
        # TODO: Name of implicit input?
        input_var = context.static.sources['implicit input']

        value = context.dep_values[input_var]
        context.state_new[value_var] = value

    @native_procedure
    @staticmethod
    def copy_to_output(*, context):
        # print('COPY_TO_OUTPUT')
        # print(context)
        value_var = context.static.state['value']
        # TODO: Name of implicit output?
        output_var = context.static.targets['']

        value = context.state_old[value_var]
        context.results[output_var] = value
        # print(value)


@native_block_type(native_block_types, has_state=False)
class TwoStageValueForward:
    @native_procedure
    @staticmethod
    def run1(*, context):
        var_temp = context.static.targets['temp']
        # TODO: Name of implicit input?
        var_input = context.static.sources['implicit input']

        context.results[var_temp] = context.dep_values[var_input]

    @native_procedure
    @staticmethod
    def run2(*, context):
        var_temp = context.static.sources['temp']
        # TODO: Name of implicit output?
        var_output = context.static.targets['']

        context.results[var_output] = context.dep_values[var_temp]


@native_block_type(native_block_types, has_state=False)
class DebugPrinter:
    @native_procedure
    @staticmethod
    def run(*, context):
        var_input = context.static.sources['implicit input']

        value = context.dep_values[var_input]

        print(f'[{context.static.name}]: Got value {value}', flush=True)


@native_block_type(native_block_types, has_state=False)
class DebugEventPrinter:
    @native_procedure
    @staticmethod
    def run(*, context):
        var_input = context.static.sources['implicit input']

        value = context.dep_values[var_input]

        print(f'[{context.static.name}]: Got event {value}', flush=True)


@native_block_type(native_block_types, has_state=True)
class DebugInput(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)
        self.current_value = None

        # TODO: Clean this up
        proc = None
        for p in self.block.block_procedures:
            if p.name == 'run':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        # TODO
        self.out = self.proc.results[0]

        # TODO: Can this be done in an easier way?
        self.out_type = self.out.var_type.resolve_self(par_values=engine.program.instances[path].parameter_values)

    @native_procedure
    def run(self, *, context):
        var_output = context.static.targets['']

        context.results[var_output] = self.current_value

    def set_value(self, value):
        self.current_value = value

        self.engine.trigger(self.block_path, self.proc)

    def debug_command(self, parameters):
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'set':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.out_type.parse(parameters[1])
            self.set_value(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.current_value
            print(f'Current value of {".".join([block.name for block in self.block_path])} is {value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@native_block_type(native_block_types, has_state=True)
class DebugEventSource(blocks.NativeState):
    def __init__(self, path, engine, static_block_context):
        super().__init__(path, engine, static_block_context)
        # TODO: Allow a queue of values?
        self.next_value = None

        # TODO: Clean this up
        proc = None
        for p in self.block.block_procedures:
            if p.name == 'run':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

        # TODO
        self.out = self.proc.results[0]

        # TODO: Clean up?
        self.out_type = self.out.var_type.resolve_self(engine.program.instances[path].parameter_values)

    @native_procedure
    def run(self, *, context):
        var_output = context.static.targets['']

        context.results[var_output] = self.next_value
        if self.next_value is not None:
            self.next_value = None
            self.engine.trigger(self.block_path, self.proc)

    def send_event(self, value):
        if self.next_value is not None:
            raise Exception(f'DebugEventSource already has an event set: {self.next_value!r}, trying to set {value!r}')

        self.next_value = value

        self.engine.trigger(self.block_path, self.proc)

    def debug_command(self, parameters):
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'send':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = (self.out_type.parse(parameters[1]),)
            self.send_event(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.next_value
            print(f'Next value of {".".join([block.name for block in self.block_path])} is {value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


@native_block_type(native_block_types, has_state=False)
class OptionNone:
    @native_procedure
    @staticmethod
    def run(*, context):
        output_var = context.static.targets['']

        val = None

        context.results[output_var] = val


@native_block_type(native_block_types, has_state=False)
class OptionSome:
    @native_procedure
    @staticmethod
    def run(*, context):
        input_var = context.static.sources['implicit input']
        output_var = context.static.targets['']

        val = context.dep_values[input_var]
        # Pack value
        val = (val,)

        context.results[output_var] = val


@native_block_type(native_block_types, has_state=False)
class OptionUnwrapOrDefault:
    @native_procedure
    @staticmethod
    def run(*, context):
        input_var = context.static.sources['implicit input']
        output_var = context.static.targets['']
        par_default = context.static.block_parameter_values['Default'].value

        val = context.dep_values[input_var]

        if val is None:
            res = par_default
        else:
            res = val[0]

        context.results[output_var] = res


@native_block_type(native_block_types, has_state=False)
class OptionIsSome:
    @native_procedure
    @staticmethod
    def run(*, context):
        input_var = context.static.sources['implicit input']
        output_var = context.static.targets['']

        context.results[output_var] = context.dep_values[input_var] is not None


@native_block_type(native_block_types, has_state=False)
class OptionCreate:
    @native_procedure
    @staticmethod
    def run(*, context):
        has_value_var = context.static.sources['HasValue']
        value_var = context.static.sources['Value']
        output_var = context.static.targets['']

        if context.dep_values[has_value_var]:
            result = (context.dep_values[value_var],)
        else:
            result = None

        context.results[output_var] = result


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
        input_var = context.static.sources['implicit input']
        output_var = context.static.targets['']
        last_value_var = context.static.state['LastValue']
        last_value = context.state_old[last_value_var]
        data = context.static.block.parsed_native_instance

        value = context.dep_values[input_var]

        # print(f'Last={last_value} Now={value}')
        if data.edge_type == 'Falling':
            do_trigger = (last_value == (True,)) and (value == False)
        else:
            raise Exception(f'TODO: Implement edge type {data.edge_type}')

        context.state_new[last_value_var] = (value,)
        if do_trigger:
            context.results[output_var] = (value,)
        else:
            context.results[output_var] = None
