import abc

from wirely.compiler import model as blocks
from wirely.util import check_parameters
from .attributes import copy_attributes
from wirely.compiler.parameters import ParameterValues, native_parameters
from wirely.compiler.types import resolve_type, check_runtime_type
from wirely.program import InputSourceBinding, Variable

# TODO: Rename this to BlockInstantiation or BlockUsage?
# TODO: Probably Input, Output, StateVariable etc. should be moved out of this and only exist in BlockType
class Block(blocks.Node):
    def __init__(self, graph, *, name, block_type, par_values, instantiation, register=True):
        super().__init__(graph=graph, name=name)

        self.block_type = block_type
        self.par_values = par_values
        self.instantiation = instantiation
        copy_attributes(self, instantiation)
        # TODO: Clean up
        for par in self.par_values:
            val = self.par_values[par]
            if not isinstance(val, blocks.ValueParameter) and not isinstance(val, blocks.Value) and not isinstance(val, blocks.Type):
               raise Exception(f'Got invalid parameter value: {val}')
            # TODO: Also check for contains_parameters? When should this be done?
            # if val.contains_parameters:
            #    raise Exception(f'Got parameter value {val} contains parameters')

        # TODO: A lot of this stuff should be moved to BlockType, instead the Block instance should only store the connections to the BlockType's inputs and from the BlockType's outputs
        self.block_inputs = []
        self.block_outputs = []
        self.block_inputs_byname = {}
        self.block_outputs_byname = {}
        self.block_temporaries = []
        self.block_statevars = []
        self.block_procedures = []
        self.last_proc_id = 0

        if register:
            if self.name in self.graph.blocks:
                raise Exception(f'Duplicate block name {self.name!r}')
            self.graph.blocks[self.name] = self

    @property
    def is_root(self):
        return self.block_type.parent_graph is None

    @property
    def type_name(self):
        name = self.block_type.name
        return name.id_str if name is not None else '<global>'

    @staticmethod
    def path_name(path, *, qualified=False):
        names = []
        for node in path:
            if isinstance(node, Block):
                if not node.is_root:
                    names.append(node.name)
            else:
                names.append(node.fullname if qualified else node.name.id_str)
        return '.'.join(names)

    def __repr__(self):
        # return f"{type(self).__name__}{{{self.graph}.{self.name}}}"
        return f"{self.graph}.{self.name}[{type(self).__name__}]"

    def __str__(self):
        return f"{self.graph}.{self.name}"

    @property
    def position(self):
        return next((attribute.value for attribute in self.attributes
                     if attribute.name == 'position'), None)

    def validate(self, path):
        """Require a source for each single input and a producer for each output."""
        name = Block.path_name(path, qualified=True)
        for input in self.all_block_inputs:
            if input.connected_source is None and not isinstance(input.block, blocks.BlockOutput):
                connector = 'Default input' if input.name == '' else f'Input {input.name!r}'
                raise blocks.ProgramError(self.instantiation, f'{connector} of node {name!r} is not connected',
                                          hints=[blocks.CompilerHint(input.conn_spec, 'Input was declared here')])

        for output in self.block_outputs:
            if (isinstance(output.conn_spec, blocks.ConnectorSpecificationResource)
                    or output.block_output.connected_source is not None):
                continue
            if output.orig_temporary.producer is None:
                connector = 'Default output' if output.name == '' else f'Output {output.name!r}'
                raise blocks.ProgramError(output.conn_spec, f'{connector} has no producer', hints=[
                    blocks.CompilerHint(self.instantiation, f'While instantiating {name!r}'),
                ])

    # TODO: Clean up
    @property
    def all_block_inputs(self):
        res = []
        for input in self.block_inputs:
            if input.is_list:
                # TODO: Currently the individual entries of the list are already in block_inputs
                # res += input.members
                pass
            else:
                res.append(input)
        return res

    # TODO: Rename
    # TODO: Must probably be cleaned up to allow multiple instantiations of the same block
    # path: A tuple which contains the name of the block being instantiated
    def instantiate(self, *, singletons, variable_map, all_variables, state_map, state_variables, instances, path, parent_instance):
        if self.block_type.is_singleton:
            if self.block_type in singletons:
                return

            # TODO: This probably should not need an instance of Block here
            singletons[self.block_type] = self

            # TODO: This is an ugly hack, remove
            if hasattr(self.block_type, 'singleton_instance'):
                raise Exception("hasattr(self.block_type, 'singleton_instance')")
            # print(f'Using {self} as instance for {self.block_type}')
            self.block_type.singleton_instance = self

            path = (self.block_type,)

        if self.block_type.is_singleton:
            instance = blocks.BlockInstance(self.block_type, parent_instance=None)
        else:
            instance = blocks.BlockInstance(self, parent_instance=parent_instance)

        if path != instance.path:
            raise Exception(f'path {path} != instance.path {instance.path}')

        if instance.path in instances:
            raise Exception(f'Duplicate path {path}')
        instances[instance.path] = instance
        if not self.is_root:
            self.validate(path)

        for name, child_block in self.block_type.blocks.items():
            child_path = path + (child_block,)
            child_block.instantiate(singletons=singletons, variable_map=variable_map, all_variables=all_variables, state_map=state_map, state_variables=state_variables, instances=instances, path=child_path, parent_instance=instance)

        # TODO: Clean up
        # path_prefix = '.'.join([block.name for block in path])
        path_prefix = self.path_name(path)
        if path_prefix:
            path_prefix += '.'
        for state_var in self.block_statevars:
            var_type = resolve_type(state_var.var_type, instance.static_values)
            source = (self.block_type.variables[state_var.name]
                      if self.is_root else self.instantiation)
            check_runtime_type(var_type, source)
            v = Variable(path_prefix + state_var.name, var_type)
            state_map[path, state_var] = v
            variable_map[path, state_var.var_identifier] = InputSourceBinding(v, new_state=False)
            state_variables.append(v)

    def bind_variables(self, *, path, variable_map, resolve_binding):
        for var in self.all_block_inputs:
            if isinstance(var.conn_spec, blocks.ConnectorSpecificationResource):
                continue
            variable_map[path, var.var_identifier] = resolve_binding(path, var)
        for var in self.block_outputs:
            if isinstance(var.conn_spec, blocks.ConnectorSpecificationResource):
                continue
            variable_map[path, var.var_identifier] = resolve_binding(path, var)
        for var in self.block_temporaries:
            variable_map[path, var.var_identifier] = resolve_binding(path, var)

    # TODO: Probably should be merged into instantiate()
    def get_block_procedures_paths(self, prefix):
        path = blocks.path_resolve_singleton(prefix + (self,))
        res = [(path, proc) for proc in self.block_procedures]
        for child_block in self.block_type.blocks.values():
            res += child_block.get_block_procedures_paths(path)
        return res

    # TODO: Probably should be merged into instantiate()
    def get_all_block_paths(self, prefix):
        path = blocks.path_resolve_singleton(prefix + (self,))
        res = [path]
        for child_block in self.block_type.blocks.values():
            res += child_block.get_all_block_paths(path)
        return res

    class Member(abc.ABC):
        def __init__(self, block: 'Block', name: str):
            if not isinstance(block, Block):
                raise Exception('block parameter is not a Block')
            if not isinstance(name, str):
                raise Exception('name parameter is not a str')
            name = str(name)

            self.block = block
            self.name = name

        def __repr__(self):
            return f"{self.block}.{self.name}"

    # TODO: Move out of block class / Remove Member parent?
    class Source(Member, abc.ABC):
        @property
        @abc.abstractmethod
        def temporary(self):
            ...

    # TODO: Move out of block class / Remove Member parent?
    class Target(Member, abc.ABC):
        pass

    # TODO: Handle var_type == None better
    # TODO: Clean up, move more functionality to BlockInput
    class Input(Source):
        def __init__(self, block: 'Block', name: str, is_list: bool, var_type: blocks.types.Type, *, block_input: blocks.BlockInput):
            Block.Member.__init__(self, block, name)
            if not isinstance(is_list, bool):
                raise Exception('is_list parameter is not of type bool')
            if var_type is not None and not isinstance(var_type, blocks.types.Type):
                raise Exception('var_type parameter is not a Type')

            self.is_list = bool(is_list)
            self.var_type = var_type
            self.block_input = block_input
            copy_attributes(self, block_input)

            self.conn_spec = self.block_input.conn_spec
            self.resource_type = self.block_input.resource_type

            if isinstance(self.conn_spec, blocks.ConnectorSpecificationData):
                if self.conn_spec.kind_spec.kind == blocks.ConnectionKind.Value:
                    self.impl_var_type = self.var_type
                elif self.conn_spec.kind_spec.kind == blocks.ConnectionKind.Event:
                    self.impl_var_type = blocks.types.TypeOption(self.var_type)
                else:
                    raise Exception(f'Unknown ConnectionKind: {self.conn_spec.kind_spec.kind}')
            else:
                # TODO: ?
                self.impl_var_type = None

            if self.is_list:
                self.members = []
            else:
                self.connected_source = None

            self.block.block_inputs.append(self)
            if self.name in self.block.block_inputs_byname:
                raise Exception(f'Duplicate input name {name!r}')
            self.block.block_inputs_byname[self.name] = self

        def __repr__(self):
            return f"{self.block}.{self.name}"

        @property
        def temporary(self):
            if self.is_list:
                raise Exception('Trying to get temporary for list')
            if self.connected_source is None:
                raise Exception(f'{self} is not connected')
            # TODO: Cache multiple follows?
            return self.connected_source.temporary

        def connect(self, output):
            # TODO: Clean up class structure for value sources / targets
            if not isinstance(output, (Block.Output, blocks.BlockInput, blocks.BlockStateVariable, blocks.BlockTemporaryVariable, blocks.StateRead)):
                raise Exception('output parameter value is not a data or resource source')
            if self.is_list:
                # TODO: Clean up
                # TODO: What should be used for block_input here?
                input = Block.Input(self.block, f'{self.name}.{len(self.members)}', False, self.var_type, block_input=self)
                input.connect(output)
                self.members.append(input)
            else:
                if self.connected_source is not None:
                    raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {output}')

                self.connected_source = output

        # TODO: Remove and replace with self, Input should contains a reference to BlockType, not to Block
        @property
        def var_identifier(self):
            return 'INP ' + self.block.type_name + '.' + self.name

    # TODO: Handle var_type == None better
    # TODO: Clean up, move more functionality to BlockOutput
    class Output(Target):
        # TODO: Allow creating Output without Temporary?
        def __init__(self, block: 'Block', name: str, var_type: blocks.types.Type, input_dependencies=[], *, block_output: blocks.BlockOutput):
            Block.Member.__init__(self, block, name)
            if var_type is not None and not isinstance(var_type, blocks.types.Type):
                raise Exception('var_type parameter is not a Type')

            self.var_type = var_type
            self.block_output = block_output
            copy_attributes(self, block_output)

            self.conn_spec = self.block_output.conn_spec
            self.resource_type = self.block_output.resource_type

            if isinstance(self.conn_spec, blocks.ConnectorSpecificationData):
                if self.conn_spec.kind_spec.kind == blocks.ConnectionKind.Value:
                    self.impl_var_type = self.var_type
                elif self.conn_spec.kind_spec.kind == blocks.ConnectionKind.Event:
                    self.impl_var_type = blocks.types.TypeOption(self.var_type)
                else:
                    raise Exception(f'Unknown ConnectionKind: {self.conn_spec.kind_spec.kind}')
            else:
                # TODO: ?
                self.impl_var_type = None

            self.input_dependencies = []
            for dependency in input_dependencies:
                if not isinstance(dependency, Block.Input):
                    raise Exception('input_dependencies parameter value is not an Input instance')
                if dependency.block != self.block:
                    raise Exception('input_dependencies parameter value is for another block')
                self.input_dependencies.append(dependency)

            if isinstance(self.conn_spec, blocks.ConnectorSpecificationData):
                # The producing procedure renames this to <procedure>.<result key>.
                self.temporary = Block.Temporary(block, name, var_type, impl_var_type=self.impl_var_type, output=self)
                # TODO: Hack
                self.orig_temporary = self.temporary

            self.block.block_outputs.append(self)
            if self.name in self.block.block_outputs_byname:
                raise Exception(f'Duplicate output name {name!r}')
            self.block.block_outputs_byname[self.name] = self

        @property
        def name_desc(self):
            if self.name == '':
                return 'default output'
            else:
                return self.name

        def __repr__(self):
            return f"{self.block}.{self.name}"

        # TODO: Remove and replace with self, Output should contains a reference to BlockType, not to Block
        @property
        def var_identifier(self):
            return 'OUT ' + self.block.type_name + '.' + self.name

    # TODO: Allow both temporaries and output values to be references to other values
    class Temporary(Source, Target):
        def __init__(self, block: 'Block', name: str, var_type: blocks.types.Type, *, impl_var_type, output=None):
            Block.Member.__init__(self, block, name)
            self.output = output  # The output stored here, or None for declared temporaries.
            if not isinstance(var_type, blocks.types.Type):
                raise Exception('var_type parameter is not a Type')

            self.var_type = var_type

            self.impl_var_type = impl_var_type

            self.producer = None

            self.block.block_temporaries.append(self)

        def __repr__(self):
            return f"{self.block}.{self.name}"

        @property
        def temporary(self):
            return self

        # TODO: Remove and replace with self, Temporary should contains a reference to BlockType, not to Block
        @property
        def var_identifier(self):
            separator = '.' if self.output is None else ':'
            return 'TEMP ' + self.block.type_name + separator + self.name

    class StateVariable(Source, Target):
        def __init__(self, block: 'Block', name: str, var_type: blocks.types.Type, initial_value):
            Block.Member.__init__(self, block, name)
            if not isinstance(var_type, blocks.types.Type):
                raise Exception('var_type parameter is not a Type')

            self.var_type = var_type
            self.initial_value = initial_value

            self.impl_var_type = self.var_type

            self.producer = None

            self.block.block_statevars.append(self)

        def __repr__(self):
            return f"StateVariable[{self}]"

        def __str__(self):
            return f"{self.block}.{self.name}"

        # TODO
        @property
        def temporary(self):
            return self

        @property
        def var_identifier(self):
            return 'STATE ' + self.block.type_name + '.' + self.name

    class Procedure:
        # TODO: Make name mandatory
        def __init__(self, block: 'Block', dependencies, results, can_trigger=False, name=None, *, static_context, declaration_procedure):
            if not isinstance(block, Block):
                raise Exception('block parameter is not a Block')

            self.block = block
            self.name = name
            self.declaration_procedure = declaration_procedure
            copy_attributes(self, declaration_procedure)
            # TODO: Is this a complete static context? Probably not because the context might also depend on the parameters to the current block. Move this somewhere else?
            self.static_context = static_context

            self.block.last_proc_id += 1
            self.id = self.block.last_proc_id

            self.dependencies = []
            for dependency in dependencies:
                if not isinstance(dependency, (Block.Source, Block.Output, blocks.StateRead)):
                    raise Exception('dependencies parameter value is not an Input or Temporary instance')
                # Old-state dependencies have no producer edge; new-state reads depend on the writer.
                # TODO: Allow dependencies on other blocks? Probably yes. Maybe check where dependency.block is a child of the current block?
                # if dependency.block != self.block:
                #     raise Exception('dependencies parameter value is for another block')
                self.dependencies.append(dependency)

            self.results = []
            for result in results:
                if not isinstance(result, Block.Target):
                    raise Exception(f'results parameter value is not an Output or Temporary instance: {type(result)}')
                if result.block != self.block:
                    raise Exception('results parameter value is for another block')
                self.results.append(result)

                # TODO:  whether dependencies of output include required inputs as dependencies

                # TODO: Clean up temporary / output stuff
                previous = result.temporary.producer
                if previous is not None:
                    if isinstance(result, Block.Output):
                        target = 'Default output' if result.name == '' else f'Output {result.name!r}'
                    else:
                        target = f'Variable {result.name!r}'
                    raise blocks.ProgramError(
                        declaration_procedure, f'{target} is already produced by procedure {previous.name!r}',
                        hints=[blocks.CompilerHint(previous.declaration_procedure, 'Previous producer was here')])
                # print(f'Setting result for {result} / {result.temporary}')
                result.temporary.producer = self

            for key, target in static_context._targets.items():
                if isinstance(target, Block.Output):
                    target.temporary.name = f'{name}.{key}'

            self.can_trigger = can_trigger

            self.block.block_procedures.append(self)

        def __repr__(self):
            if self.name is not None:
                return f"{self.block}.{self.name}"
            else:
                return f"{self.block}.proc{self.id}"

        # TODO: Remove and replace with self, Procedure should contains a reference to BlockType, not to Block
        @property
        def proc_identifier(self):
            return 'PROC ' + self.block.type_name + '.' + self.name

