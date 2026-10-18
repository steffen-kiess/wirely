"""Procedures, initializers, and their ports and static bindings."""

import dataclasses
import typing
from typing import Any

from wirely.program import RunStage
from wirely.compiler import model
from wirely.compiler.types import check_runtime_type

from .base import CompilerHint, Declaration, Identifier, ProgramError, SourceElement, SourcePosition, SyntaxElement, Value
from .specifiers import PortDirection, PortTypeSpecification, PortTypeSpecificationData, PortTypeSpecificationResource, NativeSpecifier
from .values import StringConstant, Reference
from .nodes import DeclarationState, register_name


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class StageDeclaration(SyntaxElement):
    stage: RunStage

    @staticmethod
    def create_input(*, source_position: SourcePosition) -> 'StageDeclaration':
        return StageDeclaration(stage=RunStage.INPUT, source_position=source_position)

    @staticmethod
    def create_default(*, source_position: SourcePosition) -> 'StageDeclaration':
        return StageDeclaration(stage=RunStage.DEFAULT, source_position=source_position)

    @staticmethod
    def create_output(*, source_position: SourcePosition) -> 'StageDeclaration':
        return StageDeclaration(stage=RunStage.OUTPUT, source_position=source_position)


class Port(SyntaxElement):
    """A port in a depends or produces clause."""

    reference: Reference | None
    alias: 'StringConstant | Identifier | None'

    @property
    def key(self) -> str:
        raise NotImplementedError

    @property
    def python_key(self) -> str:
        raise NotImplementedError


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class ReferencePort(Port):
    reference: Reference
    alias: StringConstant | Identifier | None = None

    @property
    def key(self) -> str:
        alias = self.alias
        if isinstance(alias, Identifier):
            return alias.id_str
        if isinstance(alias, StringConstant):
            return alias.value
        names = [identifier.id_str for identifier in self.reference.identifiers]
        if len(names) != 1 or names[0] in ('default input', 'default output'):
            raise ProgramError(self, 'Default ports and complex references require an explicit alias')
        return names[0]

    @property
    def python_key(self) -> str:
        return self.key


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class TypedPort(Port):
    """A typed procedure port, which connections supply or consume.

    In native procedures, a string alias gives the Python key.
    """

    name: Identifier
    port_spec: PortTypeSpecification
    alias: StringConstant | None = None
    reference: None = dataclasses.field(default=None, init=False)

    @staticmethod
    def from_reference(reference: Reference, alias: typing.Optional[StringConstant], port_spec: PortTypeSpecification, *,
                       source_position: SourcePosition) -> 'TypedPort':
        names = [identifier.id_str for identifier in reference.identifiers]
        if len(names) != 1 or names[0] in ('default input', 'default output'):
            raise ProgramError(reference, 'Typed port names must be identifiers')
        return TypedPort(reference.identifiers[0], port_spec, alias, source_position=source_position)

    @property
    def key(self) -> str:
        return self.name.id_str

    @property
    def python_key(self) -> str:
        return self.alias.value if self.alias is not None else self.key


def check_port_keys(ports: typing.Iterable[Port], *, python: bool = False) -> None:
    keys: dict[str, typing.Any] = {}
    for port in ports:
        key = port.python_key if python else port.key
        if key in keys:
            raise ProgramError(port, f'Duplicate port {"key" if python else "name"} {key!r}', hints=[
                CompilerHint(keys[key], 'Previous port was here'),
            ])
        keys[key] = port


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DependsDeclaration(SyntaxElement):
    dependencies: tuple[Port, ...]

    def __post_init__(self) -> None:
        for port in self.dependencies:
            if isinstance(port, TypedPort) and isinstance(port.port_spec, PortTypeSpecificationResource):
                port.port_spec.check_supported_input()

    @staticmethod
    def of(*dependencies: Port, source_position: SourcePosition) -> 'DependsDeclaration':
        return DependsDeclaration(dependencies, source_position=source_position)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class ProducesDeclaration(SyntaxElement):
    outputs: tuple[Port, ...]

    @staticmethod
    def of(*outputs: Port, source_position: SourcePosition) -> 'ProducesDeclaration':
        return ProducesDeclaration(outputs, source_position=source_position)


