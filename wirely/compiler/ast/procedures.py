"""Procedures, initializers, and their ports and compile-time reads."""

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
    """A port in a reads or writes clause."""

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
            raise ProgramError(port, f'Duplicate port {"key" if python else "name"} `{key}`', hints=[
                CompilerHint(keys[key], 'Previous port was here'),
            ])
        keys[key] = port


class ReadPort:
    """Creates the entry of a native reads clause: a port or a compile-time expression."""

    @staticmethod
    def create(expression: Value, alias: 'Identifier | StringConstant | None',
               port_spec: PortTypeSpecification | None, *,
               source_position: SourcePosition) -> 'Port | StaticBinding':
        if port_spec is not None:
            if not isinstance(expression, Reference):
                raise ProgramError(expression, 'Typed port names must be identifiers')
            if isinstance(alias, Identifier):
                raise ProgramError(alias, 'Typed port keys must be strings')
            return TypedPort.from_reference(expression, alias, port_spec, source_position=source_position)
        if isinstance(expression, Reference):
            return ReferencePort(expression, alias, source_position=source_position)
        if alias is None:
            raise ProgramError(expression, 'Compile-time expressions in reads clauses require an alias')
        return StaticBinding(expression, alias, source_position=source_position)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class ReadsDeclaration(SyntaxElement):
    """A reads clause: ports and, for native procedures, compile-time expressions."""

    entries: tuple['Port | StaticBinding', ...]

    def __post_init__(self) -> None:
        for port in self.entries:
            if isinstance(port, TypedPort) and isinstance(port.port_spec, PortTypeSpecificationResource):
                port.port_spec.check_supported_input()

    @staticmethod
    def of(*entries: 'Port | StaticBinding', source_position: SourcePosition) -> 'ReadsDeclaration':
        return ReadsDeclaration(entries, source_position=source_position)

    @staticmethod
    def depends_on(*entries: 'Port | StaticBinding', source_position: SourcePosition) -> 'ReadsDeclaration':
        raise ProgramError(SourceElement(source_position=source_position), '`depends on` was replaced by `reads`')

    def split(self, graph: 'model.NodeType | None') -> 'tuple[ReadPorts, StaticReads | None]':
        """Separate the ports from the compile-time reads.

        A reference is a port when its first name is a node, port, state, binding, or procedure of the graph.
        Other references, such as parameters, constants, and types, are compile-time reads. Without a graph,
        every reference is a port.
        """
        ports: list[Port] = []
        bindings: list[StaticBinding] = []
        for entry in self.entries:
            if isinstance(entry, StaticBinding):
                bindings.append(entry)
            elif graph is not None and isinstance(entry, ReferencePort) and not is_graph_reference(graph, entry.reference):
                bindings.append(StaticBinding(entry.reference, entry.alias, source_position=entry.source_position))
            else:
                ports.append(entry)
        static = StaticReads(tuple(bindings), source_position=self.source_position) if bindings else None
        return ReadPorts(tuple(ports), source_position=self.source_position), static


def is_graph_reference(graph: 'model.NodeType', reference: Reference) -> bool:
    """Return whether a reference names an element of the graph rather than a compile-time value."""
    names = [identifier.id_str for identifier in reference.identifiers]
    if names[0] in ('default input', 'default output'):
        return True
    declaration = graph.name_declarations.get(names[0])
    return declaration is not None and not isinstance(declaration, model.DeclarationParameter)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class ReadPorts(SyntaxElement):
    """The ports of a reads clause, which the procedure depends on."""

    dependencies: tuple[Port, ...]


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class WritesDeclaration(SyntaxElement):
    outputs: tuple[Port, ...]

    @staticmethod
    def of(*outputs: Port, source_position: SourcePosition) -> 'WritesDeclaration':
        return WritesDeclaration(outputs, source_position=source_position)


