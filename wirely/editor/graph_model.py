"""The diagram of one scope as plain data for drawing.

Items are the nodes, states, temporaries, constants, parameters, ports, and
procedures of a scope, keyed by their outline IDs. Inputs are drawn on the
left of an item and outputs on the right; a node type body's own input
declarations are sources and its output declarations targets. Geometry is in
grid units as specified in doc/diagram-layout.md.
"""

import functools
from collections import defaultdict
from dataclasses import dataclass, replace

from wirely.compiler.editing import format_identifier
from wirely.compiler.outline import attribute_value

DEFAULT_WIDTH = 8
HEADER_HEIGHT = 2
# Requested row of a socket that fills the next free row on its side.
_AUTO = 'auto'


@dataclass(frozen=True)
class Unresolved:
    """An item standing for an unresolved connection end."""
    path: tuple[str, ...]


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
    # The row of the socket, or None for the header.
    row: int | None = None


@dataclass(frozen=True)
class Item:
    id: object
    # 'node', 'state', 'temporary', 'constant', 'parameter', 'input', 'output',
    # 'procedure', or 'unresolved'.
    kind: str
    title: str
    subtitle: str
    inputs: tuple[Socket, ...]
    outputs: tuple[Socket, ...]
    position: tuple[float, float] | None
    diagnostics: tuple
    # The node type a node instantiates, if declared in the sources.
    node_type: object = None
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


def port_rows(item):
    """Return the number of port rows, including skipped ones."""
    rows = [socket.row for socket in (*item.inputs, *item.outputs) if socket.row is not None]
    return max(rows) + 1 if rows else 0


def minimum_height(item):
    return HEADER_HEIGHT + port_rows(item) + len(item.details)


def item_height(item):
    return max(item.height or 0, minimum_height(item))


def default_size(item):
    """Return the size of an item without its own Diagram.Width and Diagram.Height."""
    return item.default_width, max(item.default_height or 0, minimum_height(item))


def row_y(row):
    """Return the y offset of the middle of a row within its item; row None is the header's type line."""
    return HEADER_HEIGHT + 0.5 + (-1 if row is None else row)


@dataclass(frozen=True)
class GraphEdge:
    # The Connection ID of a connect edge; None for a procedure reference port.
    id: object
    kind: str
    source: tuple
    target: tuple
    diagnostics: tuple

    @property
    def source_item(self):
        return self.source[0]

    @property
    def target_item(self):
        return self.target[0]


@dataclass(frozen=True)
class GraphModel:
    path: tuple[str, ...]
    kind: str
    items: tuple[Item, ...]
    edges: tuple[GraphEdge, ...]
    filenames: tuple[str, ...]

    def item(self, item_id):
        for item in self.items:
            if item.id == item_id:
                return item
        return None