def resolve_dependencies(graph: 'model.NodeType', depends: DependsDeclaration) -> list:
    """Return the graph element each dependency of a depends clause references."""
    resolved: list[Any] = []
    check_port_keys(depends.dependencies)
    for port in depends.dependencies:
        dep = port.reference
        assert dep is not None
        if dep.length == 2:
            name, member = [identifier.id_str for identifier in dep.identifiers]
            variable = graph.value_nodes.get(name)
            if isinstance(variable, DeclarationState) and member in ('old', 'new'):
                resolved.append(model.StateRead(variable, member, dep))
                continue
            child = graph.instance_nodes.get(name)
            if child is not None:
                output = child.output_ports_byname.get(member)
                if output is not None:
                    resolved.append(output)
                    continue
            raise ProgramError(dep, f'Cannot resolve procedure dependency {dep}')
        if dep.length != 1:
            raise ProgramError(dep, 'Unsupported procedure dependency reference')
        identifier = dep.identifiers[0]
        name = identifier.id_str
        # TODO: Should not be done using nodes
        res: Any
        if name in graph.instance_nodes:
            res = graph.instance_nodes[name]
        elif name in graph.value_nodes:
            res = graph.value_nodes[name]
            if isinstance(res, DeclarationState):
                raise ProgramError(dep, 'State dependencies must explicitly reference .old or .new')
        elif name in graph.input_ports:
            res = graph.input_ports[name]
        elif name in graph.local_resources:
            res = graph.local_resources[name]
        else:
            raise ProgramError(dep, f'Could not find dependency')
        resolved.append(res)

    return resolved


def dependency_is_resource(dependency: object) -> bool:
    if isinstance(dependency, model.InstanceNode):
        dependency = dependency.output_ports_byname.get('')
    return isinstance(getattr(dependency, 'port_spec', None), PortTypeSpecificationResource)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class StaticBinding(SyntaxElement):
    expression: Value
    alias: Identifier | StringConstant | None = None

    @property
    def key(self) -> str:
        if isinstance(self.alias, Identifier):
            return self.alias.id_str
        if isinstance(self.alias, StringConstant):
            return self.alias.value
        if isinstance(self.expression, Reference) and self.expression.length == 1:
            return self.expression.identifiers[0].id_str
        raise ProgramError(self, 'Static binding requires an alias')


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class UsesDeclaration(SyntaxElement):
    bindings: tuple[StaticBinding, ...]

    @staticmethod
    def of(*bindings: StaticBinding, source_position: SourcePosition) -> 'UsesDeclaration':
        return UsesDeclaration(bindings, source_position=source_position)

    def check(self, scope: 'model.NodeType') -> None:
        from ..expressions import compile_constant

        keys = set()
        for binding in self.bindings:
            with binding.context:
                key = binding.key
                if key in keys:
                    raise ProgramError(binding, f'Duplicate static binding key {key!r}')
                keys.add(key)
                expression = binding.expression
                if binding.alias is None:
                    if key not in scope.static_declarations:
                        raise ProgramError(expression, f'Unknown static declaration {key!r}')
                compile_constant(scope, expression, None, {})


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationInitializer(Declaration):
    depends: DependsDeclaration
    produces: ProducesDeclaration
    uses: UsesDeclaration | None = None

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        if not isinstance(graph, model.NodeType) or not graph.is_native:
            raise ProgramError(self, 'Native initializer requires a native node type')
        if graph.initializer is not None:
            raise ProgramError(self, 'Duplicate native initializer', hints=[
                CompilerHint(graph.initializer.declaration, 'Previous initializer was here'),
            ])
        graph.initializer = TypeInitializer(graph, self)

    def bind_body(self, graph: 'model.NodeType') -> None:
        assert graph.initializer is not None and graph.initializer.declaration is self
        graph.initializer.bind_body()