def resolve_dependencies(graph: 'model.NodeType', depends: ReadPorts) -> list:
    """Return the graph element each port of a reads clause references."""
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
            raise ProgramError(dep, f'Cannot resolve procedure dependency `{dep}`')
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
        raise ProgramError(self, 'Compile-time expressions in reads clauses require an alias')


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class StaticReads(SyntaxElement):
    """The compile-time reads of a native procedure or initializer."""

    bindings: tuple[StaticBinding, ...]

    def check(self, scope: 'model.NodeType', ports: typing.Iterable[Port] = ()) -> None:
        """Check the keys and values; ports are the other reads, whose keys must differ."""
        from ..expressions import compile_constant

        keys = {port.key for port in ports}
        for binding in self.bindings:
            with binding.context:
                key = binding.key
                if key in keys:
                    raise ProgramError(binding, f'Duplicate read key `{key}`')
                keys.add(key)
                expression = binding.expression
                if binding.alias is None and key not in scope.static_declarations:
                    raise ProgramError(expression, f'`{expression}` is not a port, parameter, or constant; compile-time expressions in reads clauses require an alias')
                compile_constant(scope, expression, None, {})


def check_native_name(alias: StringConstant | None) -> None:
    if alias is not None:
        prefix, _, method = alias.value.rpartition(':')
        if not prefix or not method:
            raise ProgramError(alias, 'Native procedure names have the form `Type:procedure`')


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationInitializer(Declaration):
    name: Identifier
    alias: StringConstant | None
    reads: ReadsDeclaration
    writes: WritesDeclaration
    read_ports: ReadPorts = dataclasses.field(init=False)
    static_reads: StaticReads | None = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        check_native_name(self.alias)
        self.split_reads(None)

    def split_reads(self, graph: 'model.NodeType | None') -> None:
        read_ports, static_reads = self.reads.split(graph)
        object.__setattr__(self, 'read_ports', read_ports)
        object.__setattr__(self, 'static_reads', static_reads)

    @property
    def python_name(self) -> str:
        return self.alias.value.rpartition(':')[2] if self.alias is not None else self.name.id_str

    def collect_declarations(self, graph: 'model.NodeType') -> None:
        if not isinstance(graph, model.NodeType) or graph.parent_graph is None:
            raise ProgramError(self, 'Native initializers must belong to a node type')
        if graph.initializer is not None:
            raise ProgramError(self, 'Only one initializer per node type is supported', hints=[
                CompilerHint(graph.initializer.declaration, 'Previous initializer was here'),
            ])
        register_name(graph, self.name, self)
        graph.initializer = TypeInitializer(graph, self)

    def prepare_interface(self, graph: 'model.NodeType') -> None:
        self.split_reads(graph)

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
    def name(self) -> Identifier:
        return self.declaration.name

    @property
    def read_ports(self) -> ReadPorts:
        return self.declaration.read_ports

    @property
    def writes(self) -> WritesDeclaration:
        return self.declaration.writes

    @property
    def static_reads(self) -> 'StaticReads | None':
        return self.declaration.static_reads

    @property
    def native_name(self) -> str:
        """The native initializer name, such as 'KNX.Connection:create'."""
        if self.declaration.alias is not None:
            return self.declaration.alias.value
        return f'{self.graph.native_prefix}:{self.name.id_str}'

    def resolved_dependencies(self) -> list:
        """Return the graph elements of the read ports, resolving them first if needed."""
        if self._dependencies is not None:
            return self._dependencies
        if self._resolving:
            raise Exception('Recursive initializer dependency resolution')
        self._resolving = True
        try:
            self._dependencies = resolve_dependencies(self.graph, self.read_ports)
        finally:
            self._resolving = False
        return self._dependencies

    def bind_body(self) -> None:
        graph = self.graph
        for port in self.read_ports.dependencies:
            if isinstance(port, TypedPort):
                raise ProgramError(port, 'Initializer reads must reference fixed values or compile-time values')
        for port in self.writes.outputs:
            if isinstance(port, TypedPort):
                raise ProgramError(port, 'Initializer writes must reference fixed outputs')
        check_port_keys(self.writes.outputs)
        declarations: dict[typing.Any, typing.Any] = {}
        for port in self.writes.outputs:
            assert port.reference is not None
            with port.context:
                output = port.reference.resolve(graph, PortDirection.Input, 'destination').port
            if not isinstance(output, model.LocalResource) and (
                    not isinstance(output, model.TypeOutputPort) or output.graph is not graph
                    or output.resource_type is None):
                raise ProgramError(port, 'Initializer writes must be fixed outputs or fixed bindings of the node type')
            if output in declarations:
                raise ProgramError(port, 'Duplicate initializer output', hints=[
                    CompilerHint(declarations[output], 'Previous output was here'),
                ])
            declarations[output] = port
            self.output_keys[output] = port.key
        self.resolved_dependencies()
        if self.static_reads is not None:
            self.static_reads.check(graph, self.read_ports.dependencies)
        for port, dep in zip(self.read_ports.dependencies, self.resolved_dependencies()):
            if not dependency_is_resource(dep):
                raise ProgramError(port, 'Initializer reads must be fixed values or compile-time values')


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationProcedure(Declaration):
    native_spec: NativeSpecifier | None
    name: Identifier
    alias: StringConstant | None
    stage: StageDeclaration
    reads: ReadsDeclaration
    writes: WritesDeclaration
    _: dataclasses.KW_ONLY
    body: Value | None = None
    is_native: bool = dataclasses.field(init=False)
    read_ports: ReadPorts = dataclasses.field(init=False)
    static_reads: StaticReads | None = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'is_native', self.native_spec is not None)
        check_native_name(self.alias)
        self.split_reads(None)

    def split_reads(self, graph: 'model.NodeType | None') -> None:
        read_ports, static_reads = self.reads.split(graph)
        object.__setattr__(self, 'read_ports', read_ports)
        object.__setattr__(self, 'static_reads', static_reads)

        # TODO: Allow this? In which cases? Does this need checks that the outputs are not used in earlier stages?
        # Currently this is used to update a state node after writing to a CSV file. The new value for the state node might depend on the result (success or not) of writing to the file. Probably at least updates to state variables should be allowed in the output stage.
        # Disallowing this would make it possible to save the state before the output stage (and therefore before any results of the current step become externally visible). Or state variables could be saved twice, once before and once after the output stage.
        # if self.stage.stage == RunStage.OUTPUT and len(self.writes.outputs) > 0:
        #     hints = [
        #         CompilerHint(self.reads, 'Output declared here'),
        #     ]
        #     raise ProgramError(self.stage, f'Procedure name {self.name} is declared as output stage but produces an output', hints=hints)

    @staticmethod
    def create_body(name: Identifier, stage: StageDeclaration, reads: ReadsDeclaration, writes: WritesDeclaration,
                    body: Value, *, source_position: SourcePosition) -> 'DeclarationProcedure':
        procedure = DeclarationProcedure(None, name, None, stage, reads, writes,
                                         source_position=source_position, body=body)
        for port in (*procedure.read_ports.dependencies, *writes.outputs):
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
            raise ProgramError(self, f'Duplicate procedure name `{name}` in `{graph}`', hints=hints)
        register_name(graph, self.name, self)
        for previous in graph.procedures.values():
            if self.is_native and previous.is_native and previous.python_name == self.python_name:
                raise ProgramError(self.alias or self.name, f'Duplicate Python procedure name `{self.python_name}`', hints=[
                    CompilerHint(previous.alias or previous.name, 'Previous Python procedure name was here'),
                ])
        graph.procedures[name] = TypeProcedure(graph, self)

    @property
    def python_name(self) -> str:
        """The method name, which native code also uses for triggers."""
        return self.alias.value.rpartition(':')[2] if self.alias is not None else self.name.id_str

    def prepare_interface(self, graph: 'model.NodeType') -> None:
        if self.is_native:
            self.split_reads(graph)
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
    def read_ports(self) -> ReadPorts:
        return self.declaration.read_ports

    @property
    def writes(self) -> WritesDeclaration:
        return self.declaration.writes

    @property
    def static_reads(self) -> 'StaticReads | None':
        return self.declaration.static_reads

    @property
    def python_name(self) -> str:
        return self.declaration.python_name

    @property
    def native_name(self) -> str:
        """The native procedure name, such as 'Std.WarnOnEvent:run'."""
        if self.alias is not None:
            return self.alias.value
        return f'{self.graph.native_prefix}:{self.name.id_str}'

    def bind_body(self) -> None:
        if self.graph.parent_graph is None and self.is_native:
            raise ProgramError(self.declaration, 'Native procedures must belong to a node type')
        self.bind_connections(self.graph)

    def bind_connections(self, graph: 'model.NodeType') -> None:
        # Reference ports connect to the ports they reference.
        for declaration in self.read_ports.dependencies:
            if isinstance(declaration, ReferencePort):
                port = self.input_ports[declaration.key]
                with declaration.context:
                    model.connect(port.counterpart, port, declaration)
        for declaration in self.writes.outputs:
            if isinstance(declaration, ReferencePort):
                output_port = self.output_ports[declaration.key]
                if isinstance(output_port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(declaration, 'Only initializers can write fixed values')
                with declaration.context:
                    model.connect(output_port, output_port.counterpart, declaration)
        if self.static_reads is not None:
            self.static_reads.check(graph, self.read_ports.dependencies)
        if self.stage.stage == RunStage.INPUT:
            for port in self.input_ports.values():
                if not isinstance(port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(self.stage, f'Procedure `{self.name}` is declared as input stage but reads values that are not fixed', hints=[
                        CompilerHint(self.declaration.reads, 'Reads declared here'),
                    ])

    def resolve_port(self, name: Identifier, port_name: Identifier | None,
                     is_input: bool) -> 'model.ProcedureInputPort | model.ProcedureOutputPort':
        """Resolve a reference to a port, given as procedure and port name."""
        procedure = name.id_str
        if port_name is None:
            raise ProgramError(name, f'Procedure references require a port, as in `{procedure}.port`')
        ports, others = ((self.input_ports, self.output_ports) if is_input
                         else (self.output_ports, self.input_ports))
        port = ports.get(port_name.id_str)
        if port is None:
            if port_name.id_str in others:
                direction = 'output' if is_input else 'input'
                raise ProgramError(port_name, f'Port `{port_name.id_str}` of procedure `{procedure}` is an {direction}')
            raise ProgramError(port_name, f'Unable to find port `{port_name.id_str}` of procedure `{procedure}`')
        # Reference ports are connected to what they reference and are not addressable.
        if not isinstance(port.declaration, TypedPort):
            raise ProgramError(port_name, f'Port `{port_name.id_str}` of procedure `{procedure}` is not a typed port')
        return port

    def validate_ports(self) -> None:
        for port in self.input_ports.values():
            if not port.is_list and port.connected_source is None:
                raise ProgramError(port.declaration, f'Input port `{port.name}` of procedure `{self.name.id_str}` has no source')

    def prepare_interface(self) -> None:
        new_graph = self.graph
        check_port_keys(self.read_ports.dependencies)
        check_port_keys(self.writes.outputs)
        if self.is_native:
            check_port_keys(self.read_ports.dependencies, python=True)
            check_port_keys(self.writes.outputs, python=True)
        self.input_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=True)
            for declaration in self.read_ports.dependencies
        }
        self.output_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=False)
            for declaration in self.writes.outputs
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
                raise ProgramError(declaration, 'Only initializers can write fixed values')
            var_type = None
            resource_type = graph.resolve_resource_type(spec.ty)
        specification = model.PortSpecification(var_type, spec, resource_type, spec.list_spec.is_list)
        return port_type(graph, self, declaration, specification=specification)
