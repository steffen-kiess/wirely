"""Link, load, and prepare a program without touching the environment."""

from __future__ import annotations

import dataclasses
import datetime
import functools
import types
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, cast

from wirely.program.program import InputSource, Procedure, Program, RunStage, Variable, VariableBank
from wirely.program.program import procedure_instances
from wirely.program.types import InternalValue, TypeResource
from wirely.program.validation import validate_program
from wirely.runtime.contexts import VariableTableInstance
from wirely.runtime.extensions import NativeTypes, RuntimeNativeExtension, resolve_extensions
from wirely.runtime.linking import (
    LinkedProcedure, LinkedState, link_native_contracts, link_native_enums, link_native_functions,
    link_native_resource_types, link_native_signatures, link_native_structs, link_opaque_values,
)
from wirely.runtime.native_api import (
    InterfaceBinding, NativeProcedure, NativeState, PreparationServices, ProcedureAdapter, StateServices,
)
from wirely.runtime.procedures import LoadError, NativeFunction, execute_procedure
from wirely.runtime.resources import ResourceType, ResourceValue

if TYPE_CHECKING:
    from wirely.runtime.engine import Engine


def procedure_owner_label(procedure: Procedure, procedure_indices: Mapping[Procedure, int]) -> str:
    return procedure.node if procedure.node is not None else f'procedure[{procedure_indices[procedure]}]'


def fixed_input(source: InputSource, fixed_values: VariableTableInstance,
                resource_values: Mapping[Variable, ResourceValue]) -> Any:
    """Return the value of a constant, fixed value, or resource input."""
    if source.constant is not None:
        return source.constant.value
    if source.resource is not None:
        return resource_values[source.resource]
    assert source.bank == VariableBank.FIXED_VALUES and isinstance(source.variable, Variable)
    return fixed_values[source.variable]


class _StateHost:
    """Connects the services of loaded native states to the engine that uses them."""

    def __init__(self) -> None:
        self.engine: Engine | None = None

    def attach(self, engine: Engine) -> None:
        if self.engine is not None:
            raise RuntimeError('The loaded program is already used by an engine')
        self.engine = engine

    def trigger(self, initializer: Procedure, output: str) -> None:
        # The first step runs every procedure, so triggers before an engine uses the program have no effect.
        if self.engine is not None:
            self.engine._trigger_output(initializer, output)

    def shutdown_time(self) -> datetime.datetime | None:
        return None if self.engine is None else self.engine.shutdown_time

    def is_shutting_down(self) -> bool:
        return self.engine is not None and self.engine.is_shutting_down


@dataclasses.dataclass(frozen=True, eq=False)
class LoadedProgram:
    """A linked, loaded, and prepared program with its native objects.

    Create it with load(). It owns the native objects created while loading,
    so exactly one engine can use it; load the program again for another one.
    """

    program: Program
    native_types: NativeTypes
    native_functions: Mapping[str, NativeFunction]
    resource_types: Mapping[str, ResourceType]
    procedure_indices: Mapping[Procedure, int]
    fixed_values: VariableTableInstance
    resource_values: Mapping[Variable, ResourceValue]
    # Triggers follow resource object identity, including handles passed through load selectors.
    resource_triggers: Mapping[Variable, list[Procedure]]
    initializer_outputs: Mapping[Procedure, Mapping[str, Variable]]
    native_states: Mapping[Procedure, NativeState]
    native_procedures: Mapping[Procedure, tuple[NativeProcedure, ProcedureAdapter, InterfaceBinding]]
    _host: _StateHost = dataclasses.field(repr=False)

    @classmethod
    def load(cls, program: Program, *, extensions: Iterable[RuntimeNativeExtension] = (),
             override_extensions: Iterable[RuntimeNativeExtension] = (),
             include_builtin_extensions: bool = True,
             services: Mapping[str, object] | None = None) -> LoadedProgram:
        """Verify and link a program, run its load stage, and prepare its runtime procedures.

        services are values for native code by namespaced key, available as StateServices.runtime_services; they
        count as part of the runtime. Raises LoadError if the program does not load on this runtime.
        """
        if not isinstance(program, Program):
            raise TypeError('program parameter is not a Program')
        try:
            return _Loader(program, resolve_extensions(
                extensions, override_extensions=override_extensions,
                include_builtin_extensions=include_builtin_extensions), services).load()
        except LoadError:
            raise
        except Exception as error:
            raise LoadError(str(error)) from error


