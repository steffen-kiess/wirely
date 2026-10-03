from __future__ import annotations

import enum
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pprint import pformat
from types import MappingProxyType

from .types import Type


class RunStage(enum.Enum):
    INPUT = enum.auto()
    DEFAULT = enum.auto()
    OUTPUT = enum.auto()


@dataclass(frozen=True, slots=True, eq=False)
class Variable:
    name: str
    var_type: Type

    def __post_init__(self):
        if not isinstance(self.name, str):
            raise Exception('name parameter is not a str')
        if self.var_type.contains_parameters:
            raise Exception('var_type parameter is not a concrete type')
        object.__setattr__(self, 'name', str(self.name))

    def __repr__(self):
        return f'Variable[{self}]'

    def __str__(self):
        return self.name


@dataclass(frozen=True, slots=True, init=False, eq=False)
class VariableBlock:
    name: str
    variables: Mapping[str, Variable]
    variable_set: frozenset[Variable]

    def __init__(self, variables, name):
        by_name = {}
        for var in variables:
            if not isinstance(var, Variable):
                raise Exception('var parameter contains object that is not a Variable')
            if var.name in by_name:
                raise Exception(f'Duplicate variable name {var.name!r}')
            by_name[var.name] = var
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'variables', MappingProxyType(by_name))
        object.__setattr__(self, 'variable_set', frozenset(by_name.values()))


@dataclass(frozen=True, slots=True)
class InputSourceBinding:
    """A procedure input from a temporary or the step's state snapshot."""

    variable: Variable
    state_changed: Variable | None = None


@dataclass(frozen=True, slots=True)
class ResultBinding:
    variable: Variable
    state_new: Variable | None = None
    state_changed: Variable | None = None


@dataclass(frozen=True, slots=True)
class NativeInstance:
    name: str
    type_name: tuple[str, ...]
    has_native_state: bool
    parameters: Mapping[str, object]
    configuration: object
    initializer_bindings: tuple

    def __post_init__(self):
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
    node_id: str
    reference: tuple[tuple[str, ...], str]
    node_context: NativeInstance
    dependencies: tuple
    resources: tuple
    results: Mapping[str, ResultBinding]
    state_dependencies: tuple[Variable, ...]

    def __post_init__(self):
        type_name, proc_name = self.reference
        object.__setattr__(self, 'reference', (tuple(type_name), proc_name))
        for field in ('dependencies', 'resources'):
            object.__setattr__(self, field, tuple(
                (name, tuple(bindings), is_list)
                for name, bindings, is_list in getattr(self, field)
            ))
        object.__setattr__(self, 'results', MappingProxyType(dict(self.results)))
        object.__setattr__(self, 'state_dependencies', tuple(self.state_dependencies))

    @property
    def key(self):
        return self.node_id, self.reference[1]


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class Program:
    name: str
    state_variables: VariableBlock
    temporaries: VariableBlock
    initial_state: Mapping[Variable, object]
    instances: Mapping[str, NativeInstance]
    native_construction_order: tuple[str, ...]
    procs: Mapping[RunStage, tuple[Procedure, ...]]
    node_aliases: Mapping[str, str]
    singleton_aliases: Mapping[str, str]
    variables: Mapping[str, Variable]
    graph: object | None = None

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
            for name, variable in sorted(variable_block.variables.items()):
                line(f'{name}: {variable.var_type}', 2)
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
                line('dependencies:', 6)
                for name, bindings, is_list in proc.dependencies:
                    sources = []
                    for binding in bindings:
                        source = binding.variable.name
                        if binding.state_changed is not None:
                            source += f' (old state; changed: {binding.state_changed.name})'
                        sources.append(source)
                    source = ', '.join(sources)
                    if is_list:
                        source = '[' + source + ']'
                    line(f'{name}: {source}', 8)
                line('resources:', 6)
                resources(proc.resources, 8)
                value('state-change dependencies', [var.name for var in proc.state_dependencies], 6)
                line('results:', 6)
                for name, binding in proc.results.items():
                    line(f'{name}: {binding.variable.name}', 8)
                    if binding.state_new is not None:
                        line(f'new: {binding.state_new.name}', 10)
                        line(f'changed: {binding.state_changed.name}', 10)

        for label, aliases in [('Node aliases', self.node_aliases),
                               ('Singleton aliases', self.singleton_aliases),
                               ('Variable aliases', self.variables)]:
            line()
            line(label + ':')
            for name, target in sorted(aliases.items()):
                line(f'{name!r}: {target}', 2)
