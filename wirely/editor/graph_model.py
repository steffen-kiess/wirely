"""The diagram of one scope as plain data for drawing.

Items are the nodes, states, bindings, constants, parameters, ports, and
procedures of a scope, keyed by their outline IDs. Inputs are drawn on the
left of an item and outputs on the right; a node type body's own input
declarations are sources and its output declarations targets. Geometry is in
grid units as specified in doc/diagram-layout.md.
"""

from __future__ import annotations

import functools
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, TypeAlias

from wirely.compiler import outline as _outline
from wirely.compiler.analysis import Analysis
from wirely.compiler.attributes import Attribute
from wirely.compiler.diagnostics import Diagnostic
from wirely.compiler.editing import format_identifier
from wirely.compiler.outline import attribute_value

if TYPE_CHECKING:
    from .program import Program

DEFAULT_WIDTH = 8
HEADER_HEIGHT = 2
# Requested row of a socket that fills the next free row on its side.
_AUTO = 'auto'


@dataclass(frozen=True)
class Unresolved:
    """An item standing for an unresolved connection end."""
    path: tuple[str, ...]


ItemId: TypeAlias = _outline.DeclarationId | Unresolved
ItemRecord: TypeAlias = (_outline.NodeInfo | _outline.StateInfo | _outline.BindingInfo | _outline.ConstantInfo
                         | _outline.ParameterInfo | _outline.PortInfo | _outline.ProcedureInfo)


@dataclass(frozen=True)
class Socket:
    # The port of the element as in an outline endpoint: a port name, 'old' or
    # 'new', a procedure port key, or None for a default port.
    key: str | None
    label: str
    # The connection end as written in a connect statement.
    reference: str
    kind: str | None
    type_text: str | None
    is_list: bool
    resolved: bool = True
    # The row of the socket, None for the header, or _AUTO for automatic placement.
    row: int | str | None = None
    # A multi input whose connections have no meaningful order.
    unordered: bool = False


@dataclass(frozen=True)
class Item:
    id: ItemId
    # 'node', 'state', 'binding', 'constant', 'parameter', 'input',
    # 'output', 'procedure', or 'unresolved'.
    kind: str
    title: str
    subtitle: str
    inputs: tuple[Socket, ...]
    outputs: tuple[Socket, ...]
    position: tuple[float, float] | None
    diagnostics: tuple[Diagnostic, ...]
    # The node type a node instantiates, if declared in the sources.
    node_type: _outline.NodeType | None = None
    # Rank in source order; unresolved items come last.
    order: int = 0
    # Parameters marked Diagram.ShowValue as (name, value text, state), with
    # state 'bound', 'default', 'absent' (optional without default), or
    # 'missing'; the text is None unless bound or defaulted.
    details: tuple[tuple[str, str | None, str], ...] = ()
    width: float = DEFAULT_WIDTH
    # The requested height; the drawn height is at least minimum_height().
    height: float | None = None
    # The width and requested height without the item's own size attributes.
    default_width: float = DEFAULT_WIDTH
    default_height: float | None = None


def port_rows(item: Item) -> int:
    """Return the number of port rows, including skipped ones."""
    rows = []
    for socket in (*item.inputs, *item.outputs):
        if socket.row is not None:
            assert isinstance(socket.row, int)
            rows.append(socket.row)
    return max(rows) + 1 if rows else 0


def minimum_height(item: Item) -> int:
    return HEADER_HEIGHT + port_rows(item) + len(item.details)


def item_height(item: Item) -> float:
    return max(item.height or 0, minimum_height(item))


def default_size(item: Item) -> tuple[float, float]:
    """Return the size of an item without its own Diagram.Width and Diagram.Height."""
    return item.default_width, max(item.default_height or 0, minimum_height(item))


def row_y(row: int | None) -> float:
    """Return the y offset of the middle of a row within its item; row None is the header's type line."""
    return HEADER_HEIGHT + 0.5 + (-1 if row is None else row)


