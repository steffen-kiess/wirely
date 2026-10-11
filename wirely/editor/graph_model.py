"""The graph of one scope as plain data for drawing.

Items are the nodes, states, temporaries, constants, parameters, ports, and
procedures of a scope, keyed by their outline IDs. Inputs are drawn on the
left of an item and outputs on the right; a node type body's own input
declarations are sources and its output declarations targets.
"""

import functools
from collections import defaultdict
from dataclasses import dataclass

from wirely.compiler.editing import format_identifier


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
    # (name, value text) of bound parameters marked #[summary].
    details: tuple[tuple[str, str], ...] = ()


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

    def add(kind, record, title, subtitle, inputs, outputs, node_type=None):
        builders[record.id] = _ItemBuilder(record.id, kind, title, subtitle, list(inputs), list(outputs),
                                           None if record.position is None else tuple(map(float, record.position)),
                                           tuple(diagnostics[record.id]), node_type)

    for node in scope.nodes:
        inputs, outputs = _node_sockets(analysis, node)
        add('node', node, node.name, node.type_text, inputs, outputs, node.node_type)
        builders[node.id].details = _details(analysis, node)
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
            (Socket('old', 'old', f'{name}.old', 'value', record.type_text, False),
             Socket('new', 'new', f'{name}.new', 'value', record.type_text, False)))
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
                                             port.kind, port.type_text, port.is_list))
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

    def socket(self, side, key, reference):
        """Return the socket for an endpoint port, adding an unresolved one if needed."""
        sockets = getattr(self, side)
        for socket in sockets:
            if socket.key == key:
                return socket
        socket = Socket(key, key or '', reference, None, None, False, resolved=False)
        sockets.append(socket)
        return socket

    def freeze(self):
        return Item(self.id, self.kind, self.title, self.subtitle, tuple(self.inputs), tuple(self.outputs),
                    self.position, self.diagnostics, self.node_type, self.order, self.details)


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


def _details(analysis, node):
    values = {binding.name: binding.value_text for binding in node.parameters}
    return tuple((parameter.name, values[parameter.name]) for parameter in analysis.parameters(node.id) or ()
                 if parameter.summary and parameter.name in values)


def _node_sockets(analysis, node):
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
        socket = Socket(port_name, port_name or '', reference, kind, type_text, is_list, resolved_port)
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