class _Loader:
    """The tables of a program while it is loading."""

    def __init__(self, program: Program, extension: RuntimeNativeExtension,
                 services: Mapping[str, object] | None = None) -> None:
        self.program = program
        self.runtime_services: Mapping[str, object] = types.MappingProxyType(dict(services or {}))
        # Shared by the native states of this program while loading, see StateServices.program_state.
        self.program_state: dict[str, Any] = {}
        self.extension = extension
        self.native_types = extension.native_types
        self.host = _StateHost()
        self.procedure_indices = {proc: index for index, proc in enumerate(procedure_instances(program))}
        self.resource_types: dict[str, ResourceType] = {}
        self.fixed_values = VariableTableInstance(program.fixed_values)
        self.resource_values: dict[Variable, ResourceValue] = {}
        self.resource_triggers: dict[Variable, list[Procedure]] = {}
        self.resource_origins: dict[Variable, frozenset[Variable]] = {}
        self.initializer_outputs: dict[Procedure, Mapping[str, Variable]] = {}
        self.native_states: dict[Procedure, NativeState] = {}
        self.native_procedures: dict[Procedure, tuple[NativeProcedure, ProcedureAdapter, InterfaceBinding]] = {}

    def load(self) -> LoadedProgram:
        program = self.program
        extension = self.extension
        validate_program(program)

        link_native_enums(program, extension)
        link_native_structs(program, extension)
        link_opaque_values(program, extension)
        link_native_signatures(program, extension)
        self.resource_types = link_native_resource_types(program, extension)
        linked_states, linked_procedures = link_native_contracts(program, extension)
        native_functions = link_native_functions(program, extension)
        self._run_load_schedule(linked_states, linked_procedures, native_functions)
        # Prepare runtime procedures only after the complete load schedule has run.
        self._prepare(linked_procedures)
        return LoadedProgram(
            program=program, native_types=self.native_types, native_functions=native_functions,
            resource_types=self.resource_types, procedure_indices=self.procedure_indices,
            fixed_values=self.fixed_values, resource_values=self.resource_values,
            resource_triggers=self.resource_triggers, initializer_outputs=self.initializer_outputs,
            native_states=self.native_states, native_procedures=self.native_procedures, _host=self.host,
        )

    def _run_load_schedule(self, linked_states: Mapping[str, LinkedState],
                           linked_procedures: Mapping[str, LinkedProcedure],
                           native_functions: Mapping[str, NativeFunction]) -> None:
        for proc in self.program.procs.get(RunStage.LOAD, ()):
            if proc.native is None:
                values = [self._fixed_input(source) for source in proc.inputs]
                output_values = execute_procedure(proc, values, native_functions, native_types=self.native_types,
                                                  procedure_index=self.procedure_indices[proc])
            else:
                if proc.initializer:
                    linked = linked_states[proc.native.name]
                    interface = self._procedure_binding(proc, linked.signature)
                    self.initializer_outputs[proc] = {
                        key: target.variable for key, target in proc.named_outputs()
                        if target.variable.is_resource
                    }
                    services: StateServices[Any] = StateServices(
                        native_types=self.native_types, name=self._owner_label(proc),
                        output_keys=linked.adapter.output_keys,
                        trigger=functools.partial(self.host.trigger, proc),
                        shutdown_time=self.host.shutdown_time,
                        is_shutting_down=self.host.is_shutting_down,
                        runtime_services=self.runtime_services, program_state=self.program_state,
                    )
                    state, outputs = linked.adapter.construct(linked.factory, services, interface)
                    self.native_states[proc] = state
                else:
                    linked_procedure = linked_procedures[proc.native.name]
                    interface = self._procedure_binding(proc, linked_procedure.signature)
                    inputs = {key: self._fixed_input(source) for key, source in self._port_inputs(proc, interface)
                              if source.resource is None}
                    outputs = linked_procedure.adapter.load(
                        linked_procedure.factory(),
                        PreparationServices(native_types=self.native_types, name=self._owner_label(proc)),
                        interface, inputs,
                    )
                output_values = [outputs[key] for key in proc.native.output_names]
            for (index, target), value in zip(enumerate(proc.outputs), output_values):
                if target.variable.is_resource:
                    if not isinstance(value, ResourceValue):
                        raise ValueError(f'Resource output {index} did not return a resource')
                    if target.variable in self.resource_values:
                        raise ValueError('Resource is already initialized')
                    self.resource_values[target.variable] = value
                    origins = set()
                    if not proc.initializer:
                        for source in proc.inputs:
                            if source.resource is not None and self.resource_values[source.resource].object is value.object:
                                origins.update(self.resource_origins[source.resource])
                    self.resource_origins[target.variable] = frozenset(origins or (target.variable,))
                else:
                    self.fixed_values.add(target.variable, value)

    def _prepare(self, linked_procedures: Mapping[str, LinkedProcedure]) -> None:
        for stage, schedule in self.program.procs.items():
            if stage == RunStage.LOAD:
                continue
            for proc in schedule:
                for source in proc.inputs:
                    if source.resource is not None:
                        for origin in self.resource_origins[source.resource]:
                            keys = self.resource_triggers.setdefault(origin, [])
                            if proc not in keys:
                                keys.append(proc)
                if proc.native is not None:
                    linked_procedure = linked_procedures[proc.native.name]
                    interface = self._procedure_binding(proc, linked_procedure.signature)
                    implementation = linked_procedure.factory()
                    linked_procedure.adapter.prepare(
                        implementation, PreparationServices(native_types=self.native_types, name=self._owner_label(proc)), interface)
                    self.native_procedures[proc] = implementation, linked_procedure.adapter, interface

    def _owner_label(self, procedure: Procedure) -> str:
        return procedure_owner_label(procedure, self.procedure_indices)

    def _fixed_input(self, source: InputSource) -> Any:
        return fixed_input(source, self.fixed_values, self.resource_values)

    @staticmethod
    def _port_inputs(proc: Procedure, interface: InterfaceBinding) -> list[tuple[str, InputSource]]:
        """Return the named inputs of a native procedure that are not compile-time reads."""
        return [(name, source) for name, source in proc.named_inputs() if name not in interface.signature.uses]

    def _procedure_binding(self, proc: Procedure, signature: Any) -> InterfaceBinding:
        assert proc.native is not None
        uses = {name: source.constant for name, source in proc.named_inputs() if name in signature.uses}
        ports = [(name, source) for name, source in proc.named_inputs() if name not in uses]
        port_types = {name: source.value_type for name, source in ports if source.resource is None}
        port_types.update((name, target.variable.var_type) for name, target in proc.named_outputs()
                          if not target.variable.is_resource)
        return InterfaceBinding(
            native_types=self.native_types,
            where=f'Native {"initializer" if proc.initializer else "procedure"} `{proc.native.name}` of `{self._owner_label(proc)}`', signature=signature,
            use_types={key: constant.value_type for key, constant in uses.items() if constant is not None},
            use_values={key: cast(InternalValue, constant.value) for key, constant in uses.items() if constant is not None},
            resources={name: self.resource_values[source.resource] for name, source in ports
                       if source.resource is not None},
            port_types=port_types,
            output_resource_types={name: self.resource_types[cast(TypeResource, target.variable.var_type).native_name]
                                   for name, target in proc.named_outputs() if target.variable.is_resource},
            fixed_inputs={name: self._fixed_input(source) for name, source in ports
                          if source.resource is None
                          and source.bank in (VariableBank.FIXED_VALUES, VariableBank.CONST_VALUES)},
        )
