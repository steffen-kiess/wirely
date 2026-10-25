from __future__ import annotations

import enum
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pprint import pformat
from types import MappingProxyType
from typing import TextIO

from ..util import immutable_enum
from ..types import Type, TypeEnum, TypeList, TypeOption, TypeResource, TypeStruct
from .names import member_owner
from .procedures import Constant, ConstOperand, Function, Instruction, Opcode, ProcedureBody


@immutable_enum
class RunStage(enum.Enum):
    """Ordered load and per-step execution phases."""

    LOAD = enum.auto()  # Compute fixed values when loading the program.
    INPUT = enum.auto()  # Read external inputs before normal computation.
    DEFAULT = enum.auto()  # Run normal computation.
    OUTPUT = enum.auto()  # Write external outputs after normal computation.


@dataclass(frozen=True, slots=True, eq=False)
class Variable:
    """A fixed, state, or temporary variable, identified by object identity."""

    # Resolved value type used for validation and comparison: a runtime type, or a resource for fixed values.
    var_type: Type
    name: str | None = field(default=None, kw_only=True)  # Optional qualified name for diagnostics.

    def __post_init__(self) -> None:
        if self.name is not None and not isinstance(self.name, str):
            raise TypeError('name parameter must be a str or None')
        if not self.is_resource and not self.var_type.is_runtime_type:
            raise ValueError(f'Type `{self.var_type}` is not a runtime type: {self.var_type.runtime_type_error()}')

    @property
    def is_resource(self) -> bool:
        """Whether the variable holds a resource, kept outside the byte buffer."""
        return isinstance(self.var_type, TypeResource)

    def __repr__(self) -> str:
        return f'Variable[{self}]'

    def __str__(self) -> str:
        return self.name if self.name is not None else '<unnamed variable>'


@dataclass(frozen=True, slots=True, init=False, eq=False)
class VariableTable:
    """An immutable collection of variable definitions, without live values."""

    name: str  # Collection name for diagnostics.
    variables: tuple[Variable, ...]  # Variables in supplied order, independent of their names.
    variable_set: frozenset[Variable]  # Variables allowed in a runtime instance.
    offsets: MappingProxyType[Variable, int]  # Byte offsets, ordered by decreasing alignment.
    size: int  # Buffer size in bytes, including final alignment padding.
    alignment: int  # Required alignment of the table's starting byte offset.

    def __init__(self, variables: Iterable[Variable], name: str) -> None:
        """Build a shared compact layout while preserving supplied order."""
        variables = tuple(variables)
        seen = set()
        for var in variables:
            if not isinstance(var, Variable):
                raise Exception('var parameter contains object that is not a Variable')
            if var in seen:
                raise ValueError('Variable occurs more than once in a table')
            seen.add(var)
        offsets = {}
        size = 0
        alignment = 1
        for var in sorted(variables, key=lambda var: -var.var_type.alignment):
            alignment = max(alignment, var.var_type.alignment)
            offsets[var] = size
            size += var.var_type.size
        size = (size + alignment - 1) & ~(alignment - 1)
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'variables', variables)
        object.__setattr__(self, 'variable_set', frozenset(variables))
        object.__setattr__(self, 'offsets', MappingProxyType(offsets))
        object.__setattr__(self, 'size', size)
        object.__setattr__(self, 'alignment', alignment)

    def label(self, variable: Variable) -> str:
        """Return a name or a table-local numeric label for display."""
        if variable not in self.variable_set:
            raise ValueError('Variable is not in this table')
        if variable.name is not None:
            return variable.name
        return f'{self.name}[{tuple(self.offsets).index(variable)}]'


@immutable_enum
class VariableBank(enum.Enum):
    """The storage a procedure input or output refers to."""

    CONST_VALUES = enum.auto()  # Constants, collected in Program.constants.
    FIXED_VALUES = enum.auto()  # Fixed values, including resources, written by load procedures.
    TEMPORARIES = enum.auto()  # Values computed during a step.
    OLD_STATE = enum.auto()  # The state at the start of a step.
    NEW_STATE = enum.auto()  # The state written during a step.


