from collections.abc import Sequence
from typing import Any, Literal

import wirely
from wirely import DynamicValue, Some, ValueType
from wirely.generated import std as contracts
from wirely.native import check_state_type
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.generated.structs import StdFormatParts
from wirely.types import InternalValue


class DebugValuePrinter:

    class Run(contracts.DebugValuePrinterRun):
        def prepare(self, *, services: PreparationServices, type: ValueType[Any]) -> None:
            pass

        def run(self, *, services: ProcedureServices, input: DynamicValue) -> contracts.DebugValuePrinterRunResult:
            print(f'[{services.name}]: Got value {input.to_text()}', flush=True)
            return contracts.DebugValuePrinterRunResult()


class DebugEventPrinter:

    class Run(contracts.DebugEventPrinterRun):
        def prepare(self, *, services: PreparationServices, type: ValueType[Any]) -> None:
            pass

        def run(self, *, services: ProcedureServices,
                input: Some[DynamicValue] | None) -> contracts.DebugEventPrinterRunResult:
            if input is not None:
                print(f'[{services.name}]: Got event {input.value.to_text()}', flush=True)
            return contracts.DebugEventPrinterRunResult()


class WarnOnEvent:

    class Run(contracts.WarnOnEventRun):
        def prepare(self, *, services: PreparationServices, type: ValueType[Any],
                    format_parts: StdFormatParts) -> None:
            self.parts = format_parts

        def run(self, *, services: ProcedureServices,
                input: Some[DynamicValue] | None) -> contracts.WarnOnEventRunResult:
            if input is None:
                return contracts.WarnOnEventRunResult()

            parts = self.parts
            if parts.has_value:
                message = parts.prefix + input.value.to_text() + parts.suffix
            else:
                message = parts.prefix
            print(f'Warning: [{services.name}]: {message}', flush=True)

            # This makes sure that an identical event in the next step is not skipped
            services.trigger_self()
            return contracts.WarnOnEventRunResult()


class DebugValueSource(contracts.DebugValueSourceInitializer):
    def initialize(self, *, services: StateServices[Literal['state']],
                   type: ValueType[Any]) -> contracts.DebugValueSourceInitializerResult:
        self.value_type = type
        self.current_value: DynamicValue | None = None  # None until a value is set.
        return contracts.DebugValueSourceInitializerResult(state=self)

    class Run(contracts.DebugValueSourceRun):
        def prepare(self, *, services: PreparationServices, state: 'DebugValueSource', type: ValueType[Any]) -> None:
            check_state_type(services.name, type, state.value_type)

        def run(self, *, services: ProcedureServices,
                state: 'DebugValueSource') -> contracts.DebugValueSourceRunResult:
            value = state.current_value
            if value is None:
                # Option types start without a value; others need one to be set.
                value = DynamicValue._make(state.value_type._type, None)
            return contracts.DebugValueSourceRunResult(output=value)

    def set_value(self, value: InternalValue) -> None:
        """Set the value, in the representation of Engine.get_value()."""
        self._set(DynamicValue._make(self.value_type._type, value))

    def _set(self, value: DynamicValue) -> None:
        self.current_value = value
        self.services.trigger('state')

    def debug_command(self, parameters: Sequence[str]) -> None:
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'set':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            self._set(self.value_type.parse(parameters[1]))
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            value = self.current_value
            print(f'Current value of {self.services.name} is {value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


class DebugEventSource(contracts.DebugEventSourceInitializer):
    def initialize(self, *, services: StateServices[Literal['state']],
                   type: ValueType[Any]) -> contracts.DebugEventSourceInitializerResult:
        self.value_type = type
        # TODO: Allow a queue of values?
        self.next_value: Some[DynamicValue] | None = None
        return contracts.DebugEventSourceInitializerResult(state=self)

    class Run(contracts.DebugEventSourceRun):
        def prepare(self, *, services: PreparationServices, state: 'DebugEventSource', type: ValueType[Any]) -> None:
            check_state_type(services.name, type, state.value_type)

        def run(self, *, services: ProcedureServices,
                state: 'DebugEventSource') -> contracts.DebugEventSourceRunResult:
            value = state.next_value
            if value is not None:
                state.next_value = None
                services.trigger_self()
            return contracts.DebugEventSourceRunResult(output=value)

    def send_event(self, value: InternalValue) -> None:
        """Send an event given as Some(payload), in the representation of Engine.get_value()."""
        if value is not None and type(value) is not wirely.Some:
            raise ValueError(f'Expected None or wirely.Some, got {value!r}')
        self._send(None if value is None else wirely.Some(DynamicValue._make(self.value_type._type, value.value)))

    def _send(self, value: Some[DynamicValue] | None) -> None:
        if self.next_value is not None:
            raise Exception(f'DebugEventSource already has an event set: {self.next_value!r}, trying to set {value!r}')

        self.next_value = value

        self.services.trigger('state')

    def debug_command(self, parameters: Sequence[str]) -> None:
        if len(parameters) == 0:
            raise Exception('Missing command')
        cmd = parameters[0]

        if cmd == 'send':
            if len(parameters) != 2:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            self._send(wirely.Some(self.value_type.parse(parameters[1])))
        elif cmd == 'get':
            if len(parameters) != 1:
                raise Exception(f'Invalid number of parameter for command {cmd}')
            next_value = self.next_value
            print(f'Next value of {self.services.name} is {next_value!r}', flush=True)
        else:
            raise Exception(f'Unknown command: {parameters!r}')


DebugValueSourceState = DebugValueSource  # Used as a resource type.
DebugEventSourceState = DebugEventSource  # Used as a resource type.
