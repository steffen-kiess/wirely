from __future__ import annotations

import enum
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pprint import pformat
from types import MappingProxyType

from .types import Type
from .runtime.procedures import ProcedureBody


class RunStage(enum.Enum):
    """Ordered phases of an engine step."""

    INPUT = enum.auto()  # Read external inputs before normal computation.
    DEFAULT = enum.auto()  # Run normal computation.
    OUTPUT = enum.auto()  # Write external outputs after normal computation.


@dataclass(frozen=True, slots=True, eq=False)
class Variable:
    """A state or temporary variable, identified by object identity."""

    name: str  # Qualified variable name for lookup and diagnostics.
    var_type: Type  # Resolved value type used for validation and comparison.

    def __post_init__(self):
        if not isinstance(self.name, str):
            raise Exception('name parameter is not a str')
        if self.var_type.contains_parameters:
            raise Exception('var_type parameter is not a concrete type')
        if not self.var_type.is_runtime_type:
            raise ValueError(f'Type {self.var_type} is parameter-only')
        object.__setattr__(self, 'name', str(self.name))

    def __repr__(self):
        return f'Variable[{self}]'

    def __str__(self):
        return self.name


@dataclass(frozen=True, slots=True, init=False, eq=False)
class VariableBlock:
    """An immutable collection of variable definitions, without live values."""

    name: str  # Collection name for diagnostics.
    variables: Mapping[str, Variable]  # Variables indexed by qualified name.
    variable_set: frozenset[Variable]  # Variables allowed in a runtime instance.
    offsets: Mapping[Variable, int]  # Byte offsets, ordered by decreasing alignment.
    size: int  # Buffer size in bytes, including final alignment padding.
    alignment: int  # Required alignment of the block's starting byte offset.

    def __init__(self, variables, name):
        """Build a shared compact layout while preserving name lookup order."""
        by_name = {}
        for var in variables:
            if not isinstance(var, Variable):
                raise Exception('var parameter contains object that is not a Variable')
            if var.name in by_name:
                raise Exception(f'Duplicate variable name {var.name!r}')
            by_name[var.name] = var
        offsets = {}
        size = 0
        alignment = 1
        for var in sorted(by_name.values(), key=lambda var: -var.var_type.alignment):
            alignment = max(alignment, var.var_type.alignment)
            offsets[var] = size
            size += var.var_type.size
        size = (size + alignment - 1) & ~(alignment - 1)
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'variables', MappingProxyType(by_name))
        object.__setattr__(self, 'variable_set', frozenset(by_name.values()))
        object.__setattr__(self, 'offsets', MappingProxyType(offsets))
        object.__setattr__(self, 'size', size)
        object.__setattr__(self, 'alignment', alignment)


@dataclass(frozen=True, slots=True)
class InputSourceBinding:
    """A procedure input from a temporary or the step's state snapshot."""

    variable: Variable  # Source in the temporaries or the step's old state.


@dataclass(frozen=True, slots=True)
class ResultBinding:
    """A procedure result targeting a temporary or the next step's state."""

    variable: Variable  # Destination temporary or persistent state variable.
    state_new: Variable | None = None  # Temporary exposing the new state value.


@dataclass(frozen=True, slots=True)
class NativeInstance:
    """Stored node context for procedures and optional native state creation."""

    name: str  # Instance name exposed to native code.
    type_name: tuple[str, ...]  # Qualified node type name, split into components.
    has_native_state: bool  # Whether the engine creates a native state object.
    parameters: Mapping[str, object]  # Resolved parameters in native Python form.
    configuration: object  # Prepared configuration passed to native code.
    initializer_bindings: tuple  # (input name, provider node IDs, is_list) entries.
    initializer_values: Mapping[str, object] = field(default_factory=dict)  # Lifetime static bindings.

    def __post_init__(self):
        object.__setattr__(self, 'initializer_values', MappingProxyType(dict(self.initializer_values)))
        object.__setattr__(self, 'type_name', tuple(self.type_name))
        parameters = MappingProxyType(dict(self.parameters))
        if self.configuration is self.parameters:
            object.__setattr__(self, 'configuration', parameters)
        object.__setattr__(self, 'parameters', parameters)
        object.__setattr__(self, 'initializer_bindings', tuple(
            (name, tuple(node_ids), is_list)
            for name, node_ids, is_list in self.initializer_bindings
        ))


