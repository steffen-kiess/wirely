from __future__ import annotations

import enum
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pprint import pformat
from types import MappingProxyType

from ..types import Type
from .procedures import Constant, Function, ProcedureBody


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
        if not self.var_type.is_runtime_type:
            raise ValueError(f'Type {self.var_type} is not a runtime type: {self.var_type.runtime_type_error()}')
        object.__setattr__(self, 'name', str(self.name))

    def __repr__(self):
        return f'Variable[{self}]'

    def __str__(self):
        return self.name


@dataclass(frozen=True, slots=True, init=False, eq=False)
class VariableTable:
    """An immutable collection of variable definitions, without live values."""

    name: str  # Collection name for diagnostics.
    variables: Mapping[str, Variable]  # Variables indexed by qualified name.
    variable_set: frozenset[Variable]  # Variables allowed in a runtime instance.
    offsets: Mapping[Variable, int]  # Byte offsets, ordered by decreasing alignment.
    size: int  # Buffer size in bytes, including final alignment padding.
    alignment: int  # Required alignment of the table's starting byte offset.

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
class InputSource:
    """A procedure input from a temporary, one of the step's state tables, or a constant."""

    variable: Variable | None = None  # Source in the temporaries or the state variables.
    new_state: bool | None = None  # None for temporaries; else whether it reads this step's new state.
    constant: Constant | None = None  # Entry of Program.constants, instead of a variable.

    def __post_init__(self):
        if (self.variable is None) == (self.constant is None):
            raise ValueError('Expected either a variable or a constant')
        if self.constant is not None and self.new_state is not None:
            raise ValueError('Constant sources have no state phase')

    @property
    def value_type(self):
        return self.variable.var_type if self.constant is None else self.constant.value_type


@dataclass(frozen=True, slots=True)
class OutputTarget:
    """A procedure output targeting a temporary or the next step's state."""

    variable: Variable  # Destination temporary or persistent state variable.


@dataclass(frozen=True, slots=True)
class NativeInstance:
    """Stored node context for procedures and optional native state creation."""

    name: str  # Instance name exposed to native code.
    type_name: tuple[str, ...]  # Qualified node type name, split into components.
    has_native_state: bool  # Whether the engine creates a native state object.
    parameters: Mapping[str, object]  # Resolved parameters in native Python form.
    initializer_bindings: tuple  # (input name, provider node IDs, is_list) entries.
    initializer_values: Mapping[str, object] = field(default_factory=dict)  # Lifetime static bindings.

    def __post_init__(self):
        object.__setattr__(self, 'initializer_values', MappingProxyType(dict(self.initializer_values)))
        object.__setattr__(self, 'type_name', tuple(self.type_name))
        parameters = MappingProxyType(dict(self.parameters))
        object.__setattr__(self, 'parameters', parameters)
        object.__setattr__(self, 'initializer_bindings', tuple(
            (name, tuple(node_ids), is_list)
            for name, node_ids, is_list in self.initializer_bindings
        ))


