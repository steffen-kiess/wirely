from collections.abc import Mapping

from . import model as blocks
from .block import Block
from .node import StateRead
from .types import resolve_type


debug_variable_resolution = False


def connect(source, target, source_element):
    # TODO: Clean up
    if not isinstance(source, (Block.Output, blocks.BlockInput, blocks.BlockStateVariable, blocks.BlockTemporaryVariable, StateRead)):
        raise Exception('source parameter is not a data or resource source')
    # TODO: Clean up
    if not isinstance(target, Block.Input) and not isinstance(target, blocks.BlockOutput):
        raise Exception('target parameter is not a Block.Input and not a BlockOutput')

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
        return member.graph.variables[member.name]

    def incompatible(message):
        raise blocks.ProgramError(source_element, message, hints=[
            blocks.CompilerHint(declaration(source), 'Source was declared here'),
            blocks.CompilerHint(declaration(target), 'Target was declared here'),
        ])

    source_resource = isinstance(getattr(source, 'conn_spec', None), blocks.ConnectorSpecificationResource)
    target_resource = isinstance(target.conn_spec, blocks.ConnectorSpecificationResource)
    if source_resource or target_resource:
        if not (source_resource and target_resource):
            incompatible('Cannot connect resource and data connectors')
        if source.resource_type is not target.resource_type:
            incompatible(f'source has resource type {source.resource_type} but target has resource type {target.resource_type}')
        target.connect(source)
        return

    if isinstance(source, (blocks.BlockStateVariable, blocks.BlockTemporaryVariable, StateRead)):
        source_kind = blocks.ConnectionKind.Value
    else:
        source_kind = source.conn_spec.kind_spec.kind
    target_kind = target.conn_spec.kind_spec.kind
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
            member = next(member for member in members if type(member) is type(var) and member.name == var.name)
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
        # Normal output
        # TODO: Hack, should use var.temporary, but that is overwritten somewhere in some cases
        temporary = var.orig_temporary
        if temporary.block != path[-1]:
            if isinstance(path[-1], blocks.Block):  # TODO: Handle singletons here?
                raise Exception(f'temporary.block {temporary.block} != path[-1] {path[-1]}')
        if temporary.producer is None:
            raise Exception(f'Temporary for output {var} does not have a producer')
        if debug_variable_resolution:
            print(f'resolve_variable: returning {path}, {temporary}')
        return path, temporary

    if isinstance(var, blocks.Block.Temporary):
        temporary = var.temporary
        if temporary != var:
            raise Exception('temporary != var')
        if temporary.block != path[-1]:
            if isinstance(path[-1], blocks.Block):  # TODO: Handle singletons here?
                raise Exception(f'temporary.block {temporary.block} != path[-1] {path[-1]}')
        if temporary.producer is None:
            for output in temporary.block.block_outputs:
                if getattr(output, 'orig_temporary', None) is temporary and output.block_output.connected_source is not None:
                    return resolve_variable(path, output)
            raise Exception('temporary.producer is None')
        if debug_variable_resolution:
            print(f'resolve_variable: returning {path}, {temporary}')
        return path, temporary

    if isinstance(var, blocks.BlockTemporaryVariable):
        # TODO: Clean up
        if path[-1] != var:
            raise Exception('path[-1] != var')
        block = path[-2]
        var_inst = None
        for t in block.block_temporaries:
            if t.name == var.name:
                var_inst = t
                break
        if var_inst is None:
            raise Exception(f'Cannot find temporary {var.name!r}')
        temporary = var_inst.temporary
        if temporary != var_inst:
            raise Exception('temporary != var_inst')
        if temporary.block != path[-2]:
            if isinstance(path[-2], blocks.Block):  # TODO: Handle singletons here?
                raise Exception(f'temporary.block {temporary.block} != path[-1] {path[-1]}')
        if temporary.producer is None:
            raise Exception('temporary.producer is None')
        if debug_variable_resolution:
            print(f'resolve_variable: returning {path[:-1]}, {temporary}')
        return path[:-1], temporary

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
    def __init__(self, *, name, parameters, configuration):
        self.parameters: Mapping[str, object] = parameters
        self.name = name
        self.configuration = configuration

    def __repr__(self):
        return f'StaticBlockContext({self.__dict__!r})'


class StaticProcedureContext:
    def __init__(self, *, sources, targets, name, block_parameter_values, node_context=None):
        self._sources = sources
        self._targets = targets
        self._input_bindings = []
        self._resource_bindings = []
        self._result_types = {}
        self.name = name
        self.node_context = node_context
        self.block_parameter_values = block_parameter_values

    def __repr__(self):
        return f'StaticProcedureContext({self.__dict__!r})'
