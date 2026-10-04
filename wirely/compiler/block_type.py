import typing

from . import native as compiler_native
from .attributes import copy_attributes
from .types import check_runtime_type, resolve_type

from wirely.compiler import model as blocks
from wirely.util import check_parameters


class BlockType(blocks.Scope):
    @check_parameters
    def __init__(self, specifiers: typing.List[blocks.BlockTypeSpecifier], name: typing.Optional[blocks.Identifier], declarations: list, *, source_position: typing.Optional[blocks.SourcePosition]):
        super().__init__(source_position=source_position)
        self.name = name
        self.declarations = declarations
        self.inner_attributes = getattr(declarations, 'inner_attributes', ())

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
        self.initializer = None
        self.variables = {}
        # TODO: Clarify what is the key here (name or input_name)
        self.block_inputs = {}
        self.block_outputs = {}
        self.block_state_variables = {}
        self.block_temporary_variables = {}
        self.libraries = {}
        self.assertions = []

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
            if self.name.id_str in graph.struct_types:
                raise blocks.ProgramError(self.name, f'Node type {self.name.id_str} conflicts with struct type', hints=[
                    blocks.CompilerHint(graph.struct_types[self.name.id_str], 'Previous declaration was here'),
                ])
            if self.name.id_str in graph.enum_types:
                raise blocks.ProgramError(self.name, f'Node type {self.name.id_str} conflicts with enum type', hints=[
                    blocks.CompilerHint(graph.enum_types[self.name.id_str].declaration, 'Previous declaration was here'),
                ])
            if self.name.id_str in graph.resource_types:
                raise blocks.ProgramError(self, f'Node type {self.name.id_str} conflicts with resource type', hints=[
                    blocks.CompilerHint(graph.resource_types[self.name.id_str].declaration, 'Previous resource type was here'),
                ])
            if self.name.id_str in graph.block_type_interfaces:
                hints = [
                    blocks.CompilerHint(graph.block_type_interfaces[self.name.id_str], 'Previous block type was here'),
                ]
                raise blocks.ProgramError(self, f'Duplicate block type {self.name!r}', hints=hints)
            graph.block_type_interfaces[self.name.id_str] = self

        # TODO: Merge parsed_parameters and parameters?
        parsed_parameters = {}
        parsed_constants = {}
        static_declarations = {}
        for decl in self.declarations:
            decl.compile_pass1_btd(self)

            # TODO: Clean up isinstance?
            if not isinstance(decl, blocks.DeclarationParameter):
                continue
            with decl.context:
                is_constant = isinstance(decl, blocks.DeclarationConstant)
                if self.is_singleton and not is_constant:
                    # TODO: Allow parameters for singleton blocks and create one instance for each parameter combination?
                    raise blocks.ProgramError(decl, f'Singleton block cannot have parameters')

                name = decl.name.id_str
                if name in static_declarations:
                    previous = static_declarations[name]
                    kind = 'constant' if is_constant else 'parameter'
                    raise blocks.ProgramError(decl, f'Duplicate {kind} name {name!r}', hints=[
                        blocks.CompilerHint(previous, 'Previous declaration was here'),
                    ])
                static_declarations[name] = decl
                (parsed_constants if is_constant else parsed_parameters)[name] = decl
        if hasattr(self, 'parsed_parameters'):
            raise Exception("hasattr(self, 'parsed_parameters')")
        self.parsed_parameters = parsed_parameters
        self.parsed_constants = parsed_constants

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

        for declaration in self.static_declarations.values():
            declaration.resolve_type()


        # Former "implementation" part

        for decl in self.declarations:
            with decl.context:
                decl.create_io(self)

        for decl in self.declarations:
            if isinstance(decl, blocks.Declaration):
                with decl.context:
                    decl.compile_pass1_5(self)

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

    def create(self, *, name, graph, par_values, instantiation, register=True):
        # print('create', name, graph, par_values)
        block = blocks.Block(name=name, graph=graph, block_type=self, par_values=par_values,
                             instantiation=instantiation, register=register)
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
            if input.var_type is not None:
                check_runtime_type(resolve_type(input.var_type, block.par_values), instantiation)
            new_input = blocks.Block.Input(block, input.input_name, input.is_list, input.var_type, block_input=input)
            block.custom_inputs[input] = new_input
        for output in self.block_outputs.values():
            # TODO
            # if not isinstance(output.conn_spec, blocks.ConnectorSpecificationData):
            #     continue
            # TODO: Is the name of the block and the name of the output always the same?
            if output.var_type is not None:
                check_runtime_type(resolve_type(output.var_type, block.par_values), instantiation)
            new_output = blocks.Block.Output(block, output.output_name, output.var_type, block_output=output)
            block.custom_outputs[output] = new_output

        # TODO: Probably more stuff should be done at an earlier stage
        state_variables = {}
        for variable in self.variables.values():
            if isinstance(variable, blocks.ast.DeclarationStateVariable):
                var_type = self.resolve_type(variable.var_type)
                check_runtime_type(var_type, instantiation)
                block_var = blocks.Block.StateVariable(
                    block, variable.name.id_str, var_type, variable.initial_value,
                )
                if hasattr(var_type, 'block_var'):
                    raise Exception("hasattr(var_type, 'block_var')")
                copy_attributes(block_var, variable)
                variable.block_var = block_var
                state_variables[variable.name.id_str] = block_var
            elif isinstance(variable, blocks.ast.DeclarationTemporaryVariable):
                var_type = self.resolve_type(variable.var_type, par_values=block.parameter_values)
                check_runtime_type(var_type, instantiation)
                impl_var_type = var_type  # TODO: ?
                block_var = blocks.Block.Temporary(block, variable.name.id_str, var_type, impl_var_type=impl_var_type)
                if hasattr(var_type, 'block_var'):
                    raise Exception("hasattr(var_type, 'block_var')")
                copy_attributes(block_var, variable)
                variable.block_var = block_var
            else:
                raise Exception(f'Got unexpected variable: {variable}')

        have_native = self.is_native
        for procedure in self.procedures.values():
            have_native = have_native or procedure.is_native
        native_class = None
        if have_native:
            # TODO: Should this be done here? This will be called if 'self' is used in another block, even if that block is not instantiated. Probably looking up the native block type should be delayed until then to avoid loading the native module needed for that. Alternatively, the native class should be looked up even if 'self' is not used at all to do proper error checking.
            native_class = compiler_native.get_native_block_type(self)
            if self.is_native and not native_class.native_block_has_native_state:
                raise blocks.ProgramError(self, f'Node type {self.fullname!r} requires has_native_state=True')
            if not self.is_native and native_class.native_block_has_native_state:
                raise blocks.ProgramError(self, f'Node type {self.fullname!r} requires has_native_state=False')
        block.native_class = native_class

        block.initializer_sources = {}
        if self.initializer is not None:
            depends = self.initializer.depends
            for binding, dep in zip(depends.dependencies, depends.get_resolved_dependencies()):
                if isinstance(dep, blocks.BlockInput):
                    source = block.custom_inputs[dep]
                elif isinstance(dep, blocks.Block):
                    source = dep.block_outputs_byname['']
                else:
                    source = dep
                block.initializer_sources[binding.key] = (source, binding)

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
            for binding, dep in zip(procedure.depends.dependencies, procedure.depends.get_resolved_dependencies()):
                # print(type(dep))
                # TODO: Clean up
                if isinstance(dep, blocks.DeclarationTemporaryVariable):
                    input = dep.block_var
                    inputs.append(input)
                    sources[binding.key] = input
                elif isinstance(dep, blocks.BlockInput):
                    input = block.custom_inputs[dep]
                    inputs.append(input)
                    sources[binding.key] = input
                elif isinstance(dep, blocks.Block):
                    input = dep.block_outputs_byname['']
                    inputs.append(input)
                    sources[binding.key] = input
                elif isinstance(dep, blocks.StateRead):
                    input = blocks.StateRead(state_variables[dep.state.name.id_str], dep.phase, dep.reference)
                    inputs.append(input)
                    sources[binding.key] = input
                elif isinstance(dep, (blocks.Block.Temporary, blocks.Block.Output)):
                    inputs.append(dep)
                    sources[binding.key] = dep
                else:
                    raise Exception(f'Got unknown dependency: {dep}')
            outputs = []
            for binding, out in zip(procedure.produces.outputs, procedure.produces.get_resolved_outputs()):
                # print(type(out))
                if isinstance(out, blocks.ast.DeclarationStateVariable):
                    output = out.block_var
                    outputs.append(output)
                    targets[binding.key] = output
                elif isinstance(out, blocks.ast.DeclarationTemporaryVariable):
                    output = out.block_var
                    outputs.append(output)
                    targets[binding.key] = output
                elif isinstance(out, blocks.BlockOutput):
                    if not procedure.is_native and isinstance(out.conn_spec, blocks.ConnectorSpecificationResource):
                        raise blocks.ProgramError(binding, 'Procedures cannot produce resources')
                    # TODO: Clean up
                    output = block.custom_outputs[out]
                    outputs.append(output)
                    targets[binding.key] = output
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
                    # # TODO: This is a hack to avoid a "Input graph1.Delay.default output. is not connected" exception, this should not be needed because graph1.Delay (without instantiation) should not be used for anything
                    # out.inp.connected_source = temp
                else:
                    raise Exception(f'Unknown output: {out}')

            static_context = blocks.StaticProcedureContext(sources=sources, targets=targets, name=block.name, block_parameter_values={key.name.id_str: value for key, value in block.par_values.items()})

            if not procedure.is_native:
                run = None
            else:
                compiler_native._validate_native_procedure(
                    native_class, procedure, has_native_state=self.is_native,
                )

                def run(*, context, native_state, native_class=native_class,
                        name=procedure.python_name, has_native_state=self.is_native):
                    target = native_state if has_native_state else native_class
                    getattr(target, name)(context=context)
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
