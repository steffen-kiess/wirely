from collections.abc import Mapping

from . import model as blocks
from .block import Block
from .node import Connection, StateRead
from .types import resolve_type


debug_variable_resolution = False


SOURCE_TYPES = (Block.Output, blocks.BlockInput, blocks.BlockStateVariable, blocks.BlockTemporaryVariable,
                StateRead, blocks.ProcedureOutputPort)
TARGET_TYPES = (Block.Input, blocks.BlockOutput, blocks.ProcedureInputPort, blocks.BlockTemporaryVariable,
                blocks.BlockStateVariable)


def describe_target(target):
    if isinstance(target, Block.Input):
        connector = 'Default input' if target.name == '' else f'Input {target.name!r}'
        return f'{connector} of node {target.block.name!r}'
    if isinstance(target, blocks.BlockOutput):
        return 'Default output' if target.output_name == '' else f'Output {target.output_name!r}'
    if isinstance(target, (blocks.ProcedureInputPort, blocks.ProcedureOutputPort)):
        return target.description[0].upper() + target.description[1:]
    kind = 'State' if isinstance(target, blocks.BlockStateVariable) else 'Temporary'
    return f'{kind} {target.name!r}'


def connection_kind(member):
    spec = getattr(member, 'conn_spec', None)
    return blocks.ConnectionKind.Value if spec is None else spec.kind_spec.kind


def connect(source, target, source_element, *, attributes=()):
    # TODO: Clean up
    if not isinstance(source, SOURCE_TYPES):
        raise Exception('source parameter is not a data or resource source')
    # TODO: Clean up
    if not isinstance(target, TARGET_TYPES):
        raise Exception(f'target parameter is not a connection target: {target!r}')

    if source.block.graph != target.block.graph:
        # TODO: Can this happen from program code? If yes, make error message clearer?
        raise blocks.ProgramError(source_element, f'source has graph {source.block.graph} but target has graph {target.block.graph}')

    def declaration(member):
        if isinstance(member, Block.Input):
            return member.block_input.conn_spec
        if isinstance(member, Block.Output):
            return member.block_output.conn_spec
        if isinstance(member, (blocks.BlockInput, blocks.BlockOutput)):
            return member.conn_spec
        if isinstance(member, (blocks.ProcedureInputPort, blocks.ProcedureOutputPort)):
            return member.binding
        return member.graph.variables[member.name]

    def incompatible(message):
        raise blocks.ProgramError(source_element, message, hints=[
            blocks.CompilerHint(declaration(source), 'Source was declared here'),
            blocks.CompilerHint(declaration(target), 'Target was declared here'),
        ])

    def previous_connection_hints():
        previous = next((connection.source_element for connection in source.block.graph.connections
                         if connection.target is target), None)
        return [] if previous is None else [blocks.CompilerHint(previous, 'Previous connection was here')]

    # Single inputs, ports, outputs, and variables take one source.
    is_multi = getattr(target, 'is_list', False) and isinstance(target, (Block.Input, blocks.ProcedureInputPort))
    if is_multi and isinstance(target, blocks.ProcedureInputPort) and (
            target.connected_source is not None or (getattr(source, 'is_list', False) and target.sources)):
        # A forwarded multi input is the port's only source.
        raise blocks.ProgramError(source_element, f'{describe_target(target)} cannot combine a multi input with other sources',
                                  hints=previous_connection_hints())
    if not is_multi and target.connected_source is not None:
        message = f'{describe_target(target)} already has a source'
        if isinstance(target, Block.Input):
            message += '; only multi inputs accept several'
        raise blocks.ProgramError(source_element, message, hints=previous_connection_hints())
    if isinstance(target, blocks.BlockStateVariable):
        if not isinstance(source, blocks.ProcedureOutputPort):
            raise blocks.ProgramError(source_element, f'State {target.name!r} must be supplied by a procedure output')
        if source.state is not None:
            raise blocks.ProgramError(source_element, f'{describe_target(source)} already supplies state {source.state.name!r}',
                                      hints=previous_connection_hints())

    def record(kind):
        actual_target = target.members[-1] if isinstance(target, Block.Input) and target.is_list else target
        source.block.graph.connections.append(Connection(
            source, actual_target, attributes, source_element, kind,
        ))

    source_resource = isinstance(getattr(source, 'conn_spec', None), blocks.ConnectorSpecificationResource)
    target_resource = isinstance(getattr(target, 'conn_spec', None), blocks.ConnectorSpecificationResource)
    if source_resource or target_resource:
        if not (source_resource and target_resource):
            incompatible('Cannot connect resource and data ports')
        if source.resource_type is not target.resource_type:
            incompatible(f'source has resource type {source.resource_type} but target has resource type {target.resource_type}')
        target.connect(source)
        record('resource')
        return

    source_kind = connection_kind(source)
    target_kind = connection_kind(target)
    if source_kind != target_kind:
        incompatible(f'source has kind {source_kind} but target has kind {target_kind}')

    source_type = source.var_type
    if isinstance(source.block, blocks.Block):
        source_type = resolve_type(source_type, source.block.par_values)
    target_type = target.var_type
    if isinstance(target.block, blocks.Block):
        target_type = resolve_type(target_type, target.block.par_values)
    if source_type != target_type:
        incompatible(f'source has type {source_type} but target has type {target_type}')

    target.connect(source)
    if isinstance(target, blocks.BlockStateVariable):
        source.state = target
    record('state' if isinstance(source, (blocks.BlockStateVariable, StateRead)) else 'data')


