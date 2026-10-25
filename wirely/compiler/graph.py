from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ast.procedures import Port

from wirely.compiler import model
from .types import resolve_type
from .stages import ResolutionState


@dataclass(frozen=True)
class StateRead:
    """A read of a declared or instantiated state's old or new value."""

    state: Any  # A DeclarationState, StateNode, or InstanceStateVariable.
    phase: str
    reference: object

    @property
    def node(self) -> Any:
        return self.state.node

    @property
    def graph(self) -> 'model.NodeType':
        return self.node.graph

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def var_type(self) -> 'model.types.Type | None':
        return self.state.var_type


@dataclass(frozen=True)
class StaticSource:
    """A parameter or constant used as a connection source."""

    parameter: 'model.NodeParameter'
    reference: object

    # TODO: Remove
    @property
    def node(self) -> 'StaticSource':
        return self

    @property
    def graph(self) -> 'model.NodeType':
        return self.parameter.node_type

    @property
    def name(self) -> str:
        return self.parameter.name.id_str

    @property
    def var_type(self) -> 'model.types.Type | None':
        return self.parameter.type_resolved


@dataclass(frozen=True)
class Connection:
    """A compiler edge, whose target is the actual slot of a list input."""

    source: object
    target: object
    attributes: tuple
    source_element: 'model.ast.SourceElement'
    kind: str


# A node or port appearing in a node type (or in the global scope)
class GraphElement:
    # TODO: Should 'name' be an attribute of GraphElement?
    def __init__(self, graph: 'model.NodeType', *, name: str) -> None:
        if not isinstance(graph, model.NodeType):
            raise Exception('graph is not a NodeType')

        self.graph = graph
        self.name = name
        self.outer_attributes = ()
        self.inner_attributes = ()
        self.attributes: tuple = ()


# TODO: Move TypeInputPort and TypeOutputPort somewhere else?

# TODO: Should the Node types be merge with the corresponding AST classes?

class TypeInputPort(GraphElement):
    def __init__(self, graph: 'model.NodeType', type: 'model.types.Type | None', *, name: str,
                 input_name: str, is_list: bool, port_spec: 'model.PortTypeSpecification',
                 resource_type: 'model.types.TypeResource | None' = None) -> None:
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

    def __repr__(self) -> str:
        return f'<Input "{self.input_name}">'


class TypeOutputPort(GraphElement):
    def __init__(self, graph: 'model.NodeType', type: 'model.types.Type | None', *, name: str,
                 output_name: str, port_spec: 'model.PortTypeSpecification',
                 resource_type: 'model.types.TypeResource | None' = None) -> None:
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

        self.connected_source: Any = None  # Heterogeneous connection sources.

        # TODO: Remove
        self.node = self

    def connect(self, source: Any) -> None:
        # TODO: Clean up class structure for value sources / targets
        if not isinstance(source, (model.InstanceOutputPort, model.TypeInputPort, model.StateNode, model.TemporaryNode, model.StateRead, model.ProcedureOutputPort,
                                   StaticSource, LocalResource)):
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

    def __repr__(self) -> str:
        return f'<Output "{self.output_name}">'


class TemporaryNode(GraphElement):
    def __init__(self, graph: 'model.NodeType', type: 'model.types.Type', *, name: str,
                 port_spec: 'model.PortTypeSpecification') -> None:
        super().__init__(graph, name=name)
        self.var_type = type
        self.port_spec = port_spec

        if name in self.graph.temporary_nodes:
            raise Exception(f'Duplicate temporary node name: {name!r}')
        self.graph.temporary_nodes[name] = self

        self.connected_source: Any = None  # Heterogeneous connection sources.

        # TODO: Remove
        self.node = self

    def connect(self, source: Any) -> None:
        if self.connected_source is not None:
            raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')
        self.connected_source = source

    def __repr__(self) -> str:
        return f'<TemporaryNode "{self.name}">'


class LocalResource(GraphElement):
    """A declared fixed value, connected from one source or written by a load procedure."""

    def __init__(self, graph: 'model.NodeType', declaration: 'model.DeclarationBinding',
                 resource_type: 'model.Type') -> None:
        super().__init__(graph, name=declaration.name.id_str)
        self.declaration = declaration
        self.port_spec = declaration.port_spec
        self.resource_type = resource_type if isinstance(resource_type, model.types.TypeResource) else None
        self.var_type = resource_type
        graph.local_resources[self.name] = self

        self.connected_source: Any = None  # Heterogeneous connection sources.

        # TODO: Remove
        self.node = self

    def connect(self, source: Any) -> None:
        if self.connected_source is not None:
            raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')
        self.connected_source = source

    def __repr__(self) -> str:
        return f'<LocalResource "{self.name}">'


class InstanceLocalResource:
    """A local fixed value of one node instance."""

    def __init__(self, node: 'model.InstanceNode', resource: LocalResource) -> None:
        self.node = node
        self.resource = resource
        self.var_identifier = resource.name
        # The instance source passed on by a connected resource; None if produced.
        self.alias_of: Any = None  # Resolved instance source.
        node.local_resources[resource.name] = self

    @property
    def port_spec(self) -> 'model.PortTypeSpecification':
        return self.resource.port_spec

    @property
    def resource_type(self) -> 'model.types.TypeResource | None':
        return self.resource.resource_type

    @property
    def var_type(self) -> 'model.Type':
        return self.resource.var_type

    def __repr__(self) -> str:
        return f'<InstanceLocalResource "{self.node.name}.{self.resource.name}">'


