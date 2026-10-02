import typing
from dataclasses import dataclass

import wirely as blocks
from wirely.util import check_parameters


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


# A node appearing in a block type (or in the global scope)
class Node:
    # TODO: Should 'name' be a member of Node?
    @check_parameters
    def __init__(self, graph, *, name: str):
        if not isinstance(graph, blocks.block_type.BlockType):
            raise Exception('graph is not a BlockType')

        self.graph = graph
        self.name = name


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
        if input_name == 'implicit input':
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
        if output_name == 'implicit output':
            self.output_name = ''

        if name in self.graph.block_outputs:
            raise Exception(f'Duplicate block output name: {name!r}')
        self.graph.block_outputs[name] = self

        self.connected_source = None

        # TODO: Remove
        self.block = self

    def connect(self, source):
        # TODO: Clean up class structure for value sources / targets
        if not isinstance(source, (blocks.Block.Output, blocks.BlockInput, blocks.BlockStateVariable, blocks.StateRead)):
            raise Exception('source parameter value is not an Output, BlockInput or BlockStateVariable')
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

        # TODO: Remove
        self.block = self

    def __repr__(self):
        return f'<TemporaryVariable "{self.name}">'


class BlockStateVariable(Node):
    def __init__(self, graph, type, *, name, initial_value):
        super().__init__(graph, name=name)
        self.var_type = type

        if name in self.graph.block_state_variables:
            raise Exception(f'Duplicate block state variable name: {name!r}')
        self.graph.block_state_variables[name] = self

        # TODO: Remove
        self.block = self

    def __repr__(self):
        return f'<StateVariable "{self.name}">'