class TypeInitializer:
    """The native initializer of a node type in a compilation."""

    def __init__(self, graph: 'model.NodeType', declaration: DeclarationInitializer) -> None:
        self.graph = graph
        self.declaration = declaration
        self.output_keys: dict[model.GraphElement, str] = {}  # Produced resource output or local resource -> key.
        self._dependencies: list | None = None
        self._resolving = False

    @property
    def depends(self) -> DependsDeclaration:
        return self.declaration.depends

    @property
    def produces(self) -> ProducesDeclaration:
        return self.declaration.produces

    @property
    def uses(self) -> 'UsesDeclaration | None':
        return self.declaration.uses

    def resolved_dependencies(self) -> list:
        """Return the graph elements of the dependencies, resolving them first if needed."""
        if self._dependencies is not None:
            return self._dependencies
        if self._resolving:
            raise Exception('Recursive initializer dependency resolution')
        self._resolving = True
        try:
            self._dependencies = resolve_dependencies(self.graph, self.depends)
        finally:
            self._resolving = False
        return self._dependencies

    def bind_body(self) -> None:
        graph = self.graph
        for port in self.depends.dependencies:
            if isinstance(port, TypedPort):
                raise ProgramError(port, 'Initializer dependencies must reference resources')
        for port in self.produces.outputs:
            if isinstance(port, TypedPort):
                raise ProgramError(port, 'Initializer outputs must reference resource outputs')
        check_port_keys(self.produces.outputs)
        declarations: dict[typing.Any, typing.Any] = {}
        for port in self.produces.outputs:
            assert port.reference is not None
            with port.context:
                output = port.reference.resolve(graph, PortDirection.Input, 'destination').port
            if not isinstance(output, model.LocalResource) and (
                    not isinstance(output, model.TypeOutputPort) or output.graph is not graph
                    or output.resource_type is None):
                raise ProgramError(port, 'Initializer outputs must be resource outputs or local resources of the node type')
            if output in declarations:
                raise ProgramError(port, 'Duplicate initializer output', hints=[
                    CompilerHint(declarations[output], 'Previous output was here'),
                ])
            declarations[output] = port
            self.output_keys[output] = port.key
        self.resolved_dependencies()
        if self.uses is not None:
            self.uses.check(graph)
        for port, dep in zip(self.depends.dependencies, self.resolved_dependencies()):
            if not dependency_is_resource(dep):
                raise ProgramError(port, 'Initializer dependencies must be resources')


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationProcedure(Declaration):
    native_spec: NativeSpecifier | None
    name: Identifier
    alias: StringConstant | None
    stage: StageDeclaration
    depends: DependsDeclaration
    produces: ProducesDeclaration
    uses: UsesDeclaration | None = None
    _: dataclasses.KW_ONLY
    body: Value | None = None
    is_native: bool = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'is_native', self.native_spec is not None)
        if self.alias is not None:
            prefix, _, method = self.alias.value.rpartition(':')
            if not prefix or not method:
                raise ProgramError(self.alias, "Native procedure names have the form 'Type:procedure'")

        # TODO: Allow this? In which cases? Does this need checks that the outputs are not used in earlier stages?
        # Currently this is used to update a state node after writing to a CSV file. The new value for the state node might depend on the result (success or not) of writing to the file. Probably at least updates to state variables should be allowed in the output stage.
        # Disallowing this would make it possible to save the state before the output stage (and therefore before any results of the current step become externally visible). Or state variables could be saved twice, once before and once after the output stage.
        # if self.stage.stage == RunStage.OUTPUT and len(self.produces.outputs) > 0:
        #     hints = [
        #         CompilerHint(self.depends, 'Output declared here'),
        #     ]
        #     raise ProgramError(self.stage, f'Procedure name {self.name} is declared as output stage but produces an output', hints=hints)

    @staticmethod
    def create_body(name: Identifier, stage: StageDeclaration, depends: DependsDeclaration, produces: ProducesDeclaration,
                    body: Value, *, source_position: SourcePosition) -> 'DeclarationProcedure':
        procedure = DeclarationProcedure(None, name, None, stage, depends, produces,
                                         source_position=source_position, body=body)
        for port in (*depends.dependencies, *produces.outputs):
            if isinstance(port, TypedPort) and port.alias is not None:
                raise ProgramError(port.alias, 'Typed port keys require a native procedure')
            if isinstance(port.alias, StringConstant):
                raise ProgramError(port.alias, 'Procedure aliases must be identifiers')
        return procedure

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        name = self.name.id_str
        if name in graph.procedures:
            hints = [
                CompilerHint(graph.procedures[name].declaration, 'Previous procedure was here'),
            ]
            raise ProgramError(self, f'Duplicate procedure name {name} in {graph}', hints=hints)
        register_name(graph, self.name, self)
        for previous in graph.procedures.values():
            if self.is_native and previous.is_native and previous.python_name == self.python_name:
                raise ProgramError(self.alias or self.name, f'Duplicate Python procedure name {self.python_name!r}', hints=[
                    CompilerHint(previous.alias or previous.name, 'Previous Python procedure name was here'),
                ])
        graph.procedures[name] = TypeProcedure(graph, self)

    @property
    def python_name(self) -> str:
        """The method name, which native code also uses for triggers."""
        return self.alias.value.rpartition(':')[2] if self.alias is not None else self.name.id_str

    def prepare_interface(self, graph: 'model.NodeType') -> None:
        graph.procedures[self.name.id_str].prepare_interface()

    def bind_body(self, graph: 'model.NodeType') -> None:
        graph.procedures[self.name.id_str].bind_body()


