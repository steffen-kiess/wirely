import threading
import abc
import contextlib
import datetime
import wirely as blocks

from .time import MonotonicTime, Duration
from . import enum, types
from .types import Type, TypeParameter
from .value import Value, ConstantValue, ValueParameter

from .util import check_parameters

# TODO: Rename graph to block_type everywhere

from .node import Node, NodeInstance, BlockInput, BlockOutput, BlockTemporaryVariable, BlockStateVariable

from .block import Block, BlockInstance

def connect(source, target, source_element):
    # TODO: Clean up
    if not isinstance(source, Block.Output) and not isinstance(source, blocks.BlockInput) and not isinstance(source, blocks.BlockStateVariable) and not isinstance(source, blocks.BlockTemporaryVariable):
        raise Exception('source parameter is not a Block.Output, BlockInput, BlockStateVariable, BlockTemporaryVariable')
    # TODO: Clean up
    if not isinstance(target, Block.Input) and not isinstance(target, blocks.BlockOutput):
        raise Exception('target parameter is not a Block.Input and not a BlockOutput')

    if source.block.graph != target.block.graph:
        # TODO: Can this happen from program code? If yes, make error message clearer?
        raise blocks.ProgramError(source_element, f'source has graph {source.block.graph} but target has graph {target.block.graph}')

    # TODO: More checks for non-ConnectorSpecificationData objects, e.g. KNX connections
    source_kind = None
    if isinstance(source, blocks.BlockStateVariable):
        source_kind = blocks.ConnectionKind.Value
    elif isinstance(source, blocks.BlockTemporaryVariable):
        source_kind = blocks.ConnectionKind.Value
    elif isinstance(source.conn_spec, blocks.ConnectorSpecificationData):
        source_kind = source.conn_spec.kind_spec.kind
    target_kind = None
    if isinstance(target.conn_spec, blocks.ConnectorSpecificationData):
        target_kind = target.conn_spec.kind_spec.kind
    # print(source_kind, target_kind)
    if source_kind != target_kind:
        hints = [
            # TODO: Does this always work?
            blocks.CompilerHint(source.block_output.conn_spec, 'Source was declared here'),
            blocks.CompilerHint(target.block_input.conn_spec, 'Target was declared here'),
        ]
        raise ProgramError(source_element, f'source has kind {source_kind} but target has kind {target_kind}', hints=hints)

    # TODO: Allow automatic type conversions?
    # TODO: Raise this error only when compiling?
    source_type = source.var_type
    if isinstance(source.block, blocks.Block) and source_type is not None:
        source_type = source_type.resolve_self(source.block.par_values)
    target_type = target.var_type
    if isinstance(target.block, blocks.Block) and target_type is not None:
        target_type = target_type.resolve_self(target.block.par_values)
    # TODO: Handle knx_bus connections better than just setting the type to None
    if source_type != target_type:
        hints = [
            # TODO: Does this always work?
            # TODO: Point to type, not to conn_spec
            blocks.CompilerHint(source.block_output.conn_spec, 'Source was declared here'),
            blocks.CompilerHint(target.block_input.conn_spec, 'Target was declared here'),
        ]
        raise ProgramError(source_element, f'source has type {source_type} but target has type {target_type}', hints=hints)

    target.connect(source)


debug_variable_resolution = False


# TODO: Create "BlockInstance" class to replace the "path" values everywhere? Should there be exactly one such instance or can there be multiple BlockInstances with the same path?

def path_resolve_singleton(path):
    if isinstance(path[-1], blocks.Block) and path[-1].block_type.is_singleton:
        return (path[-1].block_type,)
    else:
        return path


