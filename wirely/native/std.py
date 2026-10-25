from typing import Any, Literal

import wirely
from wirely import Some
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.generated import std as contracts
from wirely.native.support import check_state_type
from wirely.runtime.native_api import (
    InvalidCommandArgument, CommandFailed, PreparationServices, ProcedureServices, StateServices,
    control_command,
)
from wirely.generated.structs import StdFormatParts
from wirely.runtime.extensions import NativeTypes
from wirely.runtime.value_types import check_opaque_values
from wirely.program.types import InternalValue
from wirely.types.time import Duration as Duration  # Implements the opaque struct Std.Duration.





class DebugValuePrinterRun(contracts.DebugValuePrinterRun):
    def prepare(self, *, services: PreparationServices, type: ValueType[Any]) -> None:
        pass

    def run(self, *, services: ProcedureServices, input: DynamicValue,
            type: ValueType[Any]) -> contracts.DebugValuePrinterRunResult:
        print(f'[{services.name}]: Got value {input.to_text(native_types=services.native_types)}', flush=True)
        return contracts.DebugValuePrinterRunResult()





class DebugEventPrinterRun(contracts.DebugEventPrinterRun):
    def prepare(self, *, services: PreparationServices, type: ValueType[Any]) -> None:
        pass

    def run(self, *, services: ProcedureServices,
            input: Some[DynamicValue] | None, type: ValueType[Any]) -> contracts.DebugEventPrinterRunResult:
        if input is not None:
            print(f'[{services.name}]: Got event {input.value.to_text(native_types=services.native_types)}', flush=True)
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
            message = parts.prefix + input.value.to_text(native_types=services.native_types) + parts.suffix
        else:
            message = parts.prefix
        print(f'Warning: [{services.name}]: {message}', flush=True)

        # This makes sure that an identical event in the next step is not skipped
        services.trigger_self()
        return contracts.WarnOnEventRunResult()


# Control commands of the debug sources take and return maps. Key 1 holds a value in the runtime
# binary representation, with exactly the data size of the type.


def _encode_value(value: DynamicValue) -> bytes:
    return value._type.serialize(value._value)


def _decode_value(value_type: ValueType[Any], argument: object, native_types: NativeTypes) -> DynamicValue:
    data = _argument(argument).get(1)
    data_size = value_type._type.data_size
    if not isinstance(data, bytes) or len(data) != data_size:
        raise InvalidCommandArgument(f'Expected a byte string of {data_size} bytes in key 1')
    try:
        value = value_type._type.deserialize(data)
        check_opaque_values(value_type._type, value, native_types=native_types)
        return DynamicValue._make(value_type._type, value)
    except ValueError as error:
        raise InvalidCommandArgument(str(error)) from None


def _argument(argument: object) -> dict[Any, Any]:
    """Return the argument map; an omitted argument is an empty map."""
    if argument is None:
        return {}
    if not isinstance(argument, dict):
        raise InvalidCommandArgument('Expected a map')
    return argument


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

    @control_command(0)
    def get_command(self, argument: object) -> dict[int, bytes]:
        _argument(argument)
        return {1: _encode_value(self.current_value)}

    @control_command(1)
    def set_command(self, argument: object) -> dict[int, Any]:
        self._set(_decode_value(self.value_type, argument, self.services.native_types))
        return {}


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

    @control_command(0)
    def get_command(self, argument: object) -> dict[int, bytes]:
        """Return the payload of the queued event, if any."""
        _argument(argument)
        return {} if self.next_value is None else {1: _encode_value(self.next_value.value)}

    @control_command(1)
    def send_command(self, argument: object) -> dict[int, Any]:
        payload = _decode_value(self.value_type, argument, self.services.native_types)
        if self.next_value is not None:
            # Reason 1: an event is pending.
            raise CommandFailed('An event is already queued', reason=1)
        self._send(wirely.Some(payload))
        return {}


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
