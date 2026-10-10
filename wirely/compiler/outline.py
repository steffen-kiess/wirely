"""Parse-only outline of declarations, ports, and connections.

outline() only parses the given sources, so it also works for programs that
do not compile. References to declarations that are not part of the sources,
for example node types from libraries that were not passed or nodes declared
in other files, stay unresolved instead of causing errors.

Elements are identified by their enclosing namespaces/node types and name.
Identical connections in a scope are distinguished by Connection.occurrence,
their index in source order; it is None for a unique connection.
"""

import dataclasses
import functools
from dataclasses import dataclass, field

import lark

from . import ast
from . import attributes as _attributes
from . import parser
from .diagnostics import Diagnostic, error_diagnostics
from .node_type import NodeType as _NodeType


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
class Temporary:
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
    _declaration: object = field(default=None, compare=False, repr=False)


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
    _declaration: object = field(default=None, compare=False, repr=False)


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
    _declaration: object = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class TemporaryInfo:
    id: Temporary
    filename: str
    span: tuple[int, int]
    attributes: tuple
    position: tuple | None
    name: str
    type_text: str
    _declaration: object = field(default=None, compare=False, repr=False)


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
    _declaration: object = field(default=None, compare=False, repr=False)


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
    _declaration: object = field(default=None, compare=False, repr=False)


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
    _declaration: object = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Endpoint:
    """A connection end; element is None if the reference is unresolved."""
    text: str
    element: object
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
    attributes: tuple
    _declaration: object = field(default=None, compare=False, repr=False)
    _source: object = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Scope:
    kind: str
    path: tuple[str, ...]
    nodes: tuple[NodeInfo, ...]
    node_types: tuple['NodeTypeInfo', ...]
    namespaces: tuple['Scope', ...]
    states: tuple[StateInfo, ...]
    temporaries: tuple[TemporaryInfo, ...]
    constants: tuple[ConstantInfo, ...]
    parameters: tuple[ParameterInfo, ...]
    ports: tuple[PortInfo, ...]
    procedures: tuple[ProcedureInfo, ...]
    edges: tuple[Edge, ...]


@dataclass(frozen=True)
class NodeTypeInfo:
    id: NodeType
    filename: str
    span: tuple[int, int]
    attributes: tuple
    name: str
    is_native: bool
    is_singleton: bool
    scope: Scope
    _declaration: object = field(default=None, compare=False, repr=False)

    @property
    def ports(self):
        return self.scope.ports

    @property
    def parameters(self):
        return self.scope.parameters


@dataclass(frozen=True)
class SourceFile:
    filename: str
    # The parse error, if the file contributes no declarations.
    error: Diagnostic | None


@dataclass(frozen=True)
class Outline:
    files: tuple[SourceFile, ...]
    scope: Scope

    def find(self, target):
        """Return the records with the given ID, several for duplicate names."""
        if isinstance(target, Connection):
            edges = self._index.get(dataclasses.replace(target, occurrence=None), ())
            if target.occurrence is None:
                return tuple(edges)
            if 0 <= target.occurrence < len(edges):
                return (edges[target.occurrence],)
            return ()
        return tuple(self._index.get(target, ()))

    @functools.cached_property
    def _index(self):
        index = {}

        def add(key, record):
            index.setdefault(key, []).append(record)

        def visit(scope):
            for record in (*scope.nodes, *scope.states, *scope.temporaries, *scope.constants,
                           *scope.parameters, *scope.ports):
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


def outline(sources, *, add_std=False):
    """Outline (filename, text) pairs; files that fail to parse are skipped."""
    if add_std:
        from . import _standard_library_sources
        sources = _standard_library_sources() + list(sources)
    files = []
    parsed_files = []
    for filename, text in sources:
        try:
            parsed_files.append((filename, parser.parse_str(text, filename=filename)))
            error = None
        except lark.UnexpectedInput as exception:
            error = Diagnostic('syntax_error', str(exception), filename=filename,
                               line=exception.line, context=exception.get_context(text))
        except ast.ProgramError as exception:
            error = next(error_diagnostics(exception))
        except lark.exceptions.VisitError as exception:
            if not isinstance(exception.orig_exc, ast.ProgramError):
                raise exception.orig_exc
            error = next(error_diagnostics(exception.orig_exc))
        files.append(SourceFile(filename, error))
    return dataclasses.replace(outline_parsed(parsed_files), files=tuple(files))