@dataclass(frozen=True, slots=True)
class InputSource:
    """A procedure input from a constant, fixed value, temporary, or state bank."""

    bank: VariableBank
    variable: Variable | None = None  # Source in the bank; None for constants.
    constant: Constant | None = None  # Source for CONST_VALUES.

    def __post_init__(self) -> None:
        if (self.bank == VariableBank.CONST_VALUES) != (self.constant is not None):
            raise ValueError('Constant sources, and only they, require a constant')
        if self.bank == VariableBank.CONST_VALUES:
            if self.variable is not None:
                raise ValueError('Constant sources have no variable')
        elif not isinstance(self.variable, Variable):
            raise ValueError(f'Expected a variable for bank {self.bank.name}')
        elif self.variable.is_resource and self.bank != VariableBank.FIXED_VALUES:
            raise ValueError('Resources belong to the fixed values')

    @property
    def resource(self) -> Variable | None:
        """The variable of a resource source, or None."""
        return self.variable if self.variable is not None and self.variable.is_resource else None

    @property
    def value_type(self) -> Type:
        if self.constant is not None:
            return self.constant.value_type
        assert self.variable is not None
        return self.variable.var_type


@dataclass(frozen=True, slots=True)
class OutputTarget:
    """A procedure output targeting fixed storage, a temporary, or new state."""

    bank: VariableBank  # FIXED_VALUES, TEMPORARIES, or NEW_STATE.
    variable: Variable  # Destination in the bank; resources only for FIXED_VALUES.

    def __post_init__(self) -> None:
        if self.bank not in (VariableBank.FIXED_VALUES, VariableBank.TEMPORARIES, VariableBank.NEW_STATE):
            raise ValueError(f'Outputs cannot target bank {self.bank.name}')
        if self.variable.is_resource and self.bank != VariableBank.FIXED_VALUES:
            raise ValueError('Resources belong to the fixed values')


def _sorted_names(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(names, key=lambda name: name.encode()))


@dataclass(frozen=True, slots=True)
class NativeProcedureEntry:
    """A native procedure: its name, interface names, and whether it is an initializer."""

    name: str  # Native procedure name, such as 'Std.WarnOnEvent:run'.
    input_names: tuple[str, ...]  # Names of the inputs, including compile-time reads, sorted.
    output_names: tuple[str, ...]  # Names of the outputs, sorted.
    initializer: bool = False  # Whether load procedures using it create native state.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'input_names', _sorted_names(self.input_names))
        object.__setattr__(self, 'output_names', _sorted_names(self.output_names))