class BlockInstance(blocks.NodeInstance):
    @check_parameters
    def __init__(self, block, parent_instance):
        if isinstance(block, blocks.Block):
            super().__init__(block, parent_instance=parent_instance)
            # Alias for self.node
            self.block = block

            self.is_singleton = False

            self.block_type = self.block.block_type
        elif isinstance(block, blocks.BlockType):
            if parent_instance != None:
                raise Exception('parent_instance != None')
            # TODO: Clean up? Create separate type?
            super().__init__(None, parent_instance=None)
            self.block = None

            self.is_singleton = True

            self.block_type = block
        else:
            raise Exception(f'Unknown value for block: {block}')

        if self.is_singleton:
            self.parameter_values = {}
        else:
            self.parameter_values = dict(self.block.par_values)

        all_par_values = {}
        next_parent = self.parent_instance
        while next_parent is not None:
            for key, value in next_parent.static_values.items():
                all_par_values.setdefault(key, value)
            next_parent = next_parent.parent_instance

        all_par_values.update(self.parameter_values)
        all_par_values = ParameterValues(all_par_values)
        parameter_values_resolved = {}
        parameter_keys = list(self.parameter_values)
        parameter_keys.extend(
            parameter for parameter in self.block_type.parsed_parameters.values()
            if parameter not in self.parameter_values and parameter.default_value is not None
        )
        for key in parameter_keys:
            if key in all_par_values:
                parameter_values_resolved[key] = all_par_values[key]
        self.parameter_values = parameter_values_resolved
        try:
            self.constant_values = {
                declaration: all_par_values[declaration]
                for declaration in self.block_type.parsed_constants.values()
            }
        except blocks.ProgramError as error:
            owner = self.block or self.block_type.singleton_instance
            if not owner.is_root:
                name = Block.path_name(self.path, qualified=True)
                error.hints = [*error.hints, blocks.CompilerHint(
                    owner.instantiation, f'While instantiating {name!r}',
                )]
            raise
        self.static_values = {
            key: all_par_values[key] for key in all_par_values if key in all_par_values
        }
        self.static_values.update(self.parameter_values)
        self.static_values.update(self.constant_values)

        for assertion in self.block_type.assertions:
            assertion.evaluate(self)

        from .static_bindings import evaluate_bindings
        initializer = self.block_type.initializer
        self.initializer_values = evaluate_bindings(
            initializer.uses if initializer is not None else None, self,
        )

        parameter_values_bystr = {key.name.id_str: value for key, value in parameter_values_resolved.items()}
        parameters = native_parameters(parameter_values_bystr)
        name = Block.path_name(self.path, qualified=True) or '<global>'
        self.static_context = blocks.StaticBlockContext(name=name, parameters=parameters)

    @property
    def path(self):
        if self.is_singleton:
            return (self.block_type,)

        if self.parent_instance is None:
            base_path = ()
        else:
            base_path = self.parent_instance.path
        return base_path + (self.block,)