@dataclass(frozen=True)
class GraphEdge:
    # The Connection ID of a connect edge; None for a procedure reference port.
    id: _outline.Connection | None
    kind: str
    source: tuple[ItemId, str | None]
    target: tuple[ItemId, str | None]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def source_item(self) -> ItemId:
        return self.source[0]

    @property
    def target_item(self) -> ItemId:
        return self.target[0]


@dataclass(frozen=True)
class GraphModel:
    path: tuple[str, ...]
    kind: str
    items: tuple[Item, ...]
    edges: tuple[GraphEdge, ...]
    filenames: tuple[str, ...]

    def item(self, item_id: object) -> Item | None:
        for item in self.items:
            if item.id == item_id:
                return item
        return None


def build(program: Program, path: tuple[str, ...]) -> GraphModel | None:
    """Return the GraphModel of a scope, or None if it is missing or ambiguous."""
    scope = program.scope(path)
    if scope is None:
        return None
    analysis = program.analysis
    diagnostics: defaultdict[_outline.ElementId, list[Diagnostic]] = defaultdict(list)
    for entry in analysis.diagnostics:
        if entry.element is not None:
            diagnostics[entry.element].append(entry.diagnostic)

    builders: dict[object, _ItemBuilder] = {}

    def add(kind: str, record: ItemRecord, title: str, subtitle: str, inputs: Iterable[Socket],
            outputs: Iterable[Socket], node_type: _outline.NodeType | None = None,
            defaults: Sequence[Attribute] = ()) -> _ItemBuilder:
        builder = builders[record.id] = _ItemBuilder(
            record.id, kind, title, subtitle, list(inputs), list(outputs),
            None if record.position is None else (float(record.position[0]), float(record.position[1])),
            tuple(diagnostics[record.id]), node_type)
        width = attribute_value(record.attributes, 'Diagram.Width')
        height = attribute_value(record.attributes, 'Diagram.Height')
        builder.default_width = attribute_value(defaults, 'Diagram.DefaultWidth') or DEFAULT_WIDTH
        builder.default_height = attribute_value(defaults, 'Diagram.DefaultHeight')
        builder.width = width or builder.default_width
        builder.height = height or builder.default_height
        return builder

    for node in scope.nodes:
        node_types = program.outline.find(node.node_type) if node.node_type is not None else []
        node_type = node_types[0] if len(node_types) == 1 else None
        inputs, outputs = _node_sockets(analysis, node)
        builder = add('node', node, node.name, node.type_text, inputs, outputs, node.node_type,
                      node_type.attributes if node_type is not None else ())
        if node_type is not None:
            builder.details = _details(node, node_type)
    for port in scope.ports:
        name = port.name or f'default {port.direction}'
        socket = Socket(None, '', _reference(name), port.kind, port.type_text, port.is_list)
        subtitle = f'{port.direction} {"multi " if port.is_list else ""}{port.kind} {port.type_text}'
        if port.direction == 'input':
            add('input', port, name, subtitle, (), (socket,))
        else:
            add('output', port, name, subtitle, (socket,), ())
    for parameter in scope.parameters:
        add('parameter', parameter, parameter.name, parameter.type_text,
            (), (Socket(None, '', _reference(parameter.name), 'value', parameter.type_text, False),))
    for constant in scope.constants:
        add('constant', constant, constant.name, f'{constant.type_text} = {constant.value_text}',
            (), (Socket(None, '', _reference(constant.name), 'value', constant.type_text, False),))
    for state in scope.states:
        name = _reference(state.name)
        add('state', state, state.name, f'{state.type_text} = {state.initial_text}',
            (Socket(None, '', name, 'value', state.type_text, False),),
            (Socket('old', 'old', f'{name}.old', 'value', state.type_text, False, row=0),
             Socket('new', 'new', f'{name}.new', 'value', state.type_text, False, row=1)))
    for binding in scope.bindings:
        name = _reference(binding.name)
        add('binding', binding, binding.name, f'{binding.kind} {binding.type_text}',
            (Socket(None, '', name, binding.kind, binding.type_text, False),),
            (Socket(None, '', name, binding.kind, binding.type_text, False),))
    for procedure in scope.procedures:
        name = _reference(procedure.name)
        sockets: list[list[Socket]] = [[], []]
        for index, ports in enumerate((procedure.inputs, procedure.outputs)):
            for procedure_port in ports:
                if procedure_port.key is None:
                    continue
                sockets[index].append(Socket(procedure_port.key, procedure_port.key,
                                             f'{name}.{_reference(procedure_port.key)}',
                                             procedure_port.kind, procedure_port.type_text, procedure_port.is_list,
                                             row=_AUTO))
        stage = '' if procedure.stage == 'default' else f'stage {procedure.stage}'
        subtitle = ' '.join(filter(None, ('native' if procedure.is_native else '', 'procedure', stage)))
        add('procedure', procedure, procedure.name, subtitle,
            sockets[0], sockets[1])

    edges = []
    for edge in scope.edges:
        source = _end(builders, path, edge.source, 'outputs')
        target = _end(builders, path, edge.target, 'inputs')
        edges.append(GraphEdge(edge.id, edge.kind, source, target,
                               tuple(diagnostics[edge.id]) if edge.id is not None else ()))

    scope_records: tuple[ItemRecord, ...] = (
        *scope.nodes, *scope.ports, *scope.parameters, *scope.constants,
        *scope.states, *scope.bindings, *scope.procedures)
    records = sorted(scope_records,
                     key=lambda record: (_file_rank(scope.filenames, record.filename), record.span[0]))
    unresolved = [builder for builder in builders.values() if isinstance(builder.id, Unresolved)]
    for order, builder in enumerate([builders[record.id] for record in records] + unresolved):
        builder.order = order
    return GraphModel(path, scope.kind, tuple(builder.freeze() for builder in builders.values()),
                      tuple(edges), scope.filenames)


