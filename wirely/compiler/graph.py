from dataclasses import dataclass

from wirely.compiler import model
from wirely.util import check_parameters
from .types import resolve_type
from .stages import ResolutionState


@dataclass(frozen=True)
class StateRead:
    """A read of a declared or instantiated state's old or new value."""

    state: object
    phase: str
    reference: object

    @property
    def node(self):
        return self.state.node

    @property
    def graph(self):
        return self.node.graph

    @property
    def name(self):
        return self.state.name

    @property
    def var_type(self):
        return self.state.var_type


@dataclass(frozen=True)
class StaticSource:
    """A parameter or constant used as a connection source."""

    declaration: object
    reference: object

    # TODO: Remove
    @property
    def node(self):
        return self

    @property
    def graph(self):
        return self.declaration.interface

    @property
    def name(self):
        return self.declaration.name.id_str

    @property
    def var_type(self):
        return self.declaration.type_resolved


@dataclass(frozen=True)
class Connection:
    """A compiler edge, whose target is the actual slot of a list input."""

    source: object
    target: object
    attributes: tuple
    source_element: object
    kind: str


# A node or port appearing in a node type (or in the global scope)
class GraphElement:
    # TODO: Should 'name' be an attribute of GraphElement?
    @check_parameters
    def __init__(self, graph, *, name: str):
        if not isinstance(graph, model.NodeType):
            raise Exception('graph is not a NodeType')

        self.graph = graph
        self.name = name
        self.outer_attributes = ()
        self.inner_attributes = ()
        self.attributes = ()


# TODO: Move TypeInputPort and TypeOutputPort somewhere else?

# TODO: Should the Node types be merge with the corresponding AST classes?

class TypeInputPort(GraphElement):
    def __init__(self, graph, type, *, name, input_name, is_list, port_spec, resource_type=None):
        super().__init__(graph, name=name)
        # TODO: Clean up
        self.var_type = type
        self.input_name = input_name
        self.port_spec = port_spec
        self.resource_type = resource_type
        self.is_list = is_list
        if input_name == 'default input':
            self.input_name = ''

        if name in self.graph.input_ports:
            raise Exception(f'Duplicate node input name: {name!r}')
        self.graph.input_ports[name] = self

        # TODO: Remove
        self.node = self

    def __repr__(self):
        return f'<Input "{self.input_name}">'


class TypeOutputPort(GraphElement):
    def __init__(self, graph, type, *, name, output_name, port_spec, resource_type=None):
        super().__init__(graph, name=name)
        self.var_type = type
        self.output_name = output_name
        self.port_spec = port_spec
        self.resource_type = resource_type
        if output_name == 'default output':
            self.output_name = ''

        if name in self.graph.output_ports:
            raise Exception(f'Duplicate node output name: {name!r}')
        self.graph.output_ports[name] = self

        self.connected_source = None

        # TODO: Remove
        self.node = self

    def connect(self, source):
        # TODO: Clean up class structure for value sources / targets
        if not isinstance(source, (model.InstanceOutputPort, model.TypeInputPort, model.StateNode, model.TemporaryNode, model.StateRead, model.ProcedureOutputPort,
                                   StaticSource)):
            raise Exception('source parameter value is not a data or resource source')
        # if self.is_list:
        if False:
            # TODO: Clean up
            input = model.InstanceInputPort(self.node, f'{self.name}.{len(self.members)}', False, self.var_type, type_input_port=self)
            input.connect(source)
            self.members.append(input)
        else:
            if self.connected_source is not None:
                raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')

            self.connected_source = source

    def __repr__(self):
        return f'<Output "{self.output_name}">'


class TemporaryNode(GraphElement):
    def __init__(self, graph, type, *, name):
        super().__init__(graph, name=name)
        self.var_type = type

        if name in self.graph.temporary_nodes:
            raise Exception(f'Duplicate temporary node name: {name!r}')
        self.graph.temporary_nodes[name] = self

        self.connected_source = None

        # TODO: Remove
        self.node = self

    def connect(self, source):
        if self.connected_source is not None:
            raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')
        self.connected_source = source

    def __repr__(self):
        return f'<TemporaryNode "{self.name}">'