def outline_parsed(parsed_files):
    """Outline (filename, declarations) pairs as returned by parser.parse_sources()."""
    root = _Builder('global', (), None)
    for _, declarations in parsed_files:
        _collect(root, declarations)
    files = tuple(SourceFile(filename, None) for filename, _ in parsed_files)
    return Outline(files, _build_scope(root))


class _Builder:
    def __init__(self, kind, path, parent):
        self.kind = kind
        self.path = path
        self.parent = parent
        self.items = []
        self.namespaces = {}
        self.node_types = []
        self.port_records = None
        self.name_table = None


def _collect(builder, declarations):
    for item in declarations:
        if isinstance(item, ast.DeclarationLibrary):
            name = item.name.id_str
            child = builder.namespaces.get(name)
            if child is None:
                child = builder.namespaces[name] = _Builder('namespace', builder.path + (name,), builder)
            _collect(child, item.declarations)
        elif isinstance(item, _NodeType):
            child = _Builder('node_type', builder.path + (item.name.id_str,), builder)
            builder.node_types.append((item, child))
            _collect(child, item.declarations)
        else:
            builder.items.append(item)


def _text(element):
    position = element.source_position
    return position.input_data[position.meta.start_pos:position.meta.end_pos]


def _span(element):
    meta = element.source_position.meta
    return (meta.start_pos, meta.end_pos)


def _filename(element):
    return element.source_position.filename


def _reference_text(reference):
    return '.'.join(identifier.id_str for identifier in reference.identifiers)


def _position(attributes):
    for attribute in attributes:
        if attribute.name == 'position':
            try:
                return _attributes._position(attribute)
            except ast.ProgramError:
                return None
    return None


def _direction(direction):
    return 'input' if direction == ast.PortDirection.Input else 'output'


def _port_kind(spec):
    if isinstance(spec, ast.PortTypeSpecificationResource):
        return 'resource'
    return 'value' if spec.kind_spec.kind == ast.ConnectionKind.Value else 'event'


def _ports(builder):
    if builder.port_records is None:
        builder.port_records = tuple(
            PortInfo(Port(builder.path + (item.name_str,), _direction(item.direction)),
                     _filename(item), _span(item), item.outer_attributes,
                     _position(item.outer_attributes), _direction(item.direction),
                     None if item.name is None else item.name_str, _port_kind(item.port_spec),
                     item.port_spec.list_spec.is_list, _text(item.port_spec.ty), item)
            for item in builder.items if isinstance(item, ast.DeclarationPort))
    return builder.port_records


def _names(builder):
    """Map each kind of name in a scope to the IDs of its declarations."""
    if builder.name_table is None:
        table = {key: {} for key in ('static', 'input', 'output', 'state', 'temporary', 'procedure', 'node')}
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
            elif isinstance(item, ast.DeclarationTemporary):
                table['temporary'][item.name_str] = Temporary(builder.path + (item.name_str,))
            elif isinstance(item, ast.DeclarationProcedure):
                table['procedure'][item.name.id_str] = Procedure(builder.path + (item.name.id_str,))
            elif isinstance(item, ast.DeclarationInstance):
                table['node'][item.name.id_str] = Node(builder.path + (item.name.id_str,))
        builder.name_table = table
    return builder.name_table


def _resolve_node_type(builder, reference):
    """Return the builder of the referenced node type, like Scope.resolve_node_type."""
    names = [identifier.id_str for identifier in reference.identifiers]
    scope = builder
    while scope is not None:
        if len(names) >= 2:
            namespace = scope.namespaces.get(names[0])
            for name in names[1:-1]:
                if namespace is None:
                    break
                namespace = namespace.namespaces.get(name)
            if namespace is not None:
                matches = [child for item, child in namespace.node_types if item.name.id_str == names[-1]]
                if matches:
                    return matches[0] if len(matches) == 1 else None
        else:
            matches = [child for item, child in scope.node_types if item.name.id_str == names[0]]
            if matches:
                return matches[0] if len(matches) == 1 else None
        scope = scope.parent
    return None


