from collections.abc import Sequence

import wirely
from wirely.native import check_state_type, native_node_type, native_procedure, native_resource_type
from wirely.runtime.contexts import NativeNodeContext, NativeProcedure, NativeState, ProcedureContext, ProcedureLoadContext

native_node_types: dict[str, type] = {}
native_resource_types: dict[str, type] = {}


@native_node_type(native_node_types, has_native_state=False)
class DebugValuePrinter:
    @native_procedure('run')
    class Run(NativeProcedure):
        def __call__(self, *, context: ProcedureContext) -> None:
            text = context.values['Type'].to_text(context.inputs['input'])

            print(f'[{context.name}]: Got value {text}', flush=True)


@native_node_type(native_node_types, has_native_state=False)
class DebugEventPrinter:
    @native_procedure('run')
    class Run(NativeProcedure):
        def __call__(self, *, context: ProcedureContext) -> None:
            event = context.inputs['input']

            if event is None:
                return

            text = context.values['Type'].to_text(event.value)
            print(f'[{context.name}]: Got event {text}', flush=True)


@native_node_type(native_node_types, has_native_state=False)
class WarnOnEvent:
    @native_procedure('run')
    class Run(NativeProcedure):
        def __call__(self, *, context: ProcedureContext) -> None:
            event = context.inputs['input']

            if event is None:
                return

            parts = context.values['format_parts'].native
            if parts.has_value:
                message = parts.prefix + context.values['Type'].to_text(event.value) + parts.suffix
            else:
                message = parts.prefix
            print(f'Warning: [{context.name}]: {message}', flush=True)

            # This makes sure that an identical event in the next step is not skipped
            context.trigger_self()


@native_resource_type(native_resource_types, 'DebugValueSourceState')
@native_node_type(native_node_types, has_native_state=True)
class DebugValueSource(NativeState):
    def __init__(self, *, context: NativeNodeContext) -> None:
        super().__init__(context=context)
        context.set_output('state', self)
        self.current_value: object = None

        self.out_type = context.values['Type']

    @native_procedure('run')
    class Run(NativeProcedure):
        def __init__(self, *, context: ProcedureLoadContext) -> None:
            check_state_type(context, context.resources['state'].object)

        def __call__(self, *, context: ProcedureContext) -> None:
            state = context.inputs['state'].object
            context.set_output('output', state.current_value)

    def set_value(self, value: object) -> None:
        self.current_value = value

        self.trigger('state')

    def debug_command(self, parameters: Sequence[str]) -> None:
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


@native_resource_type(native_resource_types, 'DebugEventSourceState')
@native_node_type(native_node_types, has_native_state=True)
class DebugEventSource(NativeState):
    def __init__(self, *, context: NativeNodeContext) -> None:
        super().__init__(context=context)
        context.set_output('state', self)
        # TODO: Allow a queue of values?
        self.next_value: object = None

        self.out_type = context.values['Type']

    @native_procedure('run')
    class Run(NativeProcedure):
        def __init__(self, *, context: ProcedureLoadContext) -> None:
            check_state_type(context, context.resources['state'].object)

        def __call__(self, *, context: ProcedureContext) -> None:
            state = context.inputs['state'].object
            context.set_output('output', state.next_value)
            if state.next_value is not None:
                state.next_value = None
                context.trigger_self()

    def send_event(self, value: object) -> None:
        if self.next_value is not None:
            raise Exception(f'DebugEventSource already has an event set: {self.next_value!r}, trying to set {value!r}')

        self.next_value = value

        self.trigger('state')

    def debug_command(self, parameters: Sequence[str]) -> None:
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'send':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = wirely.Some(self.values['Type'].parse(parameters[1]))
            self.send_event(value)
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            next_value = self.next_value
            print(f'Next value of {self.context.name} is {next_value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')
