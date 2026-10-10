import typing
from dataclasses import dataclass

from wirely.compiler import model as blocks
from wirely.util import check_parameters
from .types import resolve_type


@dataclass(frozen=True)
class StateRead:
    """A read of a declared or instantiated state's old or new value."""

    state: object
    phase: str
    reference: object

    @property
    def block(self):
        return self.state.block

    @property
    def graph(self):
        return self.block.graph

    @property
    def name(self):
        return self.state.name

    @property
    def var_type(self):
        return self.state.var_type


@dataclass(frozen=True)
class Connection:
    """A compiler edge, whose target is the actual slot of a list input."""

    source: object
    target: object
    attributes: tuple
    source_element: object
    kind: str


# A node appearing in a block type (or in the global scope)
class Node:
    # TODO: Should 'name' be a member of Node?
    @check_parameters
    def __init__(self, graph, *, name: str):
        if not isinstance(graph, blocks.block_type.BlockType):
            raise Exception('graph is not a BlockType')

        self.graph = graph
        self.name = name
        self.outer_attributes = ()
        self.inner_attributes = ()
        self.attributes = ()


# A single instance of a node. A node might have multiple instances if the block
# type containing it is instantiated multiple times.
# TODO: Is this used for something except BlockInstance?
class NodeInstance:
    @check_parameters
    def __init__(self, node: typing.Optional[Node], parent_instance):
        self.node = node

        if parent_instance is not None and not isinstance(parent_instance, blocks.BlockInstance):
            raise Exception('parent_instance is not None and not isinstance(parent_instance, blocks.BlockInstance)')
        self.parent_instance = parent_instance


# TODO: Move BlockInput and BlockOutput somewhere else?

# TODO: Should the Node types be merge with the corresponding AST classes?

class BlockInput(Node):
    def __init__(self, graph, type, *, name, input_name, is_list, conn_spec, resource_type=None):
        super().__init__(graph, name=name)
        # TODO: Clean up
        self.var_type = type
        self.input_name = input_name
        self.conn_spec = conn_spec
        self.resource_type = resource_type
        self.is_list = is_list
        if input_name == 'default input':
            self.input_name = ''

        if name in self.graph.block_inputs:
            raise Exception(f'Duplicate block input name: {name!r}')
        self.graph.block_inputs[name] = self

        # TODO: Remove
        self.block = self

    def __repr__(self):
        return f'<Input "{self.input_name}">'


class BlockOutput(Node):
    def __init__(self, graph, type, *, name, output_name, conn_spec, resource_type=None):
        super().__init__(graph, name=name)
        self.var_type = type
        self.output_name = output_name
        self.conn_spec = conn_spec
        self.resource_type = resource_type
        if output_name == 'default output':
            self.output_name = ''

        if name in self.graph.block_outputs:
            raise Exception(f'Duplicate block output name: {name!r}')
        self.graph.block_outputs[name] = self

        self.connected_source = None

        # TODO: Remove
        self.block = self

    def connect(self, source):
        # TODO: Clean up class structure for value sources / targets
        if not isinstance(source, (blocks.Block.Output, blocks.BlockInput, blocks.BlockStateVariable, blocks.BlockTemporaryVariable, blocks.StateRead, blocks.ProcedureOutputPort)):
            raise Exception('source parameter value is not a data or resource source')
        # if self.is_list:
        if False:
            # TODO: Clean up
            input = Block.Input(self.block, f'{self.name}.{len(self.members)}', False, self.var_type, block_input=self)
            input.connect(source)
            self.members.append(input)
        else:
            if self.connected_source is not None:
                raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')

            self.connected_source = source

    def __repr__(self):
        return f'<Output "{self.output_name}">'


class BlockTemporaryVariable(Node):
    def __init__(self, graph, type, *, name):
        super().__init__(graph, name=name)
        self.var_type = type

        if name in self.graph.block_temporary_variables:
            raise Exception(f'Duplicate block temporary variable name: {name!r}')
        self.graph.block_temporary_variables[name] = self

        self.connected_source = None

        # TODO: Remove
        self.block = self

    def connect(self, source):
        if self.connected_source is not None:
            raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')
        self.connected_source = source

    def __repr__(self):
        return f'<TemporaryVariable "{self.name}">'


class BlockStateVariable(Node):
    def __init__(self, graph, type, *, name, initial_value):
        super().__init__(graph, name=name)
        self.var_type = type

        if name in self.graph.block_state_variables:
            raise Exception(f'Duplicate block state variable name: {name!r}')
        self.graph.block_state_variables[name] = self

        # The procedure output port writing this state.
        self.connected_source = None

        # TODO: Remove
        self.block = self

    def connect(self, source):
        if self.connected_source is not None:
            raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {source}')
        self.connected_source = source

    def __repr__(self):
        return f'<StateVariable "{self.name}">'


@dataclass(frozen=True)
class PortSpecification:
    """The connector a procedure port presents to connections."""

    var_type: object
    conn_spec: object
    resource_type: object
    is_list: bool


def endpoint_specification(member):
    """Return the connector specification of a connection endpoint."""
    if isinstance(member, ProcedurePort):
        return member.specification
    if isinstance(member, (BlockTemporaryVariable, BlockStateVariable, StateRead)):
        return PortSpecification(member.var_type, None, None, False)
    var_type = member.var_type
    if isinstance(member.block, blocks.Block) and var_type is not None:
        var_type = resolve_type(var_type, member.block.par_values)
    return PortSpecification(var_type, member.conn_spec, member.resource_type,
                             getattr(member, 'is_list', False))


class ProcedurePort(Node):
    """A port of a procedure in a node type.

    A typed port has its own specification; a bound port takes the
    specification of the endpoint its binding references.
    """

    def __init__(self, graph, procedure, binding, *, specification=None, counterpart=None):
        super().__init__(graph, name=binding.key)
        self.procedure = procedure
        self.binding = binding
        self._specification = specification
        self._counterpart = counterpart

        # TODO: Remove
        self.block = self

    @property
    def counterpart(self):
        """The endpoint referenced by a bound port, or None for a typed port."""
        if callable(self._counterpart):
            self._counterpart = self._counterpart()
        return self._counterpart

    @property
    def specification(self):
        if self._specification is None:
            self._specification = endpoint_specification(self.counterpart)
        return self._specification

    @property
    def var_type(self):
        return self.specification.var_type

    @property
    def conn_spec(self):
        return self.specification.conn_spec

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
    def __init__(self, graph, procedure, binding, **kwargs):
        super().__init__(graph, procedure, binding, **kwargs)
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
    def __init__(self, graph, procedure, binding, **kwargs):
        super().__init__(graph, procedure, binding, **kwargs)
        # The state variable this port writes, if any.
        self.state = None