class StateNode(GraphElement):
    def __init__(self, graph: 'model.NodeType', type: 'model.types.Type', *, name: str,
                 initial_value: 'model.ast.Value') -> None:
        super().__init__(graph, name=name)
        self.var_type = type

        if name in self.graph.state_nodes:
            raise Exception(f'Duplicate state node name: {name!r}')
        self.graph.state_nodes[name] = self

        # The source of this state's new value.
        self.connected_source: Any = None  # Heterogeneous connection sources.

        # TODO: Remove
        self.node = self

    def connect(self, source: Any) -> None:
        if self.connected_source is not None:
            raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')
        self.connected_source = source

    @property
    def copy_source(self) -> Any:
        """The source copied into this state, or None if a procedure port stores it."""
        source = self.connected_source
        if isinstance(source, ProcedureOutputPort) and source.state is self:
            return None
        return source

    def __repr__(self) -> str:
        return f'<StateNode "{self.name}">'


@dataclass(frozen=True)
class PortSpecification:
    """The port type a procedure port presents to connections."""

    var_type: 'model.types.Type | None'
    port_spec: 'model.PortTypeSpecification | None'
    resource_type: 'model.types.TypeResource | None'
    is_list: bool


# Connection ends have different data and resource interfaces.
def port_specification(port: Any) -> PortSpecification:
    """Return the port specification of a connection source or target."""
    if isinstance(port, ProcedurePort):
        return port.specification
    if isinstance(port, TemporaryNode):
        return PortSpecification(port.var_type, port.port_spec, None, False)
    if isinstance(port, (StateNode, StateRead, StaticSource)):
        return PortSpecification(port.var_type, None, None, False)
    var_type = port.var_type
    if isinstance(port.node, model.InstanceNode) and var_type is not None:
        var_type = resolve_type(var_type, port.node.par_values)
    resource_type = var_type if isinstance(var_type, model.types.TypeResource) else None
    return PortSpecification(var_type, port.port_spec, resource_type,
                             getattr(port, 'is_list', False))


class ProcedurePort(GraphElement):
    """A port of a procedure in a node type.

    A typed port has its own specification; a reference port takes the
    specification of the port it references.
    """

    def __init__(self, graph: 'model.NodeType', procedure: 'model.TypeProcedure', declaration: 'Port', *,
                 specification: PortSpecification | None = None, counterpart: Callable[[], Any] | None = None) -> None:
        super().__init__(graph, name=declaration.key)
        self.procedure = procedure
        self.declaration = declaration
        self._specification = specification
        self._counterpart: Any = counterpart  # Callback before resolution, heterogeneous port afterwards.
        self.reference_state = (ResolutionState.COMPLETE if specification is not None
                                else ResolutionState.PENDING)

        # TODO: Remove
        self.node = self

    def resolve_reference(self) -> None:
        """Prepare a reference port's counterpart and inherited specification."""
        if self.reference_state is ResolutionState.COMPLETE:
            return
        if self.reference_state is ResolutionState.FAILED:
            raise RuntimeError('Cannot reuse a failed procedure port resolution')
        try:
            # A procedure port counterpart is typed, so its specification is known.
            counterpart = self._counterpart()
            specification = port_specification(counterpart)
            self._counterpart = counterpart
            self._specification = specification
        except Exception:
            self.reference_state = ResolutionState.FAILED
            raise
        self.reference_state = ResolutionState.COMPLETE

    @property
    def counterpart(self) -> Any:
        """The prepared referenced port, or None for a typed port."""
        if self.reference_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Procedure port references must be resolved before use')
        return self._counterpart

    @property
    def specification(self) -> PortSpecification:
        if self.reference_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Procedure port specifications must be resolved before use')
        assert self._specification is not None
        return self._specification

    @property
    def var_type(self) -> 'model.types.Type | None':
        return self.specification.var_type

    @property
    def port_spec(self) -> 'model.PortTypeSpecification | None':
        return self.specification.port_spec

    @property
    def resource_type(self) -> 'model.types.TypeResource | None':
        return self.specification.resource_type

    @property
    def is_list(self) -> bool:
        return self.specification.is_list

    @property
    def description(self) -> str:
        return f'port `{self.name}` of procedure `{self.procedure.name.id_str}`'

    def __repr__(self) -> str:
        return f'<{type(self).__name__} {self.procedure.name.id_str}.{self.name}>'


class ProcedureInputPort(ProcedurePort):
    def __init__(self, graph: 'model.NodeType', procedure: 'model.TypeProcedure', declaration: 'Port',
                 **kwargs: Any) -> None:
        super().__init__(graph, procedure, declaration, **kwargs)
        # A single source, or a multi input forwarded as a whole.
        self.connected_source: Any = None  # Heterogeneous connection sources.
        # The sources of a multi port.
        self.sources: list[Any] = []  # Heterogeneous connection sources.

    def connect(self, source: Any) -> None:
        if self.is_list and not getattr(source, 'is_list', False):
            if self.connected_source is not None:
                raise Exception(f'{self} already forwards {self.connected_source}')
            self.sources.append(source)
            return
        if self.connected_source is not None or self.sources:
            raise Exception(f'{self} is already connected, trying to connect to {source}')
        self.connected_source = source


class ProcedureOutputPort(ProcedurePort):
    def __init__(self, graph: 'model.NodeType', procedure: 'model.TypeProcedure', declaration: 'Port',
                 **kwargs: Any) -> None:
        super().__init__(graph, procedure, declaration, **kwargs)
        # The state node this port writes, if any.
        self.state: StateNode | None = None