# Return the actual source for var in the context path
def resolve_variable(path, var):
    if debug_variable_resolution:
        print(f'resolve_variable({path}, {var})')

    if isinstance(path[-1], blocks.Block) and path[-1].block_type.is_singleton:
        raise Exception('isinstance(path[-1], blocks.Blocks) and path[-1].block_type.is_singleton')

    if isinstance(path[-1], Block):  # TODO: Handle singletons here?
        # TODO: Clean this up?
        # print(path, var)
        if path[-1] == var.block:
            block = var.block
        elif var.block.graph == path[-1].block_type:
            # Dependency on the result of a child block
            # TODO: Make this more generic
            # TODO: Modify path here?
            path = path_resolve_singleton(path + (var.block,))
            block = var.block
        else:
            raise Exception('path[-1] != var.block')
        # print(type(var), type(var.block))

        # TODO: This is somewhat of a hack
        if (isinstance(var, blocks.Block.Output) or isinstance(var, blocks.Block.Temporary)) and isinstance(block, blocks.BlockInput):
            input_name = block.input_name
            new_path = path[:-1]
            parent_block = new_path[-1]
            input_var = parent_block.block_inputs_byname[input_name]
            return resolve_variable(path_resolve_singleton(new_path), input_var)

        # TODO: This is somewhat of a hack
        if (isinstance(var, blocks.Block.Input)) and isinstance(block, blocks.BlockOutput):
            output_name = block.output_name
            new_path = path[:-1]
            parent_block = new_path[-1]
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
        # TODO: Handle this differently? This is e.g. for Bus objects
        if var.var_type is None:
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
        input_var = parent_block.block_inputs_byname[input_name]
        return resolve_variable(path_resolve_singleton(new_path), input_var)

    # TODO: Clean up
    if isinstance(var, blocks.BlockStateVariable):
        if path[-1] != var:
            raise Exception('path[-1] != var')
        # TODO: Implement this
        return path, var

    print(f'resolve_variable: Unhandled {path}, {var} {type(var)} {type(var.block)}')

    if debug_variable_resolution:
        print(f'resolve_variable: returning {path}, {var.temporary}')
    # TODO: This code seems to be unused
    # TODO: Avoid using .temporary (should be handled here)
    return path, var.temporary