@dataclass(frozen=True, slots=True)
class Procedure:
    """A scheduled procedure with its inputs and result destinations."""

    node_id: str  # Owning node's key in Program.instances.
    reference: tuple[tuple[str, ...], str]  # (qualified type components, method name).
    node_context: NativeInstance  # Parameters and configuration for this node.
    dependencies: tuple  # (input name, InputSourceBinding tuple, is_list) entries.
    resources: tuple  # (input name, provider node IDs, is_list) entries.
    results: Mapping[str, ResultBinding]  # Result destinations indexed by output name.
    body: ProcedureBody | None = None  # Compiled .wrl body; None selects native dispatch.
    values: Mapping[str, object] = field(default_factory=dict)  # Native static bindings.

    def __post_init__(self):
        object.__setattr__(self, 'values', MappingProxyType(dict(self.values)))
        type_name, proc_name = self.reference
        object.__setattr__(self, 'reference', (tuple(type_name), proc_name))
        for field in ('dependencies', 'resources'):
            object.__setattr__(self, field, tuple(
                (name, tuple(bindings), is_list)
                for name, bindings, is_list in getattr(self, field)
            ))
        object.__setattr__(self, 'results', MappingProxyType(dict(self.results)))

    @property
    def key(self):
        """Return an identity distinct from native method aliases."""
        name = self.reference[1]
        return self.node_id, name if self.body is None else ('procedure', name)


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class Program:
    """An executable program with immutable structure and optional debug data.

    Referenced type descriptors, payload objects, and graph are not frozen.
    """

    name: str  # Program name for diagnostics.
    state_variables: VariableBlock  # Variables persisted between engine steps.
    temporaries: VariableBlock  # Per-step variables, including new-state values and flags.
    initial_state: Mapping[Variable, object]  # Starting values of persistent state.
    instances: Mapping[str, NativeInstance]  # Stored node contexts indexed by node ID.
    native_construction_order: tuple[str, ...]  # Node IDs ordered with providers first.
    procs: Mapping[RunStage, tuple[Procedure, ...]]  # Stages and procedures in execution order.
    node_aliases: Mapping[str, str]  # Debug lookup from declared node paths to node IDs.
    singleton_aliases: Mapping[str, str]  # Debug lookup from singleton type names to node IDs.
    variables: Mapping[str, Variable]  # Debug lookup from variable aliases to definitions.
    graph: object | None = None  # Optional compiler graph for debugging and visualization.

    def __post_init__(self):
        for name in ('initial_state', 'instances', 'node_aliases', 'singleton_aliases', 'variables'):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        object.__setattr__(self, 'procs', MappingProxyType({
            stage: tuple(schedule) for stage, schedule in self.procs.items()
        }))
        object.__setattr__(self, 'native_construction_order', tuple(self.native_construction_order))

    def dump(self, file=None):
        """Write the runtime program to a text stream (stdout by default)."""
        if file is None:
            file = sys.stdout

        def line(text='', indent=0):
            print(' ' * indent + text, file=file)

        def value(label, item, indent):
            formatted = pformat(item, width=100 - indent, sort_dicts=True)
            line(label + ':', indent)
            for part in formatted.splitlines():
                line(part, indent + 2)

        def resources(bindings, indent):
            for name, node_ids, is_list in bindings:
                providers = ', '.join(node_ids)
                if is_list:
                    providers = '[' + providers + ']'
                line(f'{name}: {providers}', indent)

        line(f'Program {self.name}')
        for label, variable_block in [('State variables', self.state_variables),
                                      ('Temporaries', self.temporaries)]:
            line()
            line(f'{label} ({variable_block.name}):')
            line(f'size: {variable_block.size}, alignment: {variable_block.alignment}', 2)
            for name, variable in sorted(variable_block.variables.items()):
                line(f'{name}: {variable.var_type}', 2)
                line(f'offset: {variable_block.offsets[variable]}', 4)
                if variable in self.initial_state:
                    value('initial', self.initial_state[variable], 4)

        line()
        line('Native construction order:')
        for index, node_id in enumerate(self.native_construction_order, 1):
            line(f'{index}. {node_id}', 2)

        line()
        line('Node instances:')
        for node_id, instance in sorted(self.instances.items()):
            line(f'{node_id}: {".".join(instance.type_name)}', 2)
            line(f'name: {instance.name}', 4)
            line(f'has native state: {instance.has_native_state}', 4)
            value('parameters', dict(instance.parameters), 4)
            value('initializer values', dict(instance.initializer_values), 4)
            value('configuration', instance.configuration, 4)
            line('initializer resources:', 4)
            resources(instance.initializer_bindings, 6)

        line()
        line('Stages:')
        for stage, schedule in self.procs.items():
            line(f'{stage.name}:', 2)
            for index, proc in enumerate(schedule, 1):
                type_name, method_name = proc.reference
                line(f'{index}. {proc.node_id}: {".".join(type_name)}.{method_name}', 4)
                line(f'node context: {proc.node_context.name}', 6)
                value('values', dict(proc.values), 6)
                line('dependencies:', 6)
                for name, bindings, is_list in proc.dependencies:
                    sources = []
                    for binding in bindings:
                        source = binding.variable.name
                        if binding.variable in self.state_variables.variable_set:
                            source += ' (old state)'
                        sources.append(source)
                    source = ', '.join(sources)
                    if is_list:
                        source = '[' + source + ']'
                    line(f'{name}: {source}', 8)
                line('resources:', 6)
                resources(proc.resources, 8)
                line('results:', 6)
                for name, binding in proc.results.items():
                    line(f'{name}: {binding.variable.name}', 8)
                    if binding.state_new is not None:
                        line(f'new: {binding.state_new.name}', 10)

        for label, aliases in [('Node aliases', self.node_aliases),
                               ('Singleton aliases', self.singleton_aliases),
                               ('Variable aliases', self.variables)]:
            line()
            line(label + ':')
            for name, target in sorted(aliases.items()):
                line(f'{name!r}: {target}', 2)