def layout_edges(model: GraphModel) -> list[tuple[ItemId, ItemId]]:
    """Return the (source, target) item pairs that order the layout.

    Reading state.old is feedback from the previous step and does not order
    the state before its reader.
    """
    kinds = {item.id: item.kind for item in model.items}
    return [(edge.source_item, edge.target_item) for edge in model.edges
            if not (kinds.get(edge.source_item) == 'state' and edge.source[1] == 'old')]


class _ItemBuilder:
    def __init__(self, item_id: ItemId, kind: str, title: str, subtitle: str, inputs: list[Socket],
                 outputs: list[Socket], position: tuple[float, float] | None, diagnostics: tuple[Diagnostic, ...],
                 node_type: _outline.NodeType | None) -> None:
        self.id = item_id
        self.kind = kind
        self.title = title
        self.subtitle = subtitle
        self.inputs = inputs
        self.outputs = outputs
        self.position = position
        self.diagnostics = diagnostics
        self.node_type = node_type
        self.order = 0
        self.details: tuple[tuple[str, str | None, str], ...] = ()
        self.width: float = DEFAULT_WIDTH
        self.default_width: float = DEFAULT_WIDTH
        self.height: float | None = None
        self.default_height: float | None = None

    def socket(self, side: str, key: str | None, reference: str) -> Socket:
        """Return the socket for an endpoint port, adding an unresolved one if needed."""
        sockets = self.inputs if side == 'inputs' else self.outputs
        for socket in sockets:
            if socket.key == key:
                return socket
        socket = Socket(key, key or '', reference, None, None, False, resolved=False,
                        row=None if key is None else _AUTO)
        sockets.append(socket)
        return socket

    def freeze(self) -> Item:
        return Item(self.id, self.kind, self.title, self.subtitle, _assign_rows(self.inputs),
                    _assign_rows(self.outputs), self.position, self.diagnostics, self.node_type, self.order,
                    self.details, self.width, self.height, self.default_width, self.default_height)