class StateNode(GraphElement):
    def __init__(self, graph, type, *, name, initial_value):
        super().__init__(graph, name=name)
        self.var_type = type

        if name in self.graph.state_nodes:
            raise Exception(f'Duplicate state node name: {name!r}')
        self.graph.state_nodes[name] = self

        # The source of this state's new value.
        self.connected_source = None

        # TODO: Remove
        self.node = self

    def connect(self, source):
        if self.connected_source is not None:
            raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')
        self.connected_source = source

    @property
    def copy_source(self):
        """The source copied into this state, or None if a procedure port stores it."""
        source = self.connected_source
        if isinstance(source, ProcedureOutputPort) and source.state is self:
            return None
        return source

    def __repr__(self):
        return f'<StateNode "{self.name}">'


@dataclass(frozen=True)
class PortSpecification:
    """The port type a procedure port presents to connections."""

    var_type: object
    port_spec: object
    resource_type: object
    is_list: bool


def port_specification(port):
    """Return the port specification of a connection source or target."""
    if isinstance(port, ProcedurePort):
        return port.specification
    if isinstance(port, (TemporaryNode, StateNode, StateRead, StaticSource)):
        return PortSpecification(port.var_type, None, None, False)
    var_type = port.var_type
    if isinstance(port.node, model.InstanceNode) and var_type is not None:
        var_type = resolve_type(var_type, port.node.par_values)
    return PortSpecification(var_type, port.port_spec, port.resource_type,
                             getattr(port, 'is_list', False))


class ProcedurePort(GraphElement):
    """A port of a procedure in a node type.

    A typed port has its own specification; a reference port takes the
    specification of the port it references.
    """

    def __init__(self, graph, procedure, declaration, *, specification=None, counterpart=None):
        super().__init__(graph, name=declaration.key)
        self.procedure = procedure
        self.declaration = declaration
        self._specification = specification
        self._counterpart = counterpart
        self.reference_state = (ResolutionState.COMPLETE if specification is not None
                                else ResolutionState.PENDING)

        # TODO: Remove
        self.node = self

    def resolve_reference(self):
        """Prepare a reference port's counterpart and inherited specification."""
        if self.reference_state is ResolutionState.COMPLETE:
            return
        if self.reference_state is ResolutionState.RESOLVING:
            raise model.ProgramError(self.declaration, 'Circular procedure port reference')
        if self.reference_state is ResolutionState.FAILED:
            raise RuntimeError('Cannot reuse a failed procedure port resolution')
        self.reference_state = ResolutionState.RESOLVING
        try:
            counterpart = self._counterpart()
            if isinstance(counterpart, ProcedurePort):
                counterpart.resolve_reference()
            specification = port_specification(counterpart)
            self._counterpart = counterpart
            self._specification = specification
        except Exception:
            self.reference_state = ResolutionState.FAILED
            raise
        self.reference_state = ResolutionState.COMPLETE

    @property
    def counterpart(self):
        """The prepared referenced port, or None for a typed port."""
        if self.reference_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Procedure port references must be resolved before use')
        return self._counterpart

    @property
    def specification(self):
        if self.reference_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Procedure port specifications must be resolved before use')
        return self._specification

    @property
    def var_type(self):
        return self.specification.var_type

    @property
    def port_spec(self):
        return self.specification.port_spec

    @property
    def resource_type(self):
        return self.specification.resource_type

    @property
    def is_list(self):
        return self.specification.is_list

    @property
    def description(self):
        return f'port {self.name!r} of procedure {self.procedure.name.id_str!r}'

    def __repr__(self):
        return f'<{type(self).__name__} {self.procedure.name.id_str}.{self.name}>'


class ProcedureInputPort(ProcedurePort):
    def __init__(self, graph, procedure, declaration, **kwargs):
        super().__init__(graph, procedure, declaration, **kwargs)
        # A single source, or a multi input forwarded as a whole.
        self.connected_source = None
        # The sources of a multi port.
        self.sources = []

    def connect(self, source):
        if self.is_list and not getattr(source, 'is_list', False):
            if self.connected_source is not None:
                raise Exception(f'{self} already forwards {self.connected_source}')
            self.sources.append(source)
            return
        if self.connected_source is not None or self.sources:
            raise Exception(f'{self} is already connected, trying to connect to {source}')
        self.connected_source = source


class ProcedureOutputPort(ProcedurePort):
    def __init__(self, graph, procedure, declaration, **kwargs):
        super().__init__(graph, procedure, declaration, **kwargs)
        # The state node this port writes, if any.
        self.state = None
