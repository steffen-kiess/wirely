import abc

import wirely as blocks
from wirely.util import check_parameters
from wirely.parameters import ParameterValues, native_parameters, parameter_origins

# TODO: Rename this to BlockInstantiation or BlockUsage?
# TODO: Probably Input, Output, StateVariable etc. should be moved out of this and only exist in BlockType
class Block(blocks.Node):
    def __init__(self, graph, *, name, block_type, par_values, instantiation):
        super().__init__(graph=graph, name=name)

        self.block_type = block_type
        self.par_values = par_values
        self.instantiation = instantiation
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

        if self.name in self.graph.blocks:
            raise Exception(f'Duplicate block name {self.name!r}')
        self.graph.blocks[self.name] = self

    def __repr__(self):
        # return f"{type(self).__name__}{{{self.graph}.{self.name}}}"
        return f"{self.graph}.{self.name}[{type(self).__name__}]"

    def __str__(self):
        return f"{self.graph}.{self.name}"

    def validate(self):
        for input in self.all_block_inputs:
            if input.connected_source is None:
                # TODO: Hack
                if isinstance(input.block, blocks.BlockOutput):
                    continue
                raise Exception(f'Input {self.graph}.{self.name}.{input.name} is not connected')

        for output in self.block_outputs:
            if isinstance(output.conn_spec, blocks.ConnectorSpecificationResource):
                continue
            if output.temporary.producer is None:
                # TODO: Hack
                if isinstance(output.temporary.block, blocks.BlockInput):
                    continue
                raise Exception(f'Output {self.graph}.{self.name}.{output.name} has no producer')

    def create_native_state(self, *, context):
        path = context._block_path
        if path[-1] != self:
            raise Exception('path[-1] != self')
        # TODO
        if hasattr(self, 'block_type') and hasattr(self.block_type, 'is_native'):
            if self.block_type.is_native:
                # print('QQQ', self.name)
                return self.native_class(context=context)
            else:
                # print('WWW', self.name)
                pass

        return None

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
    def instantiate(self, *, singletons, variable_map, state_map_new, all_variables, state_map_changed, state_map, state_variables, instances, path, parent_instance):
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

        for name, child_block in self.block_type.blocks.items():
            child_path = path + (child_block,)
            child_block.instantiate(singletons=singletons, variable_map=variable_map, state_map_new=state_map_new, all_variables=all_variables, state_map_changed=state_map_changed, state_map=state_map, state_variables=state_variables, instances=instances, path=child_path, parent_instance=instance)

        # TODO: Clean up
        # path_prefix = '.'.join([block.name for block in path])
        path_prefix = '.'.join([(block.name if isinstance(block, blocks.Block) else block.name.id_str) for block in path])
        for state_var in self.block_statevars:
            v = blocks.Variable(path_prefix + '.' + state_var.name + '.new', state_var.var_type)
            state_map_new[path, state_var] = v
            all_variables.append(v)

            v = blocks.Variable(path_prefix + '.' + state_var.name + '.changed', blocks.types.boolean)
            state_map_changed[path, state_var] = v
            all_variables.append(v)

            v = blocks.Variable(path_prefix + '.' + state_var.name, state_var.var_type)
            state_map[path, state_var] = v
            variable_map[path, state_var.var_identifier] = v
            state_variables.append(v)

    def bind_variables(self, *, path, variable_map, resolve_binding):
        for var in self.all_block_inputs:
            if isinstance(var.conn_spec, blocks.ConnectorSpecificationResource):
                continue
            variable_map[path, var.var_identifier] = resolve_binding(path, var).variable
        for var in self.block_outputs:
            if isinstance(var.conn_spec, blocks.ConnectorSpecificationResource):
                continue
            variable_map[path, var.var_identifier] = resolve_binding(path, var).variable
        for var in self.block_temporaries:
            variable_map[path, var.var_identifier] = resolve_binding(path, var).variable

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
            if not isinstance(output, Block.Output) and not isinstance(output, blocks.BlockInput) and not isinstance(output, blocks.BlockStateVariable) and not isinstance(output, blocks.BlockTemporaryVariable):
                raise Exception('output parameter value is not an Output instance and not a BlockInput, BlockStateVariable or BlockTemporaryVariable')
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
            return 'INP ' + self.block.block_type.name.id_str + '.' + self.name

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
                self.temporary = Block.Temporary(block, name + '_val', var_type, impl_var_type=self.impl_var_type)
                # TODO: Hack
                self.orig_temporary = self.temporary

            self.block.block_outputs.append(self)
            if self.name in self.block.block_outputs_byname:
                raise Exception(f'Duplicate output name {name!r}')
            self.block.block_outputs_byname[self.name] = self

        @property
        def name_desc(self):
            if self.name == '':
                return 'implicit output'
            else:
                return self.name

        def __repr__(self):
            return f"{self.block}.{self.name}"

        # TODO: Remove and replace with self, Output should contains a reference to BlockType, not to Block
        @property
        def var_identifier(self):
            return 'OUT ' + self.block.block_type.name.id_str + '.' + self.name

    # TODO: Allow both temporaries and output values to be references to other values
    class Temporary(Source, Target):
        def __init__(self, block: 'Block', name: str, var_type: blocks.types.Type, *, impl_var_type):
            Block.Member.__init__(self, block, name)
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
            return 'TEMP ' + self.block.block_type.name.id_str + '.' + self.name

    class StateVariable(Source, Target):
        def __init__(self, block: 'Block', name: str, var_type: blocks.types.Type, initial_value):
            Block.Member.__init__(self, block, name)
            if not isinstance(var_type, blocks.types.Type):
                raise Exception('var_type parameter is not a Type')

            self.var_type = var_type
            self.initial_value = self.var_type.verify_value(initial_value)

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
            return 'STATE ' + self.block.block_type.name.id_str + '.' + self.name

    class Procedure:
        # TODO: Make name mandatory
        def __init__(self, block: 'Block', dependencies, results, impl, can_trigger=False, name=None, *, static_context, declaration_procedure):
            if not isinstance(block, Block):
                raise Exception('block parameter is not a Block')

            self.block = block
            self.name = name
            self.declaration_procedure = declaration_procedure
            # TODO: Is this a complete static context? Probably not because the context might also depend on the parameters to the current block. Move this somewhere else?
            self.static_context = static_context

            self.block.last_proc_id += 1
            self.id = self.block.last_proc_id

            self.dependencies = []
            for dependency in dependencies:
                if not isinstance(dependency, (Block.Source, Block.Output)):
                    raise Exception('dependencies parameter value is not an Input or Temporary instance')
                # State dependencies read the previous step and have no producer edge.
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
                if result.temporary.producer is not None:
                    raise Exception(f'{result} already has producer {result.temporary.producer}, got new producer {self}')
                # print(f'Setting result for {result} / {result.temporary}')
                result.temporary.producer = self

            self.impl = impl
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
            return 'PROC ' + self.block.block_type.name.id_str + '.' + self.name

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
            for key, value in next_parent.parameter_values.items():
                all_par_values.setdefault(key, value)
            next_parent = next_parent.parent_instance

        # An omitted optional parameter can be forwarded to another optional
        # parameter without introducing a value.
        for key, value in list(self.parameter_values.items()):
            if isinstance(value, (blocks.TypeParameter, blocks.ValueParameter)):
                source = value.parameter
                if (source is not key and source not in all_par_values
                        and source.default_value is None and key.is_optional):
                    del self.parameter_values[key]

        all_par_values.update(self.parameter_values)
        all_par_values = ParameterValues(all_par_values)
        parameter_values_resolved = {}
        parameter_keys = list(self.parameter_values)
        parameter_keys.extend(
            parameter for parameter in self.block_type.parsed_parameters.values()
            if parameter not in self.parameter_values and parameter.default_value is not None
        )
        for key in parameter_keys:
            value = all_par_values[key]
            if isinstance(value, blocks.Value):
                value_type = key.type_resolved.resolve_self(all_par_values)
                blocks.value.check_value_type(value.value_type, value_type, value.ast_element)
                value = blocks.ConstantValue(
                    value_type, value_type.verify_value(value.evaluate()), ast_element=value.ast_element,
                )
            parameter_values_resolved[key] = value
        self.parameter_values = parameter_values_resolved

        self.parameter_origins = parameter_origins(self)
        parameter_values_bystr = {key.name.id_str: value for key, value in parameter_values_resolved.items()}
        parameters = native_parameters(parameter_values_bystr)
        source = self.block.instantiation if self.block is not None else self.block_type
        name = '.'.join(node.name if isinstance(node, blocks.Block) else node.fullname
                        for node in self.path)
        context = blocks.NativeNodeConfigurationContext(
            name=name, parameters=parameters, _source=source,
            _origins={key.name.id_str: value for key, value in self.parameter_origins.items()},
        )
        native_class = self.block_type.native_class
        configuration = None
        if native_class is not None:
            data_type = native_class.native_block_data_type
            if data_type is not None:
                try:
                    configuration = data_type.create(context=context)
                except blocks.ProgramError:
                    raise
                except ValueError as error:
                    raise context.error(str(error)) from error
                except Exception as error:
                    raise blocks.InternalCompilerError(source, error, msg=f'Error while preparing configuration: {error}') from error
        self.static_context = blocks.StaticBlockContext(
            name=name, parameters=parameters, configuration=configuration,
        )

    @property
    def path(self):
        if self.is_singleton:
            return (self.block_type,)

        if self.parent_instance is None:
            base_path = ()
        else:
            base_path = self.parent_instance.path
        return base_path + (self.block,)