@dataclass(frozen=True, slots=True)
class Procedure:
    """A scheduled procedure with its inputs and output destinations."""

    node_id: str  # Owning node's key in Program.instances.
    reference: tuple[tuple[str, ...], str]  # (qualified type components, method name).
    node_context: NativeInstance  # Stored context for this node.
    dependencies: tuple  # (input name, InputSource tuple, is_list) entries.
    resources: tuple  # (input name, provider node IDs, is_list) entries.
    outputs: Mapping[str, OutputTarget]  # Output destinations indexed by output name.
    body: ProcedureBody | None = None  # Compiled .wrl body; None selects native dispatch.
    values: Mapping[str, object] = field(default_factory=dict)  # Native static bindings.

    def __post_init__(self):
        object.__setattr__(self, 'values', MappingProxyType(dict(self.values)))
        type_name, proc_name = self.reference
        object.__setattr__(self, 'reference', (tuple(type_name), proc_name))
        for field in ('dependencies', 'resources'):
            object.__setattr__(self, field, tuple(
                (name, tuple(entries), is_list)
                for name, entries, is_list in getattr(self, field)
            ))
        object.__setattr__(self, 'outputs', MappingProxyType(dict(self.outputs)))

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
    state_variables: VariableTable  # Variables persisted between engine steps.
    temporaries: VariableTable  # Per-step variables.
    initial_state: Mapping[Variable, object]  # Starting values of persistent state.
    instances: Mapping[str, NativeInstance]  # Stored node contexts indexed by node ID.
    native_construction_order: tuple[str, ...]  # Node IDs ordered with providers first.
    procs: Mapping[RunStage, tuple[Procedure, ...]]  # Stages and procedures in execution order.
    node_aliases: Mapping[str, str]  # Debug lookup from declared node paths to node IDs.
    singleton_aliases: Mapping[str, str]  # Debug lookup from singleton type names to node IDs.
    variables: Mapping[str, InputSource]  # Debug lookup from variable aliases to sources.
    constants: tuple[Constant, ...] = ()  # Typed values shared by bytecode procedures.
    functions: tuple[Function, ...] = ()  # Native functions called by bytecode procedures.
    graph: object | None = None  # Optional compiler graph for debugging and visualization.

    def __post_init__(self):
        for name in ('initial_state', 'instances', 'node_aliases', 'singleton_aliases', 'variables'):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        object.__setattr__(self, 'procs', MappingProxyType({
            stage: tuple(schedule) for stage, schedule in self.procs.items()
        }))
        object.__setattr__(self, 'native_construction_order', tuple(self.native_construction_order))
        object.__setattr__(self, 'constants', tuple(self.constants))
        object.__setattr__(self, 'functions', tuple(self.functions))

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

        constant_indices = {id(constant): index for index, constant in enumerate(self.constants)}

        def source(input_source):
            if input_source.constant is not None:
                return f'const.{constant_indices[id(input_source.constant)]}'
            if input_source.new_state is None:
                return input_source.variable.name
            return f'{input_source.variable.name} ({"new" if input_source.new_state else "old"} state)'

        line(f'Program {self.name}')
        for label, variable_table in [('State variables', self.state_variables),
                                      ('Temporaries', self.temporaries)]:
            line()
            line(f'{label} ({variable_table.name}):')
            line(f'size: {variable_table.size}, alignment: {variable_table.alignment}', 2)
            for name, variable in sorted(variable_table.variables.items()):
                line(f'{name}: {variable.var_type}', 2)
                line(f'offset: {variable_table.offsets[variable]}', 4)
                if variable in self.initial_state:
                    value('initial', self.initial_state[variable], 4)

        line()
        line('Constants:')
        for index, constant in enumerate(self.constants):
            line(f'const.{index}: {constant.value_type}', 2)
            value('value', constant.value, 4)

        line()
        line('Functions:')
        for index, function in enumerate(self.functions):
            parameters = ', '.join(f'{name}: {value_type}' for name, value_type
                                   in zip(function.argument_names, function.parameter_types))
            line(f'func.{index}: {".".join(function.name)}({parameters}): {function.return_type}', 2)

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
                for name, input_sources, is_list in proc.dependencies:
                    sources = ', '.join(source(input_source) for input_source in input_sources)
                    if is_list:
                        sources = '[' + sources + ']'
                    line(f'{name}: {sources}', 8)
                line('resources:', 6)
                resources(proc.resources, 8)
                line('outputs:', 6)
                for name, output_target in proc.outputs.items():
                    line(f'{name}: {output_target.variable.name}', 8)
                if proc.body is not None:
                    line('locals:', 6)
                    for index, value_type in enumerate(proc.body.local_types):
                        line(f'local.{index}: {value_type}', 8)
                    line('instructions:', 6)
                    for index, instruction in enumerate(proc.body.instructions):
                        line(f'idx{index}: {instruction}', 8)

        variable_aliases = {name: source(input_source) for name, input_source in self.variables.items()}
        for label, aliases in [('Node aliases', self.node_aliases),
                               ('Singleton aliases', self.singleton_aliases),
                               ('Variable aliases', variable_aliases)]:
            line()
            line(label + ':')
            for name, target in sorted(aliases.items()):
                line(f'{name!r}: {target}', 2)