def _assign_rows(sockets: Sequence[Socket]) -> tuple[Socket, ...]:
    """Give each socket without a row the lowest free row on its side, in order.

    A row requested twice keeps its first socket; later ones are treated as
    having no row.
    """
    used = set()
    requested = []
    for socket in sockets:
        row = socket.row
        if isinstance(row, int) and row not in used:
            used.add(row)
        elif row is not None:
            row = _AUTO
        requested.append(row)
    result = []
    free = 0
    for socket, row in zip(sockets, requested):
        if row == _AUTO:
            while free in used:
                free += 1
            used.add(free)
            row = free
        result.append(replace(socket, row=row))
    return tuple(result)


def _end(builders: dict[object, _ItemBuilder], path: tuple[str, ...], endpoint: _outline.Endpoint,
         side: str) -> tuple[ItemId, str | None]:
    """Return (item ID, socket key) of a connection end."""
    builder = builders.get(endpoint.element)
    if builder is None:
        root = endpoint.text.split('.')[0]
        item_id = Unresolved(path + (root,))
        builder = builders.get(item_id)
        if builder is None:
            builder = builders[item_id] = _ItemBuilder(item_id, 'unresolved', root, 'unresolved',
                                                       [], [], None, (), None)
    key = endpoint.port
    if builder.kind == 'state' and side == 'inputs' and key == 'new':
        # Writing state.new sets the state's input.
        key = None
    socket = builder.socket(side, key, endpoint.text)
    return builder.id, socket.key


def _file_rank(filenames: Sequence[str], filename: str) -> int:
    return filenames.index(filename) if filename in filenames else len(filenames)


def _details(node: _outline.NodeInfo, node_type: _outline.NodeTypeInfo) -> tuple[tuple[str, str | None, str], ...]:
    values = {binding.name: binding.value_text for binding in node.parameters}
    details: list[tuple[str, str | None, str]] = []
    for parameter in node_type.parameters:
        if attribute_value(parameter.attributes, 'Diagram.ShowValue') is None:
            continue
        if parameter.name in values:
            details.append((parameter.name, values[parameter.name], 'bound'))
        elif parameter.default_text is not None:
            details.append((parameter.name, parameter.default_text, 'default'))
        else:
            details.append((parameter.name, None, 'absent' if parameter.is_optional else 'missing'))
    return tuple(details)


def _node_sockets(analysis: Analysis, node: _outline.NodeInfo) -> tuple[list[Socket], list[Socket]]:
    """Return the sockets of a node, with rows from its node type's port declarations."""
    rows = {}
    unordered = set()
    for port in node.ports or ():
        row = attribute_value(port.attributes, 'Diagram.Row')
        rows[(port.direction, port.name)] = row if row is not None else None if port.name is None else _AUTO
        if attribute_value(port.attributes, 'Unordered'):
            unordered.add((port.direction, port.name))
    resolved = analysis.ports(node.id)
    if resolved is not None:
        ports = [(port.name, port.direction, port.kind, port.is_list,
                  None if port.type is None else str(port.type), True) for port in resolved]
    elif node.ports is not None:
        ports = [(port.name, port.direction, port.kind, port.is_list, port.type_text, False)
                 for port in node.ports]
    else:
        ports = []
    inputs: list[Socket] = []
    outputs: list[Socket] = []
    name = _reference(node.name)
    for port_name, direction, kind, is_list, type_text, resolved_port in ports:
        reference = name if port_name is None else f'{name}.{_reference(port_name)}'
        row = rows.get((direction, port_name), None if port_name is None else _AUTO)
        socket = Socket(port_name, port_name or '', reference, kind, type_text, is_list, resolved_port, row,
                        (direction, port_name) in unordered)
        (inputs if direction == 'input' else outputs).append(socket)
    return inputs, outputs


@functools.lru_cache(maxsize=4096)
def _reference(name: str) -> str:
    if name.startswith('default '):
        return name
    try:
        return format_identifier(name)
    except ValueError:
        return name