def _resolve_endpoint(builder, reference, is_target):
    """Resolve a connection end like ResolvedPortReference, within one scope."""
    text = _reference_text(reference)
    if reference.length > 2:
        return Endpoint(text, None, None)
    name = reference.identifiers[0].id_str
    port = reference.identifiers[1].id_str if reference.length == 2 else None
    names = _names(builder)
    if is_target:
        kinds = ('static', 'output', 'state', 'temporary', 'procedure', 'node')
    else:
        kinds = ('static', 'state', 'input', 'temporary', 'procedure', 'node')
    for kind in kinds:
        if kind in ('input', 'output') and port is not None:
            continue
        element = names[kind].get(name)
        if element is not None:
            return Endpoint(text, element, port)
    return Endpoint(text, None, port)


def _port_key(port):
    try:
        return port.key
    except ast.ProgramError:
        return None


def _procedure_port(procedure_id, port, direction):
    common = (ProcedurePort(procedure_id, _port_key(port), direction), _filename(port), _span(port),
              _port_key(port), direction)
    if isinstance(port, ast.TypedPort):
        spec = port.port_spec
        return ProcedurePortInfo(*common, None, _port_kind(spec), spec.list_spec.is_list, _text(spec.ty))
    return ProcedurePortInfo(*common, _text(port.reference), None, False, None)


def _record(builder, item):
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
    if isinstance(item, ast.DeclarationTemporary):
        return TemporaryInfo(Temporary(path + (item.name_str,)), *common, attributes,
                             _position(attributes), item.name_str, _text(item.var_type), item)
    if isinstance(item, ast.DeclarationConstant):
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
            tuple(_procedure_port(procedure_id, port, 'input') for port in item.depends.dependencies),
            tuple(_procedure_port(procedure_id, port, 'output') for port in item.produces.outputs),
            item)
    return None


def _procedure_endpoint(procedure_id, port):
    key = _port_key(port)
    name = procedure_id.path[-1]
    return Endpoint(name if key is None else f'{name}.{key}', procedure_id, key)


def _edges(builder, item):
    if isinstance(item, ast.DeclarationConnection):
        target = _resolve_endpoint(builder, item.target, True)
        for source in item.sources:
            connection = Connection(builder.path, _reference_text(source), target.text)
            yield Edge('connect', connection, _resolve_endpoint(builder, source, False), target,
                       _filename(item), _span(item), _span(source),
                       item.outer_attributes + source.outer_attributes, item, source)
    elif isinstance(item, ast.DeclarationProcedure):
        procedure_id = Procedure(builder.path + (item.name.id_str,))
        for port in item.depends.dependencies:
            if isinstance(port, ast.ReferencePort):
                endpoint = _procedure_endpoint(procedure_id, port)
                yield Edge('reference', None, _resolve_endpoint(builder, port.reference, False),
                           endpoint, _filename(port), _span(port), None, (), port)
        for port in item.produces.outputs:
            if isinstance(port, ast.ReferencePort):
                endpoint = _procedure_endpoint(procedure_id, port)
                yield Edge('reference', None, endpoint,
                           _resolve_endpoint(builder, port.reference, True),
                           _filename(port), _span(port), None, (), port)


def _with_occurrences(edges):
    counts = {}
    for edge in edges:
        if edge.id is not None:
            counts[edge.id] = counts.get(edge.id, 0) + 1
    seen = {}
    result = []
    for edge in edges:
        if edge.id is not None and counts[edge.id] > 1:
            occurrence = seen.get(edge.id, 0)
            seen[edge.id] = occurrence + 1
            edge = dataclasses.replace(edge, id=dataclasses.replace(edge.id, occurrence=occurrence))
        result.append(edge)
    return tuple(result)


def _build_scope(builder):
    records = [record for record in (_record(builder, item) for item in builder.items) if record is not None]

    def of_type(record_type):
        return tuple(record for record in records if isinstance(record, record_type))

    node_types = tuple(
        NodeTypeInfo(NodeType(child.path), _filename(item), _span(item),
                     item.outer_attributes + item.inner_attributes, item.name.id_str,
                     item.is_native, item.is_singleton, _build_scope(child), item)
        for item, child in builder.node_types)
    return Scope(
        builder.kind, builder.path, of_type(NodeInfo), node_types,
        tuple(_build_scope(child) for child in builder.namespaces.values()),
        of_type(StateInfo), of_type(TemporaryInfo), of_type(ConstantInfo), of_type(ParameterInfo),
        _ports(builder), of_type(ProcedureInfo),
        _with_occurrences([edge for item in builder.items for edge in _edges(builder, item)]))