@dataclass(frozen=True, slots=True, eq=False)
class Procedure:
    """A scheduled instance of a native or bytecode procedure with its bindings."""

    inputs: tuple[InputSource, ...]  # Native: one per input name; bytecode: operands input.N.
    outputs: tuple[OutputTarget, ...]  # Native: one per output name; bytecode: operands output.N.
    name: str | None = field(default=None, kw_only=True)  # Optional instance name, such as 'a.b.run'.
    native: NativeProcedureEntry | None = field(default=None, kw_only=True)  # None for a bytecode procedure.
    body: ProcedureBody | None = field(default=None, kw_only=True)  # Compiled .wrl body; None for a native procedure.

    def __post_init__(self) -> None:
        if self.name is not None and not isinstance(self.name, str):
            raise TypeError('name parameter must be a str or None')
        object.__setattr__(self, 'inputs', tuple(self.inputs))
        object.__setattr__(self, 'outputs', tuple(self.outputs))

    @property
    def node(self) -> str | None:
        """The path of the owning node."""
        return None if self.name is None else member_owner(self.name)

    @property
    def initializer(self) -> bool:
        return self.native is not None and self.native.initializer

    def named_inputs(self) -> tuple[tuple[str, InputSource], ...]:
        """Return the inputs of a native procedure with their names."""
        assert self.native is not None
        return tuple(zip(self.native.input_names, self.inputs))

    def named_outputs(self) -> tuple[tuple[str, OutputTarget], ...]:
        """Return the outputs of a native procedure with their names."""
        assert self.native is not None
        return tuple(zip(self.native.output_names, self.outputs))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramSymbols:
    """Debug names and aliases of a program; stripped programs have none."""

    name: str = ''  # Program name for diagnostics.
    node_aliases: Mapping[str, str] = field(default_factory=dict)  # Declared node paths to node paths.
    singleton_aliases: Mapping[str, str] = field(default_factory=dict)  # Singleton type names to node paths.
    variable_aliases: Mapping[str, InputSource] = field(default_factory=dict)  # Source names to their sources.

    def __post_init__(self) -> None:
        for name in ('node_aliases', 'singleton_aliases', 'variable_aliases'):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class Program:
    """An executable program with immutable structure and optional debug data.

    Referenced type descriptors, payload objects, and graph are not frozen.
    """

    state_variables: VariableTable  # Variables persisted between engine steps.
    temporaries: VariableTable  # Per-step variables.
    initial_state: Mapping[Variable, object]  # Starting values of persistent state.
    procs: Mapping[RunStage, tuple[Procedure, ...]]  # Stages and procedures in execution order.
    fixed_values: VariableTable = field(default_factory=lambda: VariableTable((), 'fixed'))
    symbols: ProgramSymbols = field(default_factory=ProgramSymbols)
    graph: object | None = None  # Optional compiler graph for debugging and visualization.
    # Constants of input bindings, instructions, and variable aliases, by identity in first-use order.
    constants: tuple[Constant, ...] = field(init=False)
    functions: tuple[Function, ...] = field(init=False)  # Functions called by instructions, sorted by name.
    native_procedures: tuple[NativeProcedureEntry, ...] = field(init=False)  # Used entries, sorted by name.

    def __post_init__(self) -> None:
        object.__setattr__(self, 'initial_state', MappingProxyType(dict(self.initial_state)))
        object.__setattr__(self, 'procs', MappingProxyType({
            stage: tuple(schedule) for stage, schedule in self.procs.items()
        }))
        object.__setattr__(self, 'constants', _collect_constants(self))
        object.__setattr__(self, 'functions', _collect_functions(self))
        object.__setattr__(self, 'native_procedures', _collect_native_procedures(self))

    def variable_label(self, variable: Variable) -> str:
        """Return a variable name or its bank-local numeric label."""
        for bank, table in (('state', self.state_variables), ('temporary', self.temporaries),
                            ('fixed', self.fixed_values)):
            if variable in table.variable_set:
                if variable.name is not None:
                    return variable.name
                return f'{bank}[{tuple(table.offsets).index(variable)}]'
        raise ValueError('Variable is not in this program')

    def procedure_label(self, procedure: Procedure) -> str:
        """Return an instance name or its program-local numeric label."""
        index = procedure_instances(self).index(procedure)
        return procedure.name if procedure.name is not None else f'procedure[{index}]'

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

        constant_indices = {id(constant): index for index, constant in enumerate(self.constants)}
        function_indices = {function.name: index for index, function in enumerate(self.functions)}

        def operand_text(operand: object) -> str:
            if isinstance(operand, ConstOperand):
                operand = operand.constant
            if isinstance(operand, Constant):
                return f'const.{constant_indices[id(operand)]}'
            if isinstance(operand, Function):
                return f'func.{function_indices[operand.name]}'
            return str(operand)

        def instruction_text(instruction: Instruction) -> str:
            operands = ', '.join(operand_text(operand) for operand in instruction.operands)
            return instruction.opcode.name + (' ' + operands if operands else '')

        def source(input_source: InputSource) -> str:
            if input_source.constant is not None:
                return f'const.{constant_indices[id(input_source.constant)]}'
            assert input_source.variable is not None
            label = self.variable_label(input_source.variable)
            if input_source.bank == VariableBank.OLD_STATE:
                return f'{label} (old state)'
            if input_source.bank == VariableBank.NEW_STATE:
                return f'{label} (new state)'
            return label

        line(f'Program {self.symbols.name}')
        for label, variable_table in [('State variables', self.state_variables),
                                      ('Temporaries', self.temporaries), ('Fixed values', self.fixed_values)]:
            line()
            line(f'{label} ({variable_table.name}):')
            line(f'size: {variable_table.size}, alignment: {variable_table.alignment}', 2)
            for variable in sorted(variable_table.variables, key=self.variable_label):
                name = self.variable_label(variable)
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

        line()
        line('Native procedures:')
        for entry in self.native_procedures:
            line(f'{entry.name}:', 2)
            for label, names in [('inputs', entry.input_names), ('outputs', entry.output_names)]:
                line(f'{label}: {", ".join(names)}' if names else f'{label}:', 4)
            line(f'initializer: {entry.initializer}', 4)

        line()
        line('Stages:')
        for stage, schedule in self.procs.items():
            line(f'{stage.name}:', 2)
            for index, proc in enumerate(schedule, 1):
                if proc.native is not None:
                    line(f'{index}. {self.procedure_label(proc)}: native {proc.native.name}', 4)
                    input_labels = proc.native.input_names
                    output_labels = proc.native.output_names
                else:
                    line(f'{index}. {self.procedure_label(proc)}: bytecode', 4)
                    input_labels = tuple(f'input.{index}' for index in range(len(proc.inputs)))
                    output_labels = tuple(f'output.{index}' for index in range(len(proc.outputs)))
                line('inputs:', 6)
                for label, input_source in zip(input_labels, proc.inputs):
                    line(f'{label}: {source(input_source)}', 8)
                line('outputs:', 6)
                for label, output_target in zip(output_labels, proc.outputs):
                    line(f'{label}: {self.variable_label(output_target.variable)}', 8)
                if proc.body is not None:
                    line('locals:', 6)
                    for index, value_type in enumerate(proc.body.local_types):
                        line(f'local.{index}: {value_type}', 8)
                    line('instructions:', 6)
                    for index, instruction in enumerate(proc.body.instructions):
                        line(f'idx{index}: {instruction_text(instruction)}', 8)

        variable_aliases = {name: source(input_source) for name, input_source in self.symbols.variable_aliases.items()}
        for label, aliases in [('Node aliases', self.symbols.node_aliases),
                               ('Singleton aliases', self.symbols.singleton_aliases),
                               ('Variable aliases', variable_aliases)]:
            line()
            line(label + ':')
            for name, target in sorted(aliases.items()):
                line(f'{name!r}: {target}', 2)


