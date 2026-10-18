from __future__ import annotations

import enum
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pprint import pformat
from types import MappingProxyType
from typing import TextIO

from ..util import immutable_enum
from ..types import Type, TypeEnum, TypeList, TypeOption, TypeStruct
from .procedures import Constant, Function, ProcedureBody


@immutable_enum
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

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise Exception('name parameter is not a str')
        if not self.var_type.is_runtime_type:
            raise ValueError(f'Type {self.var_type} is not a runtime type: {self.var_type.runtime_type_error()}')
        object.__setattr__(self, 'name', str(self.name))

    def __repr__(self) -> str:
        return f'Variable[{self}]'

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True, init=False, eq=False)
class VariableTable:
    """An immutable collection of variable definitions, without live values."""

    name: str  # Collection name for diagnostics.
    variables: Mapping[str, Variable]  # Variables indexed by qualified name.
    variable_set: frozenset[Variable]  # Variables allowed in a runtime instance.
    offsets: MappingProxyType[Variable, int]  # Byte offsets, ordered by decreasing alignment.
    size: int  # Buffer size in bytes, including final alignment padding.
    alignment: int  # Required alignment of the table's starting byte offset.

    def __init__(self, variables: Iterable[Variable], name: str) -> None:
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

    def __post_init__(self) -> None:
        if (self.variable is None) == (self.constant is None):
            raise ValueError('Expected either a variable or a constant')
        if self.constant is not None and self.new_state is not None:
            raise ValueError('Constant sources have no state phase')

    @property
    def value_type(self) -> Type:
        if self.constant is not None:
            return self.constant.value_type
        assert self.variable is not None
        return self.variable.var_type


@dataclass(frozen=True, slots=True)
class OutputTarget:
    """A procedure output targeting a temporary or the next step's state."""

    variable: Variable  # Destination temporary or persistent state variable.


@dataclass(frozen=True, slots=True)
class ResourceBinding:
    """A resource input bound to a resource output of a native provider."""

    provider: str  # Provider node ID, a key of Program.instances.
    output: str  # Key under which the provider's initializer produces the output.

    def __str__(self) -> str:
        return f'{self.provider}.{self.output}'


def _resource_types(resources: Mapping[str, str]) -> Mapping[str, str]:
    """Return resource types by key, sorted."""
    return MappingProxyType({key: resources[key] for key in sorted(resources)})


@dataclass(frozen=True, slots=True)
class NativeProcedureInterface:
    """Declared interface of a native procedure."""

    uses: tuple[str, ...]  # Names bound by `uses`, sorted.
    resources: Mapping[str, str]  # Resource input key -> native resource type name.
    inputs: tuple[str, ...]  # Value and event input keys, sorted.
    outputs: tuple[str, ...]  # Output keys, sorted.

    def __post_init__(self) -> None:
        for name in ('uses', 'inputs', 'outputs'):
            object.__setattr__(self, name, tuple(sorted(getattr(self, name))))
        object.__setattr__(self, 'resources', _resource_types(self.resources))


