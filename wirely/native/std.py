from collections.abc import Sequence
from typing import Any, Literal

import wirely
from wirely import DynamicValue, Some, ValueType
from wirely.generated import std as contracts
from wirely.native import check_state_type
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.generated.structs import StdFormatParts
from wirely.types import InternalValue
from wirely.types.time import Duration as Duration  # Implements the opaque struct Std.Duration.





class DebugValuePrinterRun(contracts.DebugValuePrinterRun):
    def prepare(self, *, services: PreparationServices, type: ValueType[Any]) -> None:
        pass

    def run(self, *, services: ProcedureServices, input: DynamicValue,
            type: ValueType[Any]) -> contracts.DebugValuePrinterRunResult:
        print(f'[{services.name}]: Got value {input.to_text()}', flush=True)
        return contracts.DebugValuePrinterRunResult()





class DebugEventPrinterRun(contracts.DebugEventPrinterRun):
    def prepare(self, *, services: PreparationServices, type: ValueType[Any]) -> None:
        pass

    def run(self, *, services: ProcedureServices,
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.DebugEventPrinterRunResult:
        if input is not None:
            print(f'[{services.name}]: Got event {input.value.to_text()}', flush=True)
        return contracts.DebugEventPrinterRunResult()





class WarnOnEventRun(contracts.WarnOnEventRun):
    def prepare(self, *, services: PreparationServices, type: ValueType[Any],
                format_parts: StdFormatParts) -> None:
        self.parts = format_parts

    def run(self, *, services: ProcedureServices,
            input: Some[DynamicValue] | None, type: ValueType[Any],
            format_parts: StdFormatParts) -> contracts.WarnOnEventRunResult:
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


class DebugValueSourceCreate(contracts.DebugValueSourceCreate):
    def initialize(self, *, services: StateServices[Literal['state']],
                   type: ValueType[Any], initial: DynamicValue) -> contracts.DebugValueSourceCreateResult:
        self.value_type = type
        self.current_value = initial
        return contracts.DebugValueSourceCreateResult(state=self)

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


class DebugValueSourceRun(contracts.DebugValueSourceRun):
    def prepare(self, *, services: PreparationServices, state: 'DebugValueSourceCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices,
            state: 'DebugValueSourceCreate', type: ValueType[Any]) -> contracts.DebugValueSourceRunResult:
        return contracts.DebugValueSourceRunResult(output=state.current_value)


class DebugEventSourceCreate(contracts.DebugEventSourceCreate):
    def initialize(self, *, services: StateServices[Literal['state']],
                   type: ValueType[Any]) -> contracts.DebugEventSourceCreateResult:
        self.value_type = type
        # TODO: Allow a queue of values?
        self.next_value: Some[DynamicValue] | None = None
        return contracts.DebugEventSourceCreateResult(state=self)

    def send_event(self, value: InternalValue) -> None:
        """Send an event given as Some(payload), in the representation of Engine.get_value()."""
        if value is not None and type(value) is not wirely.Some:
            raise ValueError(f'Expected None or wirely.Some, got {value!r}')
        self._send(None if value is None else wirely.Some(DynamicValue._make(self.value_type._type, value.value)))

    def _send(self, value: Some[DynamicValue] | None) -> None:
        if self.next_value is not None:
            raise Exception(f'DebugEventSourceCreate already has an event set: {self.next_value!r}, trying to set {value!r}')

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


class DebugEventSourceRun(contracts.DebugEventSourceRun):
    def prepare(self, *, services: PreparationServices, state: 'DebugEventSourceCreate', type: ValueType[Any]) -> None:
        check_state_type(services.name, type, state.value_type)

    def run(self, *, services: ProcedureServices,
            state: 'DebugEventSourceCreate', type: ValueType[Any]) -> contracts.DebugEventSourceRunResult:
        value = state.next_value
        if value is not None:
            state.next_value = None
            services.trigger_self()
        return contracts.DebugEventSourceRunResult(output=value)


DebugValueSourceState = DebugValueSourceCreate  # Used as a resource type.
DebugEventSourceState = DebugEventSourceCreate  # Used as a resource type.
