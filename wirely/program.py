from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pprint import pformat
from types import MappingProxyType

import wirely as blocks


@dataclass(frozen=True)
class ResultBinding:
    variable: blocks.Variable
    state_new: blocks.Variable | None = None
    state_changed: blocks.Variable | None = None


@dataclass(frozen=True)
class NativeInstance:
    name: str
    type_name: tuple[str, ...]
    has_native_state: bool
    parameters: Mapping[str, object]
    configuration: object
    initializer_bindings: tuple


@dataclass(frozen=True)
class Procedure:
    node_id: str
    reference: tuple[tuple[str, ...], str]
    node_context: NativeInstance
    dependencies: tuple
    resources: tuple
    results: Mapping[str, ResultBinding]
    state_dependencies: tuple[blocks.Variable, ...]

    @property
    def key(self):
        return self.node_id, self.reference[1]


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class Program:
    name: str
    state_variables: blocks.VariableBlock
    temporaries: blocks.VariableBlock
    initial_state: Mapping[blocks.Variable, object]
    instances: Mapping[str, NativeInstance]
    native_construction_order: tuple[str, ...]
    procs: Mapping[blocks.RunStage, tuple[Procedure, ...]]
    node_aliases: Mapping[str, str]
    singleton_aliases: Mapping[str, str]
    variables: Mapping[str, blocks.Variable]
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