def path_resolve_singleton(path):
    if isinstance(path[-1], blocks.Block) and path[-1].block_type.is_singleton:
        return (path[-1].block_type,)
    else:
        return path


def resolve_variable(path, var):
    if debug_variable_resolution:
        print(f'resolve_variable({path}, {var})')

    if isinstance(var, StateRead):
        owner_path, state = resolve_variable(path, var.state)
        return owner_path, StateRead(state, var.phase, var.reference)

    if isinstance(var.block, Block) and var.block.block_type.is_singleton:
        owner = var.block.block_type.singleton_instance
        if owner is not var.block:
            members = owner.block_inputs + owner.block_outputs + owner.block_temporaries + owner.block_statevars
            member = next(member for member in members if type(member) is type(var) and member.var_identifier == var.var_identifier)
            return resolve_variable((owner.block_type,), member)

    if isinstance(var, blocks.Block.StateVariable):
        return path, var

    if isinstance(path[-1], blocks.Block) and path[-1].block_type.is_singleton:
        raise Exception('isinstance(path[-1], blocks.Blocks) and path[-1].block_type.is_singleton')

    if isinstance(path[-1], (Block, blocks.block_type.BlockType)):
        current_block = path[-1]
        if not isinstance(current_block, Block):
            current_block = current_block.singleton_instance
        # TODO: Clean this up?
        # print(path, var)
        if current_block == var.block:
            block = var.block
        elif var.block.graph == current_block.block_type:
            # Dependency on the result of a child block
            # TODO: Make this more generic
            # TODO: Modify path here?
            path = path_resolve_singleton(path + (var.block,))
            block = var.block
        else:
            raise Exception(f'Cannot resolve {var} from {current_block}')
        # print(type(var), type(var.block))

        # TODO: This is somewhat of a hack
        if (isinstance(var, blocks.Block.Output) or isinstance(var, blocks.Block.Temporary)) and isinstance(block, blocks.BlockInput):
            input_name = block.input_name
            new_path = path[:-1]
            parent_block = new_path[-1]
            if not isinstance(parent_block, Block):
                parent_block = parent_block.singleton_instance
            input_var = parent_block.block_inputs_byname[input_name]
            return resolve_variable(path_resolve_singleton(new_path), input_var)

        # TODO: This is somewhat of a hack
        if (isinstance(var, blocks.Block.Input)) and isinstance(block, blocks.BlockOutput):
            output_name = block.output_name
            new_path = path[:-1]
            parent_block = new_path[-1]
            if not isinstance(parent_block, Block):
                parent_block = parent_block.singleton_instance
            output_var = parent_block.block_outputs_byname[output_name]
            return resolve_variable(path_resolve_singleton(new_path), output_var)

    if isinstance(var, blocks.Block.Input):
        src = var.connected_source
        if src is None:
            raise Exception('src is None')
        new_path = path[:-1] + (src.block,)
        # print(src, type(src), src.block, new_path)
        return resolve_variable(path_resolve_singleton(new_path), src)

    if isinstance(var, blocks.Block.Output):
        source = var.block_output.connected_source
        if source is not None:
            return resolve_variable(path_resolve_singleton(path + (source.block,)), source)
        if isinstance(var.conn_spec, blocks.ConnectorSpecificationResource):
            return path, var
        raise Exception(f'Output {var} is not connected')

    if isinstance(var, blocks.Block.Temporary):
        temporary = var.temporary
        if temporary != var:
            raise Exception('temporary != var')
        if temporary.block != path[-1]:
            if isinstance(path[-1], blocks.Block):  # TODO: Handle singletons here?
                raise Exception(f'temporary.block {temporary.block} != path[-1] {path[-1]}')
        # A declared temporary is another name for its source.
        if temporary.alias_of is not None:
            return resolve_variable(path, temporary.alias_of)
        if temporary.producer is None:
            raise Exception('temporary.producer is None')
        if debug_variable_resolution:
            print(f'resolve_variable: returning {path}, {temporary}')
        return path, temporary

    if isinstance(var, blocks.BlockTemporaryVariable):
        # TODO: Clean up
        if path[-1] != var:
            raise Exception('path[-1] != var')
        block = path[-2]
        if not isinstance(block, Block):
            block = block.singleton_instance
        var_inst = None
        for t in block.block_temporaries:
            if t.port is None and t.name == var.name:
                var_inst = t
                break
        if var_inst is None:
            raise Exception(f'Cannot find temporary {var.name!r}')
        return resolve_variable(path[:-1], var_inst)

    if isinstance(var, blocks.ProcedureOutputPort):
        if path[-1] != var:
            raise Exception('path[-1] != var')
        block = path[-2]
        if not isinstance(block, Block):
            block = block.singleton_instance
        storage = block.port_storage[var]
        if isinstance(storage, Block.StateVariable):
            # Other consumers of a port writing state read the new value.
            return path[:-1], StateRead(storage, 'new', var.binding)
        return resolve_variable(path[:-1], storage)

    # TODO: Clean up
    if isinstance(var, blocks.BlockInput):
        if path[-1] != var:
            raise Exception('path[-1] != var')
        input_name = var.input_name
        new_path = path[:-1]
        parent_block = new_path[-1]
        if not isinstance(parent_block, Block):
            parent_block = parent_block.singleton_instance
        input_var = parent_block.block_inputs_byname[input_name]
        return resolve_variable(path_resolve_singleton(new_path), input_var)

    # TODO: Clean up
    if isinstance(var, blocks.BlockStateVariable):
        if path[-1] != var:
            raise Exception('path[-1] != var')
        owner_path = path[:-1]
        owner = owner_path[-1]
        if not isinstance(owner, blocks.Block):
            owner = owner.singleton_instance
        state = next(state for state in owner.block_statevars if state.name == var.name)
        return owner_path, state

    print(f'resolve_variable: Unhandled {path}, {var} {type(var)} {type(var.block)}')

    if debug_variable_resolution:
        print(f'resolve_variable: returning {path}, {var.temporary}')
    # TODO: This code seems to be unused
    # TODO: Avoid using .temporary (should be handled here)
    return path, var.temporary


class StaticBlockContext:
    def __init__(self, *, name, parameters):
        self.parameters: Mapping[str, object] = parameters
        self.name = name

    def __repr__(self):
        return f'StaticBlockContext({self.__dict__!r})'


class StaticProcedureContext:
    def __init__(self, *, sources, targets, name, block_parameter_values, node_context=None):
        self._sources = sources
        self._targets = targets
        self._input_bindings = []
        self._resource_bindings = []
        self._output_types = {}
        self.name = name
        self.node_context = node_context
        self.block_parameter_values = block_parameter_values

    def __repr__(self):
        return f'StaticProcedureContext({self.__dict__!r})'