# TODO: Rename to NativeBlockState?
class NativeState:
    def __init__(self, path, engine, static_block_context):
        self.block_path = path
        self.engine = engine
        self.static_block_context = static_block_context

    @property
    def block_type(self):
        value = self.block_path[-1]
        if isinstance(value, blocks.Block):
            return value.block_type
        else:
            return value

    @property
    def block(self):
        value = self.block_path[-1]
        if isinstance(value, blocks.Block):
            return value
        else:
            raise Exception(f'Trying to get block for a singleton block type ({value})')

    # TODO: Remove, this is a hack
    @property
    def block_or_singleton(self):
        value = self.block_path[-1]
        if isinstance(value, blocks.Block):
            return value
        else:
            return value.singleton_instance

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        pass

    # TODO: Merge start_tasks and start_tasks_async?
    # TODO: Allow blocking behavior during startup?
    # TODO: Use an AsyncExitStack or an ExitStack?
    async def start_tasks_async(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        pass

    @staticmethod
    @contextlib.contextmanager
    def start_task_context(task_group, coroutine):
        import wirely.misc.service

        task = task_group.create_task(wirely.misc.service.should_never_return(coroutine))
        try:
            yield task
        finally:
            task.cancel()

    @staticmethod
    def start_task(task_group, async_exit_stack, coroutine):
        return async_exit_stack.enter_context(NativeState.start_task_context(task_group, coroutine))


class Variable:
    def __init__(self, name, var_type):
        if not isinstance(name, str):
            raise Exception('name parameter is not a str')
        if var_type.contains_parameters:
            raise Exception('var_type parameter is not a concrete type')

        self.name = str(name)
        self.var_type = var_type

    def __repr__(self):
        return f'Variable[{self}]'

    def __str__(self):
        return f'{self.name}'


class VariableBlock:
    def __init__(self, variables, name):
        self.name = name

        self.variables = {}
        self.variable_set = set()

        for var in variables:
            if not isinstance(var, Variable):
                raise Exception('var parameter contains object that is not a Variable')
            name = var.name
            if name in self.variables:
                raise Exception(f'Duplicate variable name {name!r}')
            self.variables[name] = var
            self.variable_set.add(var)


class VariableBlockInstance:
    def __init__(self, variable_block):
        if not isinstance(variable_block, VariableBlock):
            raise Exception('variable_block parameter is not a VariableBlock')
        self.variable_block = variable_block

        self.data = {}

    def add(self, variable, value):
        # print(f'Setting {variable}')
        if variable not in self.variable_block.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableBlock {self.variable_block.name}')
        if variable in self.data:
            raise Exception(f'Variable {variable} already set')
        self.data[variable] = variable.var_type.verify_value(value)

    def __getitem__(self, variable):
        # print(f'Getting {variable}')
        if variable not in self.variable_block.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableBlock {self.variable_block.name}')
        if variable not in self.data:
            raise Exception(f'Variable {variable} not yet set')
        return self.data[variable]


# Things which will stay the same for one block instance
# TODO: Prevent modifications, more checks
# TODO: Merge this into BlockInstance? Or should this data (which will be needed at runtime) be kept separately?
class StaticBlockContext:
    def __init__(self, *, parameter_values, parsed_native_instance):
        self.parameter_values = parameter_values
        # TODO: Rename?
        self.parsed_native_instance = parsed_native_instance

    def __repr__(self):
        return f'StaticBlockContext({self.__dict__!r})'


# Things which will stay the same if the same instance of a procedure is executed multiple times
# TODO: Prevent modifications, more checks
class StaticProcedureContext:
    def __init__(self, *, sources, targets, state, name, block_parameter_values, block=None):
        self.sources = sources
        self.targets = targets
        self.state = state
        self.name = name
        self.block = block
        self.block_parameter_values = block_parameter_values

    def __repr__(self):
        return f'StaticProcedureContext({self.__dict__!r})'


# TODO: Prevent modifications, more checks (also e.g. on assignments to results etc.)
class ProcedureContext:
    def __init__(self, *, dep_values, results, state_old, state_new, static, step_info):
        self.dep_values = dep_values
        self.results = results
        self.state_old = state_old
        self.state_new = state_new
        self.static = static
        self.step_info = step_info
        self.wakeup_time = None

    def __repr__(self):
        return f'ProcedureContext({self.__dict__!r})'


# TODO: Rename to CycleContext?
class StepContext:
    def __init__(self):
        self.stage_start_time_monotonic = {}
        self.stage_start_time_utc = {}
        self.stage_start_time = {}

    def start_stage(self, engine, stage):
        if stage in self.stage_start_time:
            raise Exception(f'Stage {stage} already started')

        now_monotonic = blocks.MonotonicTime.current_time()
        if engine.fake_monotonic_time_offset is not None:
            now_monotonic += engine.fake_monotonic_time_offset
        # print(f'StepContext for {stage} started at {now_monotonic}')

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        # Note: This should work with all python versions, even without PEP 495
        # <https://peps.python.org/pep-0495/>
        # Starting with Python 3.6 (PEP 495)
        # datetime.datetime.now().astimezone() should return the same value.
        now = now_utc.astimezone()

        self.stage_start_time_monotonic[stage] = now_monotonic
        self.stage_start_time_utc[stage] = now_utc
        self.stage_start_time[stage] = now

    def __repr__(self):
        return f'StepContext({self.__dict__!r})'


from wirely.program import Program
from wirely.engine import Engine

from .ast import UnitLiteral
from .ast import ProgramError, InternalCompilerError, CompilerHint, SourcePosition, Declaration, BlockConnectorTypeSpecifier, ListSpecifier, ProgramError, Identifier, IntNumber, FloatNumber, StringConstant, Reference, Declaration, DeclarationConnector, DeclarationParameter, BlockInstantiation, DeclarationBlockInstantiation, DeclarationConnection, DeclarationStateVariable, DeclarationTemporaryVariable, DeclarationProcedure, NativeSpecifier, OptionalSpecifier, StageDeclaration, DependsDeclaration, ProducesDeclaration, ConnectionKindSpecifier, ConnectorSpecification, ConnectorSpecificationData, ConnectorSpecificationKnxBus, ConnectorSpecificationMqttServer, ConnectorSpecificationTwoNDevice, DeclarationLibrary, BlockParameter, Scope, BlockTypeSpecifier, ListLiteral, FunctionCall, BracketOperator, ConnectionKind, RunStage

from .block_type import BlockType
