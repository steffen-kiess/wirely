import typing

import wirely as blocks
from wirely.util import check_parameters


class BlockType(blocks.Scope):
    @check_parameters
    def __init__(self, specifiers: typing.List[blocks.BlockTypeSpecifier], name: typing.Optional[blocks.Identifier], declarations: typing.List[blocks.Declaration], *, source_position: typing.Optional[blocks.SourcePosition]):
        super().__init__(source_position=source_position)
        self.name = name
        self.declarations = declarations

        all_specifiers = ['native', 'singleton']
        specifiers_status = dict({key: False for key in all_specifiers})
        for specifier in specifiers:
            if specifier.specifier_str not in all_specifiers:
                raise Exception(f'Unknown specifier: {specifier.specifier_str!r}')
            if specifiers_status[specifier.specifier_str]:
                raise blocks.ProgramError(specifier, f'Got duplicate {specifier.specifier_str} specifier')
            specifiers_status[specifier.specifier_str] = True
        self.is_native = specifiers_status['native']
        self.is_singleton = specifiers_status['singleton']

        self.blocks = {}
        # TODO: Remove one of block_type_interfaces / block_type_implementations
        self.block_type_interfaces = {}
        self.block_type_implementations = {}
        self.procedures = {}
        self.variables = {}
        # TODO: Clarify what is the key here (name or input_name)
        self.block_inputs = {}
        self.block_outputs = {}
        self.block_state_variables = {}
        self.block_temporary_variables = {}
        self.libraries = {}

    @staticmethod
    def make(*args, source_position: typing.Optional[blocks.SourcePosition]):
        specifiers = args[:-2]
        name = args[-2]
        declarations = args[-1]
        return BlockType(list(specifiers), name, declarations, source_position=source_position)

    def compile_pass1(self, graph):
        if hasattr(self, 'parent_graph'):
            raise Exception("hasattr(self, 'parent_graph')")
        self.parent_graph = graph

        if self.parent_graph is not None:
            if self.name.id_str in graph.block_type_interfaces:
                hints = [
                    blocks.CompilerHint(graph.block_type_interfaces[self.name.id_str], 'Previous block type was here'),
                ]
                raise blocks.ProgramError(self, f'Duplicate block type {self.name!r}', hints=hints)
            graph.block_type_interfaces[self.name.id_str] = self

        # TODO: Merge parsed_parameters and parameters?
        parsed_parameters = {}
        for decl in self.declarations:
            decl.compile_pass1_btd(self)

            # TODO: Clean up isinstance?
            if not isinstance(decl, blocks.DeclarationParameter):
                continue
            with decl.context:
                if self.is_singleton:
                    # TODO: Allow parameters for singleton blocks and create one instance for each parameter combination?
                    raise blocks.ProgramError(decl, f'Singleton block cannot have parameters')

                name = decl.name.id_str
                if name in parsed_parameters:
                    hints = [
                        blocks.CompilerHint(parsed_parameters[name], 'Previous parameter was here'),
                    ]
                    raise blocks.ProgramError(decl, f'Duplicate parameter name {name!r}', hints=hints)
                parsed_parameters[name] = decl
        if hasattr(self, 'parsed_parameters'):
            raise Exception("hasattr(self, 'parsed_parameters')")
        self.parsed_parameters = parsed_parameters

        # Former "implementation" part

        for decl in self.declarations:
            if isinstance(decl, blocks.Declaration):
                with decl.context:
                    decl.compile_pass1(self)

        if self.parent_graph is not None:
            if self.name.id_str in self.parent_graph.block_type_implementations:
                hints = [
                    blocks.CompilerHint(graph.block_type_implementations[self.name.id_str], 'Previous implementation was here'),
                ]
                raise blocks.ProgramError(self, f'Duplicate block type implementation for {self.name!r}', hints=hints)
            self.parent_graph.block_type_implementations[self.name.id_str] = self

    def compile_pass1_5(self, graph):
        self.compile_pass1_5_if_needed()

    def compile_pass1_5_if_needed(self):
        # TODO: Make this more generic, provide proper error messages on cycles
        if hasattr(self, 'pass1_5_done'):
            return
        self.pass1_5_done = True

        # Former "implementation" part

        for decl in self.declarations:
            with decl.context:
                decl.create_io(self)

        for decl in self.declarations:
            if isinstance(decl, blocks.Declaration):
                with decl.context:
                    decl.compile_pass1_5(self)

        # Resolve Parameters
        for par_name in self.parsed_parameters:
            par = self.parsed_parameters[par_name]

            par.resolve_type()

        # TODO: Remove
        self.parameters_by_name = self.parsed_parameters

    def compile_pass2(self, graph):
        for decl in self.declarations:
            if isinstance(decl, blocks.Declaration):
                with decl.context:
                    decl.compile_pass2(self)

        # TODO: Allow interfaces without implementations?
        # if self.name.id_str not in graph.block_type_implementations:
        #     raise blocks.ProgramError(self, f'Block type interface for {self.name!r} without implementation')

        # TODO: Validate parameters

    def create(self, *, name, graph, par_values, instantiation):
        # Do this here to avoid problems with circular imports
        import wirely.native

        # print('create', name, graph, par_values)
        block = blocks.Block(name=name, graph=graph, block_type=self, par_values=par_values, instantiation=instantiation)
        # TODO: Remove
        block.parameter_values = dict(par_values)

        for par in self.parsed_parameters.values():
            # print(par, par.is_optional)
            if not par.is_optional and par not in block.parameter_values:
                # TODO: Raise ProgramError and pass source location
                raise Exception(f'Missing value for parameter {par.name!r}')

        # TODO: Pass list of connections to constructor instead of getting them from self.graph
        block.custom_inputs = {}
        block.custom_outputs = {}
        for input in self.block_inputs.values():
            # TODO
            # if not isinstance(input.conn_spec, blocks.ConnectorSpecificationData):
            #     continue
            # TODO: Is the name of the block and the name of the input always the same?
            new_input = blocks.Block.Input(block, input.input_name, input.is_list, input.var_type, block_input=input)
            block.custom_inputs[input] = new_input
        for output in self.block_outputs.values():
            # TODO
            # if not isinstance(output.conn_spec, blocks.ConnectorSpecificationData):
            #     continue
            # TODO: Is the name of the block and the name of the output always the same?
            new_output = blocks.Block.Output(block, output.output_name, output.var_type, block_output=output)
            block.custom_outputs[output] = new_output

        # TODO: Probably more stuff should be done at an earlier stage
        state_variables = {}
        for variable in self.variables.values():
            if isinstance(variable, blocks.ast.DeclarationStateVariable):
                var_type = self.resolve_type(variable.var_type, par_values=block.parameter_values)
                initial_value = self.get_const_value(variable.initial_value, value_type=var_type, par_values=block.parameter_values)
                # TODO: Can there be type parameters here?
                initial_value = initial_value.value
                block_var = blocks.Block.StateVariable(block, variable.name.id_str, var_type, initial_value)
                if hasattr(var_type, 'block_var'):
                    raise Exception("hasattr(var_type, 'block_var')")
                variable.block_var = block_var
                state_variables[variable.name.id_str] = block_var
            elif isinstance(variable, blocks.ast.DeclarationTemporaryVariable):
                var_type = self.resolve_type(variable.var_type, par_values=block.parameter_values)
                impl_var_type = var_type  # TODO: ?
                block_var = blocks.Block.Temporary(block, variable.name.id_str, var_type, impl_var_type=impl_var_type)
                if hasattr(var_type, 'block_var'):
                    raise Exception("hasattr(var_type, 'block_var')")
                variable.block_var = block_var
            else:
                raise Exception(f'Got unexpected variable: {variable}')

        have_native = self.is_native
        for procedure in self.procedures.values():
            have_native = have_native or procedure.is_native
        native_class = None
        if have_native:
            # TODO: Should this be done here? This will be called if 'self' is used in another block, even if that block is not instantiated. Probably looking up the native block type should be delayed until then to avoid loading the native module needed for that. Alternatively, the native class should be looked up even if 'self' is not used at all to do proper error checking.
            native_class = wirely.native.get_native_block_type(self)
            if self.is_native and not native_class.native_block_has_state:
                # TODO: Allow this?
                raise Exception(f'Block type {self.fullname!r} requires native state but native class is stateless')
            if not self.is_native and native_class.native_block_has_state:
                raise Exception(f'Block type {self.fullname!r} does not requires native state but native class is stateful')
        block.native_class = native_class

        # TODO: This is an ugly hack, native class resolution should be done per-type, not per-instance
        if not hasattr(self, 'native_class'):
            self.native_class = native_class

        # TODO: Probably more stuff should be done at an earlier stage
        for procedure in self.procedures.values():
            # print(procedure, procedure.depends.get_resolved_dependencies(), procedure.produces.get_resolved_outputs())
            sources = {}
            targets = {}
            inputs = []
            procedures = {}
            for dep in procedure.depends.get_resolved_dependencies():
                # print(type(dep))
                # TODO: Clean up
                if isinstance(dep, blocks.DeclarationTemporaryVariable):
                    input = dep.block_var
                    inputs.append(input)
                    sources[dep.name.id_str] = input
                elif isinstance(dep, blocks.BlockInput):
                    input = block.custom_inputs[dep]
                    inputs.append(input)
                    sources[dep.name] = input
                elif isinstance(dep, blocks.Block):
                    # TODO: Clean up handling of implicit output of block, allow explicit output
                    input = dep.block_outputs_byname[''].temporary
                    inputs.append(input)
                    # TODO: What should be the name of this in sources?
                    sources[dep.name] = input
                else:
                    raise Exception(f'Got unknown dependency: {dep}')
            outputs = []
            for out in procedure.produces.get_resolved_outputs():
                # print(type(out))
                if isinstance(out, blocks.ast.DeclarationStateVariable):
                    output = out.block_var
                    outputs.append(output)
                    targets[output.name] = output
                elif isinstance(out, blocks.ast.DeclarationTemporaryVariable):
                    output = out.block_var
                    outputs.append(output)
                    targets[output.name] = output
                elif isinstance(out, blocks.BlockOutput):
                    # TODO: Clean up
                    output = block.custom_outputs[out]
                    outputs.append(output)
                    targets[output.name] = output
                    # print(type(out.inp))
                    # print(out.inp)
                    # # outputs.append(out.inp)
                    # # targets[out.name] = out.inp
                    # temp = blocks.Block.Temporary(block, procedure.name.id_str + '__' + out.name, out.inp.var_type, impl_var_type=...)
                    # outputs.append(temp)
                    # targets[out.name] = temp
                    # # TODO: Remove this, connecting the different graphs should be done at instantiation time, not here
                    # # out.inp.connect(temp)
                    # # if out.inp.connected_source is not None:
                    # #     raise Exception('out.inp.connected_source is not None')
                    # # out.inp.connected_source = temp
                    # # TODO: This is a hack to avoid a "Input graph1.Delay.implicit output. is not connected" exception, this should not be needed because graph1.Delay (without instantiation) should not be used for anything
                    # out.inp.connected_source = temp
                else:
                    raise Exception(f'Unknown output: {out}')

            static_context = blocks.StaticProcedureContext(sources=sources, targets=targets, state=state_variables, name=block.name, block_parameter_values={key.name.id_str: value for key, value in block.par_values.items()})

            if not procedure.is_native:
                raise Exception('Got non-native procedure')
            else:
                # TODO: Think about e.g. static stateful procedures
                func = getattr(native_class, procedure.name.id_str, None)
                if func is None:
                    raise Exception(f'Cannot find native procedure {procedure.name.id_str!r} in native block type {self.name!r}')
                if self.is_native:
                    def run(*, context, native_state, func=func):
                        func(native_state, context=context)
                else:
                    def run(*, context, native_state, func=func):
                        func(context=context)
            # print('QQ', [type(x) for x in inputs], [type(x) for x in outputs])
            block_procedure = blocks.Block.Procedure(block, inputs, outputs, run, name=procedure.name.id_str, static_context=static_context, declaration_procedure=procedure)
            if procedure in procedures:
                raise Exception('procedure in procedures')
            procedures[procedure] = block_procedure

        # print(block)
        # print(block.block_inputs)
        # print(block.block_outputs)
        # print(block.all_block_inputs)

        return block

    def __repr__(self):
        return self.fullname

    @property
    def fullname(self):
        if self.name is None and self.parent_graph is None:
            return '<global>'
        elif self.parent_graph.name is None:
            return self.name.id_str
        else:
            return self.parent_graph.fullname + '.' + self.name.id_str

    @property
    def fullname_tuple(self):
        if self.name is None and self.parent_graph is None:
            return ()
        elif self.parent_graph.name is None:
            return (self.name.id_str),
        else:
            return self.parent_graph.fullname_tuple + (self.name.id_str,)

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass

    # TODO: Currently only used for singletons, clean this up, merge with block.create_native_state
    def create_native_state(self, path, engine, static_block_context):
        if len(path) != 1:
            raise Exception('len(path) != 1')
        if path[-1] != self:
            raise Exception('path[-1] != self')

        if self.is_native:
            # print('QQQ', self.name)
            return self.native_class(path, engine, static_block_context=static_block_context)
        else:
            # print('WWW', self.name)
            pass

        return None