def build(program, path):
    """Return the GraphModel of a scope, or None if it is missing or ambiguous."""
    scope = program.scope(path)
    if scope is None:
        return None
    analysis = program.analysis
    diagnostics = defaultdict(list)
    for entry in analysis.diagnostics:
        if entry.element is not None:
            diagnostics[entry.element].append(entry.diagnostic)

    builders = {}

    def add(kind, record, title, subtitle, inputs, outputs, node_type=None, defaults=()):
        builder = builders[record.id] = _ItemBuilder(
            record.id, kind, title, subtitle, list(inputs), list(outputs),
            None if record.position is None else tuple(map(float, record.position)),
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
    for record in scope.parameters:
        add('parameter', record, record.name, record.type_text,
            (), (Socket(None, '', _reference(record.name), 'value', record.type_text, False),))
    for record in scope.constants:
        add('constant', record, record.name, f'{record.type_text} = {record.value_text}',
            (), (Socket(None, '', _reference(record.name), 'value', record.type_text, False),))
    for record in scope.states:
        name = _reference(record.name)
        add('state', record, record.name, f'{record.type_text} = {record.initial_text}',
            (Socket(None, '', name, 'value', record.type_text, False),),
            (Socket('old', 'old', f'{name}.old', 'value', record.type_text, False, row=0),
             Socket('new', 'new', f'{name}.new', 'value', record.type_text, False, row=1)))
    for record in scope.temporaries:
        name = _reference(record.name)
        add('temporary', record, record.name, record.type_text,
            (Socket(None, '', name, 'value', record.type_text, False),),
            (Socket(None, '', name, 'value', record.type_text, False),))
    for record in scope.procedures:
        name = _reference(record.name)
        sockets = [[], []]
        for index, ports in enumerate((record.inputs, record.outputs)):
            for port in ports:
                if port.key is None:
                    continue
                sockets[index].append(Socket(port.key, port.key, f'{name}.{_reference(port.key)}',
                                             port.kind, port.type_text, port.is_list, row=_AUTO))
        stage = '' if record.stage == 'default' else f'stage {record.stage}'
        add('procedure', record, record.name, ' '.join(filter(None, ('native' if record.is_native else '',
                                                                     'procedure', stage))),
            *sockets)

    edges = []
    for edge in scope.edges:
        source = _end(builders, path, edge.source, 'outputs')
        target = _end(builders, path, edge.target, 'inputs')
        edges.append(GraphEdge(edge.id, edge.kind, source, target,
                               tuple(diagnostics[edge.id]) if edge.id is not None else ()))

    records = sorted((*scope.nodes, *scope.ports, *scope.parameters, *scope.constants,
                      *scope.states, *scope.temporaries, *scope.procedures),
                     key=lambda record: (_file_rank(scope.filenames, record.filename), record.span[0]))
    unresolved = [builder for builder in builders.values() if isinstance(builder.id, Unresolved)]
    for order, builder in enumerate([builders[record.id] for record in records] + unresolved):
        builder.order = order
    return GraphModel(path, scope.kind, tuple(builder.freeze() for builder in builders.values()),
                      tuple(edges), scope.filenames)


def layout_edges(model):
    """Return the (source, target) item pairs that order the layout.

    Reading state.old is feedback from the previous step and does not order
    the state before its reader.
    """
    kinds = {item.id: item.kind for item in model.items}
    return [(edge.source_item, edge.target_item) for edge in model.edges
            if not (kinds.get(edge.source_item) == 'state' and edge.source[1] == 'old')]


class _ItemBuilder:
    def __init__(self, item_id, kind, title, subtitle, inputs, outputs, position, diagnostics, node_type):
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
        self.details = ()
        self.width = self.default_width = DEFAULT_WIDTH
        self.height = self.default_height = None

    def socket(self, side, key, reference):
        """Return the socket for an endpoint port, adding an unresolved one if needed."""
        sockets = getattr(self, side)
        for socket in sockets:
            if socket.key == key:
                return socket
        socket = Socket(key, key or '', reference, None, None, False, resolved=False,
                        row=None if key is None else _AUTO)
        sockets.append(socket)
        return socket

    def freeze(self):
        return Item(self.id, self.kind, self.title, self.subtitle, _assign_rows(self.inputs),
                    _assign_rows(self.outputs), self.position, self.diagnostics, self.node_type, self.order,
                    self.details, self.width, self.height, self.default_width, self.default_height)


def _assign_rows(sockets):
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


def _end(builders, path, endpoint, side):
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


def _file_rank(filenames, filename):
    return filenames.index(filename) if filename in filenames else len(filenames)


def _details(node, node_type):
    values = {binding.name: binding.value_text for binding in node.parameters}
    details = []
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


def _node_sockets(analysis, node):
    """Return the sockets of a node, with rows from its node type's port declarations."""
    rows = {}
    for port in node.ports or ():
        row = attribute_value(port.attributes, 'Diagram.Row')
        rows[(port.direction, port.name)] = row if row is not None else None if port.name is None else _AUTO
    resolved = analysis.ports(node.id)
    if resolved is not None:
        ports = [(port.name, port.direction, port.kind, port.is_list,
                  None if port.type is None else str(port.type), True) for port in resolved]
    elif node.ports is not None:
        ports = [(port.name, port.direction, port.kind, port.is_list, port.type_text, False)
                 for port in node.ports]
    else:
        ports = []
    inputs = []
    outputs = []
    name = _reference(node.name)
    for port_name, direction, kind, is_list, type_text, resolved_port in ports:
        reference = name if port_name is None else f'{name}.{_reference(port_name)}'
        row = rows.get((direction, port_name), None if port_name is None else _AUTO)
        socket = Socket(port_name, port_name or '', reference, kind, type_text, is_list, resolved_port, row)
        (inputs if direction == 'input' else outputs).append(socket)
    return inputs, outputs


@functools.lru_cache(maxsize=4096)
def _reference(name):
    if name.startswith('default '):
        return name
    try:
        return format_identifier(name)
    except ValueError:
        return name