@dataclass(frozen=True, slots=True)
class NativeNodeType:
    """Declared interface of a node type with native state, linked by name at runtime."""

    name: str  # Native node type name, such as 'KNX.Switch'.
    initializer_uses: tuple[str, ...]  # Names bound by the initializer's `uses`, sorted.
    initializer_resources: Mapping[str, str]  # Resource input key -> resource type.
    resource_outputs: Mapping[str, str]  # Initializer `produces` key -> resource type.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'initializer_uses', tuple(sorted(self.initializer_uses)))
        for name in ('initializer_resources', 'resource_outputs'):
            object.__setattr__(self, name, _resource_types(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class NativeInstance:
    """Stored node context for procedures and optional native state creation."""

    name: str  # Instance name exposed to native code.
    type_name: str | None  # Native node type name; None without native state.
    parameters: Mapping[str, object]  # Resolved parameters as ValueTypes or DynamicValues.
    initializer_bindings: Mapping[str, ResourceBinding]  # Initializer resource inputs by name.
    initializer_values: Mapping[str, Constant] = field(default_factory=dict)  # Typed initializer `uses` values.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'initializer_values', MappingProxyType(dict(self.initializer_values)))
        parameters = MappingProxyType(dict(self.parameters))
        object.__setattr__(self, 'parameters', parameters)
        object.__setattr__(self, 'initializer_bindings', MappingProxyType(dict(self.initializer_bindings)))

    @property
    def has_native_state(self) -> bool:
        """Whether the engine creates a native state object."""
        return self.type_name is not None


@dataclass(frozen=True, slots=True)
class Procedure:
    """A scheduled procedure with its inputs and output destinations."""

    node_id: str  # Owning node's key in Program.instances.
    # Native procedure name, such as 'Std.WarnOnEvent:run'; a 'Type:name' label for bodies.
    reference: str
    node_context: NativeInstance  # Stored context for this node.
    dependencies: tuple  # (input name/debug label, InputSource) entries in operand order.
    resources: Mapping[str, ResourceBinding]  # Resource inputs by name.
    outputs: Mapping[str, OutputTarget]  # Output destinations indexed by output name.
    body: ProcedureBody | None = None  # Compiled .wrl body; None selects native dispatch.
    values: Mapping[str, Constant] = field(default_factory=dict)  # Typed procedure `uses` values.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'values', MappingProxyType(dict(self.values)))
        object.__setattr__(self, 'dependencies', tuple((name, source) for name, source in self.dependencies))
        object.__setattr__(self, 'resources', MappingProxyType(dict(self.resources)))
        object.__setattr__(self, 'outputs', MappingProxyType(dict(self.outputs)))

    @property
    def key(self) -> tuple[str, str | tuple[str, str]]:
        """Return an identity that triggers use: the method name of native procedures."""
        name = self.reference.rpartition(':')[2]
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
    native_types: Mapping[str, NativeNodeType] = field(default_factory=dict)  # By type name.
    native_procedures: Mapping[str, NativeProcedureInterface] = field(default_factory=dict)  # By name.
    graph: object | None = None  # Optional compiler graph for debugging and visualization.

    def __post_init__(self) -> None:
        for name in ('initial_state', 'instances', 'node_aliases', 'singleton_aliases', 'variables',
                     'native_types', 'native_procedures'):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        object.__setattr__(self, 'procs', MappingProxyType({
            stage: tuple(schedule) for stage, schedule in self.procs.items()
        }))
        object.__setattr__(self, 'native_construction_order', tuple(self.native_construction_order))
        object.__setattr__(self, 'constants', tuple(self.constants))
        object.__setattr__(self, 'functions', tuple(self.functions))

    def dump(self, file: TextIO | None = None) -> None:
        """Write the runtime program to a text stream (stdout by default)."""
        if file is None:
            file = sys.stdout

        def line(text: str = '', indent: int = 0) -> None:
            print(' ' * indent + text, file=file)

        def value(label: str, item: object, indent: int) -> None:
            formatted = pformat(item, width=100 - indent, sort_dicts=True)
            line(label + ':', indent)
            for part in formatted.splitlines():
                line(part, indent + 2)

        def static_bindings(label: str, values: Mapping[str, Constant], indent: int) -> None:
            line(label + ':', indent)
            for name, constant in sorted(values.items()):
                line(f'{name}: {constant.value_type}', indent + 2)
                value('value', constant.value, indent + 4)

        def resources(bindings: Mapping[str, ResourceBinding], indent: int) -> None:
            for name, binding in bindings.items():
                line(f'{name}: {binding}', indent)

        constant_indices = {id(constant): index for index, constant in enumerate(self.constants)}

        def source(input_source: InputSource) -> str:
            if input_source.constant is not None:
                return f'const.{constant_indices[id(input_source.constant)]}'
            assert input_source.variable is not None
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
            parameters = ', '.join(str(value_type) for value_type in function.parameter_types)
            line(f'func.{index}: {function.name}({parameters}): {function.return_type}', 2)

        def names(label: str, items: Iterable[str], indent: int) -> None:
            line(f'{label}: {", ".join(items)}' if items else f'{label}:', indent)

        def resource_types(label: str, resources: Mapping[str, str], indent: int) -> None:
            line(label + ':', indent)
            for key, resource_type in resources.items():
                line(f'{key}: {resource_type}', indent + 2)

        line()
        line('Native types:')
        for type_name, native_type in sorted(self.native_types.items()):
            line(f'{type_name}:', 2)
            names('initializer uses', native_type.initializer_uses, 4)
            resource_types('initializer resources', native_type.initializer_resources, 4)
            resource_types('resource outputs', native_type.resource_outputs, 4)

        line()
        line('Native procedures:')
        for name, interface in sorted(self.native_procedures.items()):
            line(f'{name}:', 2)
            names('uses', interface.uses, 4)
            resource_types('resources', interface.resources, 4)
            names('inputs', interface.inputs, 4)
            names('outputs', interface.outputs, 4)

        line()
        line('Native construction order:')
        for index, node_id in enumerate(self.native_construction_order, 1):
            line(f'{index}. {node_id}', 2)

        line()
        line('Node instances:')
        for node_id, instance in sorted(self.instances.items()):
            line(f'{node_id}:', 2)
            line(f'name: {instance.name}', 4)
            line(f'native state type: {instance.type_name}', 4)
            value('parameters', dict(instance.parameters), 4)
            static_bindings('initializer values', instance.initializer_values, 4)
            line('initializer resources:', 4)
            resources(instance.initializer_bindings, 6)

        line()
        line('Stages:')
        for stage, schedule in self.procs.items():
            line(f'{stage.name}:', 2)
            for index, proc in enumerate(schedule, 1):
                line(f'{index}. {proc.node_id}: {proc.reference}', 4)
                line(f'node context: {proc.node_context.name}', 6)
                static_bindings('values', proc.values, 6)
                line('dependencies:', 6)
                for name, input_source in proc.dependencies:
                    line(f'{name}: {source(input_source)}', 8)
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


def _declared_types(value_type: Type, seen: set[int]) -> Iterator[TypeEnum | TypeStruct]:
    if id(value_type) in seen:
        return
    seen.add(id(value_type))
    if isinstance(value_type, TypeEnum):
        yield value_type
    elif isinstance(value_type, (TypeOption, TypeList)):
        yield from _declared_types(value_type.inner_type, seen)
    elif isinstance(value_type, TypeStruct):
        yield value_type
        for field_type in value_type.fields.values():
            yield from _declared_types(field_type, seen)


def used_declared_types(program: Program) -> Iterator[TypeEnum | TypeStruct]:
    """Yield each enum and struct type that occurs in the runtime types of a program once."""
    constants = [*program.constants]
    value_types = [variable.var_type for table in (program.state_variables, program.temporaries)
                   for variable in table.variables.values()]
    for function in program.functions:
        value_types += [*function.parameter_types, function.return_type]
    for instance in program.instances.values():
        constants += instance.initializer_values.values()
    for schedule in program.procs.values():
        for procedure in schedule:
            constants += procedure.values.values()
            if procedure.body is not None:
                value_types += procedure.body.local_types
    value_types += [constant.value_type for constant in constants]
    seen: set[int] = set()  # Identities of visited types.
    for value_type in value_types:
        yield from _declared_types(value_type, seen)
