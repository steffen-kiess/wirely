"""Parse-only outline of declarations, ports, and connections.

outline() only parses the given sources, so it also works for programs that
do not compile. References to declarations that are not part of the sources,
for example node types from libraries that were not passed or nodes declared
in other files, stay unresolved instead of causing errors.

Elements are identified by their enclosing namespaces/node types and name.
Identical connections in a scope are distinguished by Connection.occurrence,
their index in source order; it is None for a unique connection.
wirely.compiler.editing changes sources using these IDs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator, Sequence
import functools
from dataclasses import dataclass, field
from typing import Any, TypeAlias, TypeVar, cast, overload

from . import ast
from .ast.procedures import Port as AstPort
from . import attributes as _attributes
from .diagnostics import Diagnostic
from .sources import ParseCache


@dataclass(frozen=True)
class Node:
    """A node instance, addressed by enclosing namespaces/node types and name."""
    path: tuple[str, ...]


@dataclass(frozen=True)
class NodeType:
    """A node type, addressed by enclosing namespaces/node types and name."""
    path: tuple[str, ...]


@dataclass(frozen=True)
class State:
    path: tuple[str, ...]


@dataclass(frozen=True)
class Binding:
    path: tuple[str, ...]



@dataclass(frozen=True)
class Constant:
    path: tuple[str, ...]


@dataclass(frozen=True)
class Parameter:
    path: tuple[str, ...]


@dataclass(frozen=True)
class Port:
    """An input or output of a node type; default ports are named 'default input'
    and 'default output'.
    """
    path: tuple[str, ...]
    direction: str


@dataclass(frozen=True)
class Procedure:
    path: tuple[str, ...]


@dataclass(frozen=True)
class ProcedurePort:
    procedure: Procedure
    key: str | None
    direction: str


@dataclass(frozen=True)
class Connection:
    """The edge from source to target in a connect statement within scope.

    References are written as in the source, e.g. 'node.output' or
    'default input'. With occurrence None, the edge must be unique.
    """
    scope: tuple[str, ...]
    source: str
    target: str
    occurrence: int | None = None


DeclarationId: TypeAlias = Node | NodeType | State | Binding | Constant | Parameter | Port | Procedure
ElementId: TypeAlias = DeclarationId | ProcedurePort | Connection


@dataclass(frozen=True)
class PortInfo:
    id: Port
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    direction: str
    # None for a default port.
    name: str | None
    kind: str
    is_list: bool
    type_text: str
    _declaration: ast.DeclarationPort | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ParameterBindingInfo:
    name: str
    value_text: str
    is_optional: bool
    span: tuple[int, int]


@dataclass(frozen=True)
class NodeInfo:
    id: Node
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    name: str
    type_text: str
    parameters: tuple[ParameterBindingInfo, ...]
    # None if the type is not declared in the sources.
    node_type: NodeType | None
    ports: tuple[PortInfo, ...] | None
    _declaration: ast.DeclarationInstance | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class StateInfo:
    id: State
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    name: str
    type_text: str
    initial_text: str
    _declaration: ast.DeclarationState | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class BindingInfo:
    id: Binding
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    name: str
    type_text: str
    kind: str
    _declaration: ast.DeclarationBinding | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ConstantInfo:
    id: Constant
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    name: str
    type_text: str
    value_text: str
    _declaration: ast.DeclarationConstant | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ParameterInfo:
    id: Parameter
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    name: str
    type_text: str
    default_text: str | None
    is_optional: bool
    _declaration: ast.DeclarationParameter | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ProcedurePortInfo:
    id: ProcedurePort
    filename: str
    span: tuple[int, int]
    key: str | None
    direction: str
    # Set for ports connected to what they reference.
    reference_text: str | None
    # Set for typed ports.
    kind: str | None
    is_list: bool
    type_text: str | None


@dataclass(frozen=True)
class ProcedureInfo:
    id: Procedure
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    name: str
    is_native: bool
    stage: str
    inputs: tuple[ProcedurePortInfo, ...]
    outputs: tuple[ProcedurePortInfo, ...]
    _declaration: ast.DeclarationProcedure | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Endpoint:
    """A connection end; element is None if the reference is unresolved."""
    text: str
    element: ElementId | None
    # A port name, 'old' or 'new', or None.
    port: str | None


@dataclass(frozen=True)
class Edge:
    # 'connect' or 'reference' for procedure ports connected to what they reference.
    kind: str
    id: Connection | None
    source: Endpoint
    target: Endpoint
    filename: str
    span: tuple[int, int]
    source_span: tuple[int, int] | None
    target_span: tuple[int, int] | None
    attributes: tuple
    _declaration: ast.DeclarationConnection | ast.ReferencePort | None = field(default=None, compare=False, repr=False)
    _source: ast.Reference | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Scope:
    kind: str
    path: tuple[str, ...]
    nodes: tuple[NodeInfo, ...]
    node_types: tuple['NodeTypeInfo', ...]
    namespaces: tuple['Scope', ...]
    states: tuple[StateInfo, ...]
    bindings: tuple[BindingInfo, ...]
    constants: tuple[ConstantInfo, ...]
    parameters: tuple[ParameterInfo, ...]
    ports: tuple[PortInfo, ...]
    procedures: tuple[ProcedureInfo, ...]
    edges: tuple[Edge, ...]
    # (filename, block) for each file, namespace block, or node type body.
    _blocks: tuple[tuple[str, Block], ...] = field(default=(), compare=False, repr=False)

    @property
    def filenames(self) -> tuple[str, ...]:
        """The files containing a part of the scope, in order."""
        return tuple(dict.fromkeys(filename for filename, _ in self._blocks))


@dataclass(frozen=True)
class NodeTypeInfo:
    id: NodeType
    filename: str
    span: tuple[int, int]
    attributes: tuple
    name: str
    has_initializer: bool
    is_singleton: bool
    scope: Scope
    _declaration: ast.DeclarationNodeType | None = field(default=None, compare=False, repr=False)

    @property
    def ports(self) -> tuple[PortInfo, ...]:
        return self.scope.ports

    @property
    def parameters(self) -> tuple[ParameterInfo, ...]:
        return self.scope.parameters


DeclarationRecord: TypeAlias = NodeInfo | NodeTypeInfo | StateInfo | BindingInfo | ConstantInfo | ParameterInfo | PortInfo | ProcedureInfo
Record: TypeAlias = DeclarationRecord | ProcedurePortInfo | Edge
RecordT = TypeVar('RecordT')
Block: TypeAlias = _attributes.ParsedFile | ast.DeclarationNodeType | ast.DeclarationLibrary


@dataclass(frozen=True)
class SourceFile:
    filename: str
    # The parse error, if the file contributes no declarations.
    error: Diagnostic | None


@dataclass(frozen=True)
class Outline:
    files: tuple[SourceFile, ...]
    scope: Scope

    @overload
    def find(self, target: Node) -> tuple[NodeInfo, ...]: ...

    @overload
    def find(self, target: NodeType) -> tuple[NodeTypeInfo, ...]: ...

    @overload
    def find(self, target: State) -> tuple[StateInfo, ...]: ...

    @overload
    def find(self, target: Binding) -> tuple[BindingInfo, ...]: ...

    @overload
    def find(self, target: Constant) -> tuple[ConstantInfo, ...]: ...

    @overload
    def find(self, target: Parameter) -> tuple[ParameterInfo, ...]: ...

    @overload
    def find(self, target: Port) -> tuple[PortInfo, ...]: ...

    @overload
    def find(self, target: Procedure) -> tuple[ProcedureInfo, ...]: ...

    @overload
    def find(self, target: ProcedurePort) -> tuple[ProcedurePortInfo, ...]: ...

    @overload
    def find(self, target: Connection) -> tuple[Edge, ...]: ...

    @overload
    def find(self, target: DeclarationId) -> tuple[DeclarationRecord, ...]: ...

    @overload
    def find(self, target: ElementId) -> tuple[Record, ...]: ...

    def find(self, target: ElementId) -> tuple[Record, ...]:
        """Return the records with the given ID, several for duplicate names."""
        if isinstance(target, Connection):
            edges = self._index.get(dataclasses.replace(target, occurrence=None), ())
            if target.occurrence is None:
                return tuple(edges)
            if 0 <= target.occurrence < len(edges):
                return (edges[target.occurrence],)
            return ()
        return tuple(self._index.get(target, ()))

    def find_scope(self, path: Sequence[str]) -> tuple[Scope, ...]:
        """Return the scopes with the given path, several for duplicate node types."""
        def visit(scope: Scope) -> Iterator[Scope]:
            if scope.path == tuple(path):
                yield scope
            for node_type in scope.node_types:
                yield from visit(node_type.scope)
            for namespace in scope.namespaces:
                yield from visit(namespace)

        return tuple(visit(self.scope))

    @functools.cached_property
    def _index(self) -> dict[ElementId, list[Record]]:
        index: dict[ElementId, list[Record]] = {}

        def add(key: ElementId, record: Record) -> None:
            index.setdefault(key, []).append(record)

        def visit(scope: Scope) -> None:
            records: tuple[DeclarationRecord, ...] = (*scope.nodes, *scope.states, *scope.bindings,
                                                     *scope.constants, *scope.parameters, *scope.ports)
            for record in records:
                add(record.id, record)
            for procedure in scope.procedures:
                add(procedure.id, procedure)
                for port in (*procedure.inputs, *procedure.outputs):
                    add(port.id, port)
            for edge in scope.edges:
                if edge.id is not None:
                    add(dataclasses.replace(edge.id, occurrence=None), edge)
            for node_type in scope.node_types:
                add(node_type.id, node_type)
                visit(node_type.scope)
            for namespace in scope.namespaces:
                visit(namespace)

        visit(self.scope)
        return index


def outline(sources: Iterable[tuple[str, str]], *, add_std: bool = False, cache: ParseCache | None = None) -> Outline:
    """Outline (filename, text) pairs; files that fail to parse are skipped."""
    return outline_parsed(*parse(sources, add_std=add_std, cache=cache))


def parse(sources: Iterable[tuple[str, str]], *,
          add_std: bool = False, cache: ParseCache | None = None) -> tuple[tuple[SourceFile, ...], list[tuple[str, _attributes.ParsedFile]]]:
    """Return the file records and the (filename, declarations) of files that parse."""
    from .sources import SourceError, parse_source, standard_library_cache, standard_library_texts

    parse = [(filename, text, standard_library_cache.parse_source)
             for filename, text in (standard_library_texts() if add_std else ())]
    parse_user = parse_source if cache is None else cache.parse_source
    parse += [(filename, text, parse_user) for filename, text in sources]
    files = []
    parsed_files = []
    for filename, text, parse_function in parse:
        try:
            parsed_files.append((filename, parse_function(filename, text).tree))
            error = None
        except SourceError as exception:
            error = exception.diagnostics()[0]
        files.append(SourceFile(filename, error))
    return tuple(files), parsed_files


def outline_parsed(files: tuple[SourceFile, ...],
                   parsed_files: Iterable[tuple[str, _attributes.ParsedFile]]) -> Outline:
    """Outline the result of parse()."""
    root = _Builder('global', (), None)
    for filename, declarations in parsed_files:
        root.blocks.append((filename, declarations))
        _collect(root, declarations)
    return Outline(files, _build_scope(root))


class _Builder:
    def __init__(self, kind: str, path: tuple[str, ...], parent: _Builder | None) -> None:
        self.kind = kind
        self.path = path
        self.parent = parent
        self.items: list[ast.Declaration] = []
        self.namespaces: dict[str, _Builder] = {}
        self.node_types: list[tuple[ast.DeclarationNodeType, _Builder]] = []
        self.blocks: list[tuple[str, Block]] = []
        self.port_records: tuple[PortInfo, ...] | None = None
        self.name_table: dict[str, dict[str, ElementId]] | None = None


def _collect(builder: _Builder, declarations: Iterable[ast.Declaration]) -> None:
    for item in declarations:
        if isinstance(item, ast.DeclarationLoadAssertion):
            continue
        if isinstance(item, ast.DeclarationLibrary):
            name = item.name.id_str
            child = builder.namespaces.get(name)
            if child is None:
                child = builder.namespaces[name] = _Builder('namespace', builder.path + (name,), builder)
            # A dotted namespace declaration only has a body in its innermost namespace.
            if not item.dotted:
                child.blocks.append((_filename(item), item))
            _collect(child, item.declarations)
        elif isinstance(item, ast.DeclarationNodeType):
            assert item.name is not None
            child = _Builder('node_type', builder.path + (item.name.id_str,), builder)
            child.blocks.append((_filename(item), item))
            builder.node_types.append((item, child))
            _collect(child, item.declarations)
        else:
            builder.items.append(item)


def _text(element: ast.SourceElement) -> str:
    position = element.source_position
    assert position.meta is not None
    return position.input_data[position.meta.start_pos:position.meta.end_pos]


def _span(element: ast.SourceElement) -> tuple[int,int]:
    meta = element.source_position.meta
    assert meta is not None
    return (meta.start_pos, meta.end_pos)


def _filename(element: ast.SourceElement) -> str:
    return cast(str, element.source_position.filename)


def _reference_text(reference: ast.Reference) -> str:
    return '.'.join(identifier.id_str for identifier in reference.identifiers)


def attribute_value(attributes: Iterable[_attributes.Attribute], name: str) -> Any:
    """Return the validated value of the first attribute called name, or None if missing or invalid."""
    for attribute in attributes:
        if attribute.name == name:
            try:
                return _attributes.ATTRIBUTE_REGISTRY[name].validate(attribute)
            except ast.ProgramError:
                return None
    return None


def _position(attributes: Iterable[_attributes.Attribute]) -> tuple | None:
    return attribute_value(attributes, 'Diagram.Position')


def _direction(direction: ast.PortDirection) -> str:
    return 'input' if direction == ast.PortDirection.Input else 'output'


def _port_kind(spec: ast.PortTypeSpecification) -> str:
    if isinstance(spec, ast.PortTypeSpecificationFixed):
        return 'fixed'
    assert isinstance(spec, ast.PortTypeSpecificationData)
    return 'value' if spec.kind_spec.kind == ast.ConnectionKind.Value else 'event'


def _ports(builder: _Builder) -> tuple[PortInfo, ...]:
    if builder.port_records is None:
        builder.port_records = tuple(
            PortInfo(Port(builder.path + (item.name_str,), _direction(item.direction)),
                     _filename(item), _span(item), item.outer_attributes,
                     _position(item.outer_attributes), _direction(item.direction),
                     None if item.name is None else item.name_str, _port_kind(item.port_spec),
                     item.port_spec.list_spec.is_list, _text(item.port_spec.ty), item)
            for item in builder.items if isinstance(item, ast.DeclarationPort))
    return builder.port_records


def _names(builder: _Builder) -> dict[str, dict[str, ElementId]]:
    """Map each kind of name in a scope to the IDs of its declarations."""
    if builder.name_table is None:
        table: dict[str, dict[str, ElementId]] = {key: {} for key in ('static', 'input', 'output', 'state', 'binding', 'procedure',
                                     'node')}
        for item in builder.items:
            if isinstance(item, ast.DeclarationConstant):
                table['static'][item.name.id_str] = Constant(builder.path + (item.name.id_str,))
            elif isinstance(item, ast.DeclarationParameter):
                table['static'][item.name.id_str] = Parameter(builder.path + (item.name.id_str,))
            elif isinstance(item, ast.DeclarationPort):
                direction = _direction(item.direction)
                table[direction][item.name_str] = Port(builder.path + (item.name_str,), direction)
            elif isinstance(item, ast.DeclarationState):
                table['state'][item.name_str] = State(builder.path + (item.name_str,))
            elif isinstance(item, ast.DeclarationBinding):
                table['binding'][item.name_str] = Binding(builder.path + (item.name_str,))
            elif isinstance(item, ast.DeclarationProcedure):
                table['procedure'][item.name.id_str] = Procedure(builder.path + (item.name.id_str,))
            elif isinstance(item, ast.DeclarationInstance):
                table['node'][item.name.id_str] = Node(builder.path + (item.name.id_str,))
        builder.name_table = table
    return builder.name_table


def _resolve_node_type(builder: _Builder, reference: ast.Reference) -> _Builder | None:
    """Return the builder of the referenced node type, like Scope.resolve_node_type."""
    names = [identifier.id_str for identifier in reference.identifiers]
    scope: _Builder | None = builder
    while scope is not None:
        if len(names) >= 2:
            namespace = scope.namespaces.get(names[0])
            for name in names[1:-1]:
                if namespace is None:
                    break
                namespace = namespace.namespaces.get(name)
            if namespace is not None:
                matches = [child for item, child in namespace.node_types if item.name is not None and item.name.id_str == names[-1]]
                if matches:
                    return matches[0] if len(matches) == 1 else None
        else:
            matches = [child for item, child in scope.node_types if item.name is not None and item.name.id_str == names[0]]
            if matches:
                return matches[0] if len(matches) == 1 else None
        scope = scope.parent
    return None


def _resolve_endpoint(builder: _Builder, reference: ast.Reference, is_target: bool) -> Endpoint:
    """Resolve a connection end like ResolvedPortReference, within one scope."""
    text = _reference_text(reference)
    if reference.length > 2:
        return Endpoint(text, None, None)
    name = reference.identifiers[0].id_str
    port = reference.identifiers[1].id_str if reference.length == 2 else None
    names = _names(builder)
    if is_target:
        kinds = ('static', 'output', 'state', 'binding', 'procedure', 'node')
    else:
        kinds = ('static', 'state', 'input', 'binding', 'procedure', 'node')
    for kind in kinds:
        if kind in ('input', 'output') and port is not None:
            continue
        element = names[kind].get(name)
        if element is not None:
            return Endpoint(text, element, port)
    return Endpoint(text, None, port)


def _port_key(port: AstPort) -> str | None:
    try:
        return port.key
    except ast.ProgramError:
        return None


def _procedure_port(procedure_id: Procedure, port: AstPort, direction: str) -> ProcedurePortInfo:
    common = (ProcedurePort(procedure_id, _port_key(port), direction), _filename(port), _span(port),
              _port_key(port), direction)
    if isinstance(port, ast.TypedPort):
        spec = port.port_spec
        return ProcedurePortInfo(*common, None, _port_kind(spec), spec.list_spec.is_list, _text(spec.ty))
    assert isinstance(port, ast.ReferencePort)
    return ProcedurePortInfo(*common, _text(port.reference), None, False, None)


def _record(builder: _Builder, item: ast.Declaration) -> Record | None:
    path = builder.path
    attributes = item.outer_attributes
    common = (_filename(item), _span(item))
    if isinstance(item, ast.DeclarationInstance):
        instantiation = item.instantiation
        attributes = item.outer_attributes + instantiation.inner_attributes
        node_type = _resolve_node_type(builder, instantiation.type_name)
        parameters = tuple(ParameterBindingInfo(binding.name.id_str, _text(binding.value),
                                                binding.is_optional, _span(binding))
                           for binding in instantiation.parameters)
        return NodeInfo(Node(path + (item.name.id_str,)), *common, attributes, _position(attributes),
                        item.name.id_str, _text(instantiation.type_name), parameters,
                        None if node_type is None else NodeType(node_type.path),
                        None if node_type is None else _ports(node_type), item)
    if isinstance(item, ast.DeclarationState):
        return StateInfo(State(path + (item.name_str,)), *common, attributes, _position(attributes),
                         item.name_str, _text(item.var_type), _text(item.initial_value), item)
    if isinstance(item, ast.DeclarationBinding):
        return BindingInfo(Binding(path + (item.name_str,)), *common, attributes,
                           _position(attributes), item.name_str, _text(item.port_spec.ty),
                           _port_kind(item.port_spec), item)
    if isinstance(item, ast.DeclarationConstant):
        assert item.default_value is not None
        return ConstantInfo(Constant(path + (item.name.id_str,)), *common, attributes,
                            _position(attributes), item.name.id_str, _text(item.ty),
                            _text(item.default_value), item)
    if isinstance(item, ast.DeclarationParameter):
        default = None if item.default_value is None else _text(item.default_value)
        return ParameterInfo(Parameter(path + (item.name.id_str,)), *common, attributes,
                             _position(attributes), item.name.id_str, _text(item.ty), default,
                             item.is_optional, item)
    if isinstance(item, ast.DeclarationProcedure):
        procedure_id = Procedure(path + (item.name.id_str,))
        return ProcedureInfo(
            procedure_id, *common, attributes, _position(attributes), item.name.id_str,
            item.is_native, item.stage.stage.name.lower(),
            tuple(_procedure_port(procedure_id, port, 'input') for port in _read_ports(item)),
            tuple(_procedure_port(procedure_id, port, 'output') for port in item.writes.outputs),
            item)
    return None


def _read_ports(item: ast.DeclarationProcedure) -> list[AstPort]:
    """Return the ports of a reads clause, without compile-time expressions."""
    return [entry for entry in item.reads.entries if isinstance(entry, AstPort)]


def _procedure_endpoint(procedure_id: Procedure, port: AstPort) -> Endpoint:
    key = _port_key(port)
    name = procedure_id.path[-1]
    return Endpoint(name if key is None else f'{name}.{key}', procedure_id, key)


def _edges(builder: _Builder, item: ast.Declaration) -> Iterator[Edge]:
    if isinstance(item, ast.DeclarationConnection):
        target = _resolve_endpoint(builder, item.target, True)
        for source in item.sources:
            connection = Connection(builder.path, _reference_text(source), target.text)
            yield Edge('connect', connection, _resolve_endpoint(builder, source, False), target,
                       _filename(item), _span(item), _span(source), _span(item.target),
                       item.outer_attributes + source.outer_attributes, item, source)
    elif isinstance(item, ast.DeclarationProcedure):
        procedure_id = Procedure(builder.path + (item.name.id_str,))
        for port in _read_ports(item):
            if isinstance(port, ast.ReferencePort):
                endpoint = _procedure_endpoint(procedure_id, port)
                yield Edge('reference', None, _resolve_endpoint(builder, port.reference, False),
                           endpoint, _filename(port), _span(port), _span(port.reference), None, (), port)
        for port in item.writes.outputs:
            if isinstance(port, ast.ReferencePort):
                endpoint = _procedure_endpoint(procedure_id, port)
                yield Edge('reference', None, endpoint,
                           _resolve_endpoint(builder, port.reference, True),
                           _filename(port), _span(port), None, _span(port.reference), (), port)


def _with_occurrences(edges: Sequence[Edge]) -> tuple[Edge, ...]:
    counts: dict[Connection, int] = {}
    for edge in edges:
        if edge.id is not None:
            counts[edge.id] = counts.get(edge.id, 0) + 1
    seen: dict[Connection, int] = {}
    result = []
    for edge in edges:
        if edge.id is not None and counts[edge.id] > 1:
            occurrence = seen.get(edge.id, 0)
            seen[edge.id] = occurrence + 1
            edge = dataclasses.replace(edge, id=dataclasses.replace(edge.id, occurrence=occurrence))
        result.append(edge)
    return tuple(result)


def _build_scope(builder: _Builder) -> Scope:
    records = [record for record in (_record(builder, item) for item in builder.items) if record is not None]

    def of_type(record_type: type[RecordT]) -> tuple[RecordT, ...]:
        return tuple(record for record in records if isinstance(record, record_type))

    node_types = []
    for item, child in builder.node_types:
        assert item.name is not None
        node_types.append(NodeTypeInfo(
            NodeType(child.path), _filename(item), _span(item),
            item.outer_attributes + item.inner_attributes, item.name.id_str,
            item.has_initializer, item.is_singleton, _build_scope(child), item,
        ))
    return Scope(
        builder.kind, builder.path, of_type(NodeInfo), tuple(node_types),
        tuple(_build_scope(child) for child in builder.namespaces.values()),
        of_type(StateInfo), of_type(BindingInfo), of_type(ConstantInfo),
        of_type(ParameterInfo),
        _ports(builder), of_type(ProcedureInfo),
        _with_occurrences([edge for item in builder.items for edge in _edges(builder, item)]),
        tuple(builder.blocks))
