import typing

from .attributes import copy_attributes
from .types import check_runtime_type, resolve_type

from wirely.compiler import model
from wirely.util import check_parameters


class NodeType(model.Scope):
    @check_parameters
    def __init__(self, specifiers: typing.List[model.NodeTypeSpecifier], name: typing.Optional[model.Identifier], declarations: list, *, source_position: typing.Optional[model.SourcePosition]):
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
                raise model.ProgramError(specifier, f'Got duplicate {specifier.specifier_str} specifier')
            specifiers_status[specifier.specifier_str] = True
        self.is_native = specifiers_status['native']
        self.is_singleton = specifiers_status['singleton']

        self.instance_nodes = {}
        # TODO: Remove one of node_type_interfaces / node_type_implementations
        self.node_type_interfaces = {}
        self.node_type_implementations = {}
        self.procedures = {}
        self.node_declarations = {}
        # Nodes and named ports share names.
        self.name_declarations = {}
        self.initializer = None
        self.value_nodes = {}
        # TODO: Clarify what is the key here (name or input_name)
        self.input_ports = {}
        self.output_ports = {}
        self.state_nodes = {}
        self.temporary_nodes = {}
        self.libraries = {}
        self.assertions = []

    @staticmethod
    def make(*args, source_position: typing.Optional[model.SourcePosition]):
        specifiers = args[:-2]
        name = args[-2]
        declarations = args[-1]
        return NodeType(list(specifiers), name, declarations, source_position=source_position)

    def compile_pass1(self, graph):
        if hasattr(self, 'parent_graph'):
            raise Exception("hasattr(self, 'parent_graph')")
        self.parent_graph = graph
        if graph is not None:
            self.services = graph.services

        if self.parent_graph is not None:
            if self.name.id_str in graph.struct_types:
                raise model.ProgramError(self.name, f'Node type {self.name.id_str} conflicts with struct type', hints=[
                    model.CompilerHint(graph.struct_types[self.name.id_str], 'Previous declaration was here'),
                ])
            if self.name.id_str in graph.enum_types:
                raise model.ProgramError(self.name, f'Node type {self.name.id_str} conflicts with enum type', hints=[
                    model.CompilerHint(graph.enum_types[self.name.id_str].declaration, 'Previous declaration was here'),
                ])
            if self.name.id_str in graph.resource_types:
                raise model.ProgramError(self, f'Node type {self.name.id_str} conflicts with resource type', hints=[
                    model.CompilerHint(graph.resource_types[self.name.id_str].declaration, 'Previous resource type was here'),
                ])
            if self.name.id_str in graph.node_type_interfaces:
                hints = [
                    model.CompilerHint(graph.node_type_interfaces[self.name.id_str], 'Previous node type was here'),
                ]
                raise model.ProgramError(self, f'Duplicate node type {self.name!r}', hints=hints)
            graph.node_type_interfaces[self.name.id_str] = self

        # TODO: Merge parsed_parameters and parameters?
        parsed_parameters = {}
        parsed_constants = {}
        static_declarations = {}
        for decl in self.declarations:
            decl.compile_pass1_btd(self)

            # TODO: Clean up isinstance?
            if not isinstance(decl, model.DeclarationParameter):
                continue
            with decl.context:
                is_constant = isinstance(decl, model.DeclarationConstant)
                if self.is_singleton and not is_constant:
                    # TODO: Allow parameters for singleton nodes and create one instance for each parameter combination?
                    raise model.ProgramError(decl, 'Singleton node type cannot have parameters')

                name = decl.name.id_str
                if name in static_declarations:
                    previous = static_declarations[name]
                    kind = 'constant' if is_constant else 'parameter'
                    raise model.ProgramError(decl, f'Duplicate {kind} name {name!r}', hints=[
                        model.CompilerHint(previous, 'Previous declaration was here'),
                    ])
                static_declarations[name] = decl
                (parsed_constants if is_constant else parsed_parameters)[name] = decl
        if hasattr(self, 'parsed_parameters'):
            raise Exception("hasattr(self, 'parsed_parameters')")
        self.parsed_parameters = parsed_parameters
        self.parsed_constants = parsed_constants

        # Former "implementation" part

        for decl in self.declarations:
            if isinstance(decl, model.Declaration):
                with decl.context:
                    decl.compile_pass1(self)

        if self.parent_graph is not None:
            if self.name.id_str in self.parent_graph.node_type_implementations:
                hints = [
                    model.CompilerHint(graph.node_type_implementations[self.name.id_str], 'Previous implementation was here'),
                ]
                raise model.ProgramError(self, f'Duplicate node type implementation for {self.name!r}', hints=hints)
            self.parent_graph.node_type_implementations[self.name.id_str] = self

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
            if isinstance(decl, model.Declaration):
                with decl.context:
                    decl.compile_pass1_5(self)

        # TODO: Remove
        self.parameters_by_name = self.parsed_parameters

    def compile_pass2(self, graph):
        for decl in self.declarations:
            if isinstance(decl, model.Declaration):
                with decl.context:
                    decl.compile_pass2(self)
        # The root graph binds its procedures when compiling the program.
        if self.parent_graph is not None:
            model.ast.validate_nodes(self)

        # TODO: Allow interfaces without implementations?
        # if self.name.id_str not in graph.node_type_implementations:
        #     raise model.ProgramError(self, f'Node type interface for {self.name!r} without implementation')

        # TODO: Validate parameters

    def create(self, *, name, graph, par_values, instantiation, register=True):
        # print('create', name, graph, par_values)
        node = model.InstanceNode(name=name, graph=graph, node_type=self, par_values=par_values,
                             instantiation=instantiation, register=register)
        # TODO: Remove
        node.parameter_values = dict(par_values)

        for par in self.parsed_parameters.values():
            # print(par, par.is_optional)
            if not par.is_optional and par not in node.parameter_values:
                raise model.ProgramError(
                    instantiation, f'Missing value for parameter {par.name.id_str!r} of node {name!r}',
                    hints=[model.CompilerHint(par, 'Parameter was declared here')])

        # TODO: Pass list of connections to constructor instead of getting them from self.graph
        node.custom_inputs = {}
        node.custom_outputs = {}
        for input in self.input_ports.values():
            # TODO
            # if not isinstance(input.port_spec, model.PortTypeSpecificationData):
            #     continue
            # TODO: Is the name of the node and the name of the input always the same?
            if input.var_type is not None:
                check_runtime_type(resolve_type(input.var_type, node.par_values), instantiation)
            new_input = model.InstanceInputPort(node, input.input_name, input.is_list, input.var_type, type_input_port=input)
            node.custom_inputs[input] = new_input
        for output in self.output_ports.values():
            # TODO
            # if not isinstance(output.port_spec, model.PortTypeSpecificationData):
            #     continue
            # TODO: Is the name of the node and the name of the output always the same?
            if output.var_type is not None:
                check_runtime_type(resolve_type(output.var_type, node.par_values), instantiation)
            new_output = model.InstanceOutputPort(node, output.output_name, output.var_type, type_output_port=output)
            node.custom_outputs[output] = new_output

        # TODO: Probably more stuff should be done at an earlier stage
        state_variables = {}
        temporaries = {}
        for variable in self.value_nodes.values():
            if isinstance(variable, model.ast.DeclarationState):
                var_type = self.resolve_type(variable.var_type)
                check_runtime_type(var_type, instantiation)
                storage = model.InstanceStateVariable(
                    node, variable.name.id_str, var_type, variable.initial_value,
                )
                copy_attributes(storage, variable)
                state_variables[variable.name.id_str] = storage
            elif isinstance(variable, model.ast.DeclarationTemporary):
                var_type = self.resolve_type(variable.var_type, par_values=node.parameter_values)
                check_runtime_type(var_type, instantiation)
                impl_var_type = var_type  # TODO: ?
                storage = model.InstanceTemporary(node, variable.name.id_str, var_type, impl_var_type=impl_var_type)
                copy_attributes(storage, variable)
                temporaries[variable.name.id_str] = storage
            else:
                raise Exception(f'Got unexpected variable: {variable}')

        # Each procedure output port writes its state, or else its own temporary.
        node.port_storage = {}
        for procedure in self.procedures.values():
            for key, port in procedure.output_ports.items():
                if port.state is not None:
                    node.port_storage[port] = state_variables[port.state.name]
                    continue
                impl_var_type = port.var_type
                if model.connection_kind(port) == model.ConnectionKind.Event:
                    impl_var_type = model.types.TypeOption(port.var_type)
                temporary = model.InstanceTemporary(node, f'{procedure.name.id_str}.{key}', port.var_type,
                                                   impl_var_type=impl_var_type, port=port)
                temporary.port_spec = port.port_spec
                node.port_storage[port] = temporary

        def source(port):
            """Map a connection source in this type to this instance."""
            if isinstance(port, model.TypeInputPort):
                return node.custom_inputs[port]
            if isinstance(port, model.TemporaryNode):
                return temporaries[port.name]
            if isinstance(port, model.StateRead):
                return model.StateRead(state_variables[port.name], port.phase, port.reference)
            if isinstance(port, model.ProcedureOutputPort):
                storage = node.port_storage[port]
                if isinstance(storage, model.InstanceStateVariable):
                    return model.StateRead(storage, 'new', port.declaration)
                return storage
            if isinstance(port, model.InstanceOutputPort):
                return port
            raise Exception(f'Got unknown source: {port}')

        for name, temporary in temporaries.items():
            temporary.alias_of = source(self.temporary_nodes[name].connected_source)
        for name, state in self.state_nodes.items():
            if state.copy_source is not None:
                state_variables[name].copy_source = source(state.copy_source)

        node.initializer_sources = {}
        if self.initializer is not None:
            depends = self.initializer.depends
            for port, dep in zip(depends.dependencies, depends.get_resolved_dependencies()):
                if isinstance(dep, model.TypeInputPort):
                    source_port = node.custom_inputs[dep]
                elif isinstance(dep, model.InstanceNode):
                    source_port = dep.output_ports_byname['']
                else:
                    source_port = dep
                node.initializer_sources[port.key] = (source_port, port)

        # TODO: Probably more stuff should be done at an earlier stage
        for procedure in self.procedures.values():
            sources = {}
            for key, port in procedure.input_ports.items():
                if port.connected_source is not None:
                    sources[key] = source(port.connected_source)
                else:
                    sources[key] = model.PortSources(
                        node, f'{procedure.name.id_str}.{key}',
                        [source(member) for member in port.sources], port_spec=port.port_spec,
                    )
            targets = {key: node.port_storage[port] for key, port in procedure.output_ports.items()}

            static_context = model.StaticProcedureContext(sources=sources, targets=targets, name=node.name, node_parameter_values={key.name.id_str: value for key, value in node.par_values.items()})

            model.InstanceProcedure(
                node, list(sources.values()), list(targets.values()), name=procedure.name.id_str,
                static_context=static_context, declaration_procedure=procedure,
            )

        # print(node)
        # print(node.input_ports)
        # print(node.output_ports)
        # print(node.all_input_ports)

        return node

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