def _collect_constants(program: Program) -> tuple[Constant, ...]:
    constants: dict[int, Constant] = {}

    def add(constant: Constant | None) -> None:
        if constant is not None:
            constants.setdefault(id(constant), constant)

    for schedule in program.procs.values():
        for procedure in schedule:
            for source in procedure.inputs:
                add(source.constant)
            if procedure.body is not None:
                for instruction in procedure.body.instructions:
                    for operand in instruction.operands:
                        if isinstance(operand, ConstOperand):
                            add(operand.constant)
                        elif isinstance(operand, Constant):
                            add(operand)
    for source in program.symbols.variable_aliases.values():
        add(source.constant)
    return tuple(constants.values())


def _collect_native_procedures(program: Program) -> tuple[NativeProcedureEntry, ...]:
    entries: dict[str, NativeProcedureEntry] = {}
    for schedule in program.procs.values():
        for procedure in schedule:
            if procedure.native is None:
                continue
            previous = entries.setdefault(procedure.native.name, procedure.native)
            if previous != procedure.native:
                raise ValueError(f'Native procedure `{procedure.native.name}` is used with different interfaces')
    return tuple(entries[name] for name in _sorted_names(entries))


def _collect_functions(program: Program) -> tuple[Function, ...]:
    functions: dict[str, Function] = {}
    for schedule in program.procs.values():
        for procedure in schedule:
            if procedure.body is None:
                continue
            for instruction in procedure.body.instructions:
                if instruction.opcode != Opcode.CALL or len(instruction.operands) < 2:
                    continue
                function = instruction.operands[1]
                if not isinstance(function, Function):
                    continue
                previous = functions.setdefault(function.name, function)
                if previous != function:
                    raise ValueError(f'Function `{function.name}` is called with different signatures')
    return tuple(functions[name] for name in _sorted_names(functions))


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
        for field_type in value_type.field_types:
            yield from _declared_types(field_type, seen)


def used_declared_types(program: Program) -> Iterator[TypeEnum | TypeStruct]:
    """Yield each enum and struct type that occurs in the runtime types of a program once."""
    value_types = [variable.var_type for table in (program.state_variables, program.temporaries, program.fixed_values)
                   for variable in table.variables]
    for function in program.functions:
        value_types += [*function.parameter_types, function.return_type]
    for schedule in program.procs.values():
        for procedure in schedule:
            if procedure.body is not None:
                value_types += procedure.body.local_types
    value_types += [constant.value_type for constant in program.constants]
    seen: set[int] = set()  # Identities of visited types.
    for value_type in value_types:
        yield from _declared_types(value_type, seen)


def procedure_instances(program: Program) -> tuple[Procedure, ...]:
    """Return the procedures of all stages in stage order, the order of the ProcedureInstances table."""
    return tuple(procedure for stage in RunStage for procedure in program.procs.get(stage, ()))