class TypeProcedure:
    """A procedure of a node type in a compilation."""

    def __init__(self, graph: 'model.NodeType', declaration: DeclarationProcedure) -> None:
        self.graph = graph
        self.declaration = declaration
        self.input_ports: dict[str, model.ProcedureInputPort] = {}
        self.output_ports: dict[str, model.ProcedureOutputPort] = {}

    @property
    def name(self) -> Identifier:
        return self.declaration.name

    @property
    def alias(self) -> StringConstant | None:
        return self.declaration.alias

    @property
    def is_native(self) -> bool:
        return self.declaration.is_native

    @property
    def stage(self) -> StageDeclaration:
        return self.declaration.stage

    @property
    def depends(self) -> DependsDeclaration:
        return self.declaration.depends

    @property
    def produces(self) -> ProducesDeclaration:
        return self.declaration.produces

    @property
    def uses(self) -> 'UsesDeclaration | None':
        return self.declaration.uses

    @property
    def python_name(self) -> str:
        return self.declaration.python_name

    @property
    def native_name(self) -> str:
        """The native procedure name, such as 'Std.WarnOnEvent:run'."""
        if self.alias is not None:
            return self.alias.value
        prefix = self.graph.native_name if self.graph.is_native else self.graph.qualified_name
        return f'{prefix}:{self.name.id_str}'

    def bind_body(self) -> None:
        if self.graph.parent_graph is None and self.is_native:
            raise ProgramError(self.declaration, 'Native procedures must belong to a node type')
        self.bind_connections(self.graph)

    def bind_connections(self, graph: 'model.NodeType') -> None:
        # Reference ports connect to the ports they reference.
        for declaration in self.depends.dependencies:
            if isinstance(declaration, ReferencePort):
                port = self.input_ports[declaration.key]
                with declaration.context:
                    model.connect(port.counterpart, port, declaration)
        for declaration in self.produces.outputs:
            if isinstance(declaration, ReferencePort):
                output_port = self.output_ports[declaration.key]
                if isinstance(output_port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(declaration, 'Procedures cannot produce resources')
                with declaration.context:
                    model.connect(output_port, output_port.counterpart, declaration)
        if self.uses is not None:
            self.uses.check(graph)
        if self.stage.stage == RunStage.INPUT:
            for port in self.input_ports.values():
                if not isinstance(port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(self.stage, f'Procedure name {self.name} is declared as input stage but depends on something else', hints=[
                        CompilerHint(self.depends, 'Dependencies declared here'),
                    ])

    def resolve_port(self, name: Identifier, port_name: Identifier | None,
                     is_input: bool) -> 'model.ProcedureInputPort | model.ProcedureOutputPort':
        """Resolve a reference to a port, given as procedure and port name."""
        procedure = name.id_str
        if port_name is None:
            raise ProgramError(name, f'Procedure references require a port, as in {procedure}.port')
        ports, others = ((self.input_ports, self.output_ports) if is_input
                         else (self.output_ports, self.input_ports))
        port = ports.get(port_name.id_str)
        if port is None:
            if port_name.id_str in others:
                direction = 'output' if is_input else 'input'
                raise ProgramError(port_name, f'Port {port_name.id_str!r} of procedure {procedure!r} is an {direction}')
            raise ProgramError(port_name, f'Unable to find port {port_name.id_str!r} of procedure {procedure!r}')
        # Reference ports are connected to what they reference and are not addressable.
        if not isinstance(port.declaration, TypedPort):
            raise ProgramError(port_name, f'Port {port_name.id_str!r} of procedure {procedure!r} is not a typed port')
        return port

    def validate_ports(self) -> None:
        for port in self.input_ports.values():
            if not port.is_list and port.connected_source is None:
                raise ProgramError(port.declaration, f'Input port {port.name!r} of procedure {self.name.id_str!r} has no source')

    def prepare_interface(self) -> None:
        new_graph = self.graph
        check_port_keys(self.depends.dependencies)
        check_port_keys(self.produces.outputs)
        if self.is_native:
            check_port_keys(self.depends.dependencies, python=True)
            check_port_keys(self.produces.outputs, python=True)
        self.input_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=True)
            for declaration in self.depends.dependencies
        }
        self.output_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=False)
            for declaration in self.produces.outputs
        }

    @typing.overload
    def create_port(self, graph: 'model.NodeType', declaration: Port, *,
                    is_input: typing.Literal[True]) -> 'model.ProcedureInputPort': ...

    @typing.overload
    def create_port(self, graph: 'model.NodeType', declaration: Port, *,
                    is_input: typing.Literal[False]) -> 'model.ProcedureOutputPort': ...

    def create_port(self, graph: 'model.NodeType', declaration: Port, *,
                    is_input: bool) -> 'model.ProcedureInputPort | model.ProcedureOutputPort':
        port_type = model.ProcedureInputPort if is_input else model.ProcedureOutputPort
        if isinstance(declaration, ReferencePort):
            direction = PortDirection.Output if is_input else PortDirection.Input
            purpose = 'dependency' if is_input else 'destination'

            def counterpart() -> Any:
                assert declaration.reference is not None
                with declaration.context:
                    return declaration.reference.resolve(graph, direction, purpose).port
            return port_type(graph, self, declaration, counterpart=counterpart)

        assert isinstance(declaration, TypedPort)
        spec = declaration.port_spec
        if spec.list_spec.is_list and not is_input:
            raise ProgramError(spec, 'Got multi specifier for non-input')
        if isinstance(spec, PortTypeSpecificationData):
            var_type = graph.resolve_type(spec.ty)
            check_runtime_type(var_type, spec)
            resource_type = None
        else:
            if not is_input:
                raise ProgramError(declaration, 'Procedures cannot produce resources')
            var_type = None
            resource_type = graph.resolve_resource_type(spec.ty)
        specification = model.PortSpecification(var_type, spec, resource_type, spec.list_spec.is_list)
        return port_type(graph, self, declaration, specification=specification)
