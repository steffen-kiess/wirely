import typing
from collections.abc import Collection, Mapping

if typing.TYPE_CHECKING:
    from .parameters import StaticValue
    from .extensions import CompilerNativeExtension
    from ..runtime.extensions import RuntimeNativeExtension

from .attributes import copy_attributes
from .types import check_runtime_type, resolve_type
from .stages import ResolutionState

from wirely.compiler import model


class NodeType(model.Scope):
    # Set on the global node type by the compiler.
    file_metadata: typing.Any
    native_extension: 'RuntimeNativeExtension'
    compiler_native_extension: 'CompilerNativeExtension'
    # The instance of a singleton node type, set when it is instantiated.
    singleton_instance: 'model.InstanceNode'

    def __init__(self, declaration: model.DeclarationNodeType, parent_graph: model.Scope | None) -> None:
        super().__init__()
        self.declaration = declaration
        self.parent_graph = parent_graph
        if parent_graph is not None:
            self.services = parent_graph.services
            self.coordinator = parent_graph.coordinator

        self.instance_nodes: dict[str, model.InstanceNode] = {}
        self.procedures: dict[str, model.TypeProcedure] = {}
        self.node_declarations: dict[str, model.ChildNode] = {}
        # Nodes, named ports, parameters, and constants share names.
        self.name_declarations: dict[str, model.Declaration] = {}
        self.initializer: model.TypeInitializer | None = None
        self.value_nodes: dict[str, model.DeclarationState | model.DeclarationBinding] = {}
        # TODO: Clarify what is the key here (name or input_name)
        self.input_ports: dict[str, model.TypeInputPort] = {}
        self.output_ports: dict[str, model.TypeOutputPort] = {}
        self.state_nodes: dict[str, model.StateNode] = {}
        self.temporary_nodes: dict[str, model.TemporaryNode] = {}
        self.local_resources: dict[str, model.LocalResource] = {}
        self.assertions: list[model.DeclarationAssertion] = []
        self.interface_state = ResolutionState.PENDING
        self.instance_declarations_state = ResolutionState.PENDING
        self.body_state = ResolutionState.PENDING

    @property
    def name(self) -> model.Identifier | None:
        return self.declaration.name

    @property
    def alias(self) -> model.StringConstant | None:
        """The native name replacing the qualified name."""
        return self.declaration.alias

    @property
    def declarations(self) -> tuple[model.Declaration, ...]:
        return self.declaration.declarations

    @property
    def is_native(self) -> bool:
        return self.declaration.is_native

    @property
    def is_singleton(self) -> bool:
        return self.declaration.is_singleton

    def collect_declarations(self) -> None:
        self.coordinator.node_types.append(self)
        self.coordinator.declaration_groups.append((self, self.declarations))

        if self.parent_graph is not None:
            with self.coordinator.contain(None):
                self.declaration.register(self.parent_graph, self)

        parsed_parameters: dict[str, model.NodeParameter] = {}
        parsed_constants: dict[str, model.NodeParameter] = {}
        static_declarations: dict[str, model.DeclarationParameter] = {}
        for decl in self.declarations:
            with self.coordinator.contain(decl):
                decl.register_interface_declarations(self)

            # TODO: Clean up isinstance?
            if not isinstance(decl, model.DeclarationParameter) or self.coordinator.has_failed(decl):
                continue
            with self.coordinator.contain(decl), decl.context:
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
                model.ast.register_name(self, decl.name, decl)
                parameter = model.NodeParameter(decl, self)
                if parameter.is_constant:
                    parsed_constants[name] = parameter
                else:
                    parsed_parameters[name] = parameter
        if hasattr(self, 'parsed_parameters'):
            raise Exception("hasattr(self, 'parsed_parameters')")
        self.parsed_parameters = parsed_parameters
        self.parsed_constants = parsed_constants

        for decl in self.declarations:
            if isinstance(decl, model.Declaration) and not self.coordinator.has_failed(decl):
                with self.coordinator.contain(decl), decl.context:
                    decl.collect_declarations(self)

    def prepare_type_interface(self) -> None:
        if self.interface_state is ResolutionState.COMPLETE:
            return
        if self.interface_state is not ResolutionState.PENDING:
            raise model.ProgramError(self.declaration, f'Cannot resolve interface of {self.fullname!r}')
        self.interface_state = ResolutionState.RESOLVING
        try:
            for parameter in self.static_declarations.values():
                with self.coordinator.contain(parameter.declaration):
                    parameter.resolve_type()
            for declaration in self.declarations:
                if (isinstance(declaration, (model.DeclarationNodeType, model.DeclarationLibrary))
                        or self.coordinator.has_failed(declaration)):
                    continue
                with self.coordinator.contain(declaration), declaration.context:
                    declaration.prepare_interface(self)
        except Exception:
            self.interface_state = ResolutionState.FAILED
            raise
        self.interface_state = ResolutionState.COMPLETE

    def resolve_instance_declarations(self) -> None:
        if self.instance_declarations_state is ResolutionState.COMPLETE:
            return
        if self.instance_declarations_state is not ResolutionState.PENDING:
            raise RuntimeError('Cannot reuse unfinished instance declaration resolution')
        if self.interface_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Instance declarations require a prepared interface')
        self.instance_declarations_state = ResolutionState.RESOLVING
        try:
            for child in self.node_declarations.values():
                with self.coordinator.contain(child.declaration), child.declaration.context:
                    child.resolve_arguments()
        except Exception:
            self.instance_declarations_state = ResolutionState.FAILED
            raise
        self.instance_declarations_state = ResolutionState.COMPLETE

    def bind_type_body(self) -> None:
        if self.body_state is not ResolutionState.PENDING:
            raise RuntimeError('Body binding requires an unbound body')
        if self.instance_declarations_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Body binding requires resolved instance declarations')
        self.body_state = ResolutionState.RESOLVING
        try:
            self._bind_body()
        except Exception:
            self.body_state = ResolutionState.FAILED
            raise

    def validate_body(self) -> None:
        if self.body_state is not ResolutionState.RESOLVING:
            raise RuntimeError('Body validation requires a bound body')
        try:
            model.ast.validate_nodes(self)
        except Exception:
            self.body_state = ResolutionState.FAILED
            raise
        self.body_state = ResolutionState.COMPLETE

    def _bind_body(self) -> None:
        reference_declarations = []
        connections = []
        for decl in self.declarations:
            if isinstance(decl, (model.DeclarationProcedure, model.DeclarationInitializer)):
                reference_declarations.append(decl)
                continue
            if isinstance(decl, model.DeclarationConnection):
                connections.append(decl)
                continue
            if isinstance(decl, (model.DeclarationNodeType, model.DeclarationLibrary, model.DeclarationInstance)):
                continue
            if isinstance(decl, model.DeclarationParameter):
                parameter = self.static_declarations.get(decl.name.id_str)
                # A duplicate declaration has no parameter.
                if parameter is not None and parameter.declaration is decl:
                    with decl.context:
                        parameter.bind_body()
                continue
            if isinstance(decl, model.Declaration):
                with decl.context:
                    decl.bind_body(self)
        # Initializers, procedures, and connections can reference later nodes.
        for decl in reference_declarations:
            with decl.context:
                decl.bind_body(self)
        for decl in connections:
            with decl.context:
                decl.bind_body(self)

    def create_instance_interface(self, *, name: str, graph: 'NodeType',
                                  par_values: 'Mapping[model.NodeParameter, StaticValue]',
                                  instantiation: 'model.ast.SourceElement', register: bool = True,
                                  failed_parameters: Collection[model.NodeParameter] = ()) -> 'model.InstanceNode':
        if self.interface_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Instance creation requires a prepared interface')
        node = model.InstanceNode(name=name, graph=graph, node_type=self, par_values=par_values,
                             instantiation=instantiation, register=register)
        # TODO: Remove
        node.parameter_values = dict(par_values)

        # Parameters without a value because of an earlier error are not reported again.
        failed = failed_parameters
        # Port types are not checked again without all parameter values.
        incomplete = bool(failed)
        for par in self.parsed_parameters.values():
            # print(par, par.is_optional)
            if not par.is_optional and par not in node.parameter_values:
                incomplete = True
                if par in failed or self.coordinator.has_failed(par.declaration):
                    continue
                with self.coordinator.contain(None):
                    raise model.ProgramError(
                        instantiation, f'Missing value for parameter {par.name.id_str!r} of node {name!r}',
                        hints=[model.CompilerHint(par.declaration, 'Parameter was declared here')])

        # TODO: Pass list of connections to constructor instead of getting them from self.graph
        node.custom_inputs = {}
        node.custom_outputs = {}
        for input in self.input_ports.values():
            # TODO
            # if not isinstance(input.port_spec, model.PortTypeSpecificationData):
            #     continue
            # TODO: Is the name of the node and the name of the input always the same?
            if input.var_type is not None and not incomplete:
                with self.coordinator.contain(None):
                    check_runtime_type(resolve_type(input.var_type, node.par_values), instantiation)
            new_input = model.InstanceInputPort(node, input.input_name, input.is_list, input.var_type, type_input_port=input)
            node.custom_inputs[input] = new_input
        for output in self.output_ports.values():
            # TODO
            # if not isinstance(output.port_spec, model.PortTypeSpecificationData):
            #     continue
            # TODO: Is the name of the node and the name of the output always the same?
            if output.var_type is not None and not incomplete:
                with self.coordinator.contain(None):
                    check_runtime_type(resolve_type(output.var_type, node.par_values), instantiation)
            new_output = model.InstanceOutputPort(node, output.output_name, output.var_type, type_output_port=output)
            node.custom_outputs[output] = new_output

        node.population_state = ResolutionState.PENDING
        return node

    def populate_instance(self, node: 'model.InstanceNode') -> None:
        if self.body_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Instance population requires a completed body')
        if node.node_type is not self or node.population_state is not ResolutionState.PENDING:
            raise RuntimeError('Instance population requires an unpopulated instance of this type')
        node.population_state = ResolutionState.RESOLVING
        try:
            self._populate_instance(node)
        except Exception:
            node.population_state = ResolutionState.FAILED
            raise
        node.population_state = ResolutionState.COMPLETE

    def _populate_instance(self, node: 'model.InstanceNode') -> None:
        instantiation = node.instantiation
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
                copy_attributes(storage, variable, self.coordinator.attributes(variable))
                state_variables[variable.name.id_str] = storage
            elif isinstance(variable, model.ast.DeclarationBinding):
                var_type = self.resolve_type(variable.var_type, par_values=node.parameter_values)
                check_runtime_type(var_type, instantiation)
                impl_var_type = (model.types.TypeOption(var_type)
                                 if model.connection_kind(variable) == model.ConnectionKind.Event else var_type)
                temporary = model.InstanceTemporary(node, variable.name.id_str, var_type, impl_var_type=impl_var_type)
                temporary.port_spec = variable.port_spec
                copy_attributes(temporary, variable, self.coordinator.attributes(variable))
                temporaries[variable.name.id_str] = temporary
            else:
                raise Exception(f'Got unexpected variable: {variable}')

        # Each procedure output port writes its state, or else its own temporary.
        node.port_storage = {}
        for procedure in self.procedures.values():
            for key, port in procedure.output_ports.items():
                if port.state is not None:
                    node.port_storage[port] = state_variables[port.state.name]
                    continue
                assert port.var_type is not None
                impl_var_type = port.var_type
                if model.connection_kind(port) == model.ConnectionKind.Event:
                    impl_var_type = model.types.TypeOption(port.var_type)
                temporary = model.InstanceTemporary(node, f'{procedure.name.id_str}.{key}', port.var_type,
                                                   impl_var_type=impl_var_type, port=port)
                temporary.port_spec = port.port_spec
                node.port_storage[port] = temporary

        for resource in self.local_resources.values():
            model.InstanceLocalResource(node, resource)

        # Data and resource ports map to different instance source classes.
        def source(port: typing.Any) -> typing.Any:
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
            if isinstance(port, (model.InstanceOutputPort, model.StaticSource)):
                return port
            if isinstance(port, model.LocalResource):
                return node.local_resources[port.name]
            raise Exception(f'Got unknown source: {port}')

        for name, temporary in temporaries.items():
            temporary.alias_of = source(self.temporary_nodes[name].connected_source)
        for name, resource in self.local_resources.items():
            if resource.connected_source is not None:
                node.local_resources[name].alias_of = source(resource.connected_source)
        for name, state in self.state_nodes.items():
            if state.copy_source is not None:
                state_variables[name].copy_source = source(state.copy_source)

        node.initializer_sources = {}
        if self.initializer is not None:
            depends = self.initializer.depends
            source_port: typing.Any  # Data and resource dependencies use different source classes.
            for dependency_port, dep in zip(depends.dependencies, self.initializer.resolved_dependencies()):
                if isinstance(dep, model.TypeInputPort):
                    source_port = node.custom_inputs[dep]
                elif isinstance(dep, model.InstanceNode):
                    source_port = dep.output_ports_byname['']
                elif isinstance(dep, model.LocalResource):
                    source_port = source(dep)
                else:
                    source_port = dep
                node.initializer_sources[dependency_port.key] = (source_port, dependency_port)

        # TODO: Probably more stuff should be done at an earlier stage
        for procedure in self.procedures.values():
            sources = {}
            for key, input_port in procedure.input_ports.items():
                if input_port.connected_source is not None:
                    sources[key] = source(input_port.connected_source)
                else:
                    sources[key] = model.PortSources(
                        node, f'{procedure.name.id_str}.{key}',
                        [source(member) for member in input_port.sources], port_spec=input_port.port_spec,
                    )
            targets = {key: node.port_storage[port] for key, port in procedure.output_ports.items()}

            static_context = model.StaticProcedureContext(sources=sources, targets=targets, name=node.name, node_parameter_values={key.name.id_str: value for key, value in node.par_values.items()})

            model.InstanceProcedure(
                node, list(sources.values()), list(targets.values()), name=procedure.name.id_str,
                static_context=static_context, declaration_procedure=procedure.declaration,
            )

    def __repr__(self) -> str:
        return self.fullname

    @property
    def fullname(self) -> str:
        if self.name is None and self.parent_graph is None:
            return '<global>'
        assert self.name is not None and self.parent_graph is not None
        if self.parent_graph.name is None:
            return self.name.id_str
        else:
            return self.parent_graph.fullname + '.' + self.name.id_str

    @property
    def qualified_name(self) -> str:
        """The dotted Wirely name, such as 'ABB.JRA.Blind'."""
        return '.'.join(self.fullname_tuple)

    @property
    def native_name(self) -> str | None:
        """The name that links native state, or None without native state."""
        if not self.is_native:
            return None
        return self.alias.value if self.alias is not None else self.qualified_name

    @property
    def fullname_tuple(self) -> tuple[str, ...]:
        if self.name is None and self.parent_graph is None:
            return ()
        assert self.name is not None and self.parent_graph is not None
        if self.parent_graph.name is None:
            return (self.name.id_str),
        else:
            return self.parent_graph.fullname_tuple + (self.name.id_str,)
