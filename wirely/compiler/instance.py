import abc
import typing

from wirely.compiler import model
from wirely.util import check_parameters
from .attributes import copy_attributes
from .stages import ResolutionState
from wirely.compiler.parameters import ParameterValues, native_parameters
from wirely.compiler.types import resolve_type, check_runtime_type
from wirely.program import InputSource, Variable

# TODO: Probably InstanceInputPort, InstanceOutputPort, InstanceStateVariable etc. should only exist in NodeType
class InstanceNode(model.GraphElement):
    def __init__(self, graph, *, name, node_type, par_values, instantiation, register=True):
        super().__init__(graph=graph, name=name)

        self.node_type = node_type
        self.par_values = par_values
        self.instantiation = instantiation
        copy_attributes(self, instantiation)
        # TODO: Clean up
        for par in self.par_values:
            val = self.par_values[par]
            if not isinstance(val, model.ValueParameter) and not isinstance(val, model.Value) and not isinstance(val, model.Type):
               raise Exception(f'Got invalid parameter value: {val}')
            # TODO: Also check for contains_parameters? When should this be done?
            # if val.contains_parameters:
            #    raise Exception(f'Got parameter value {val} contains parameters')

        # TODO: A lot of this stuff should be moved to NodeType, instead the InstanceNode should only store the connections to the NodeType's inputs and from the NodeType's outputs
        self.input_ports = []
        self.output_ports = []
        self.input_ports_byname = {}
        self.output_ports_byname = {}
        self.temporaries = []
        self.local_resources = {}
        self.state_variables = []
        self.procedures = []
        self.last_proc_id = 0

        if register:
            if self.name in self.graph.instance_nodes:
                raise Exception(f'Duplicate node name {self.name!r}')
            self.graph.instance_nodes[self.name] = self

    @property
    def is_root(self):
        return self.node_type.parent_graph is None

    @property
    def type_name(self):
        name = self.node_type.name
        return name.id_str if name is not None else '<global>'

    @staticmethod
    def path_name(path, *, qualified=False):
        names = []
        for node in path:
            if isinstance(node, InstanceNode):
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
                     if attribute.name == 'Diagram.Position'), None)

    def validate(self, path):
        """Require a source for each single input and a producer for each output."""
        name = InstanceNode.path_name(path, qualified=True)
        for input in self.all_input_ports:
            if input.connected_source is None and not isinstance(input.node, model.TypeOutputPort):
                port = 'Default input' if input.name == '' else f'Input {input.name!r}'
                raise model.ProgramError(self.instantiation, f'{port} of node {name!r} is not connected',
                                          hints=[model.CompilerHint(input.port_spec, 'Input was declared here')])

        for output in self.output_ports:
            if (isinstance(output.port_spec, model.PortTypeSpecificationResource)
                    or output.type_output_port.connected_source is not None):
                continue
            port = 'Default output' if output.name == '' else f'Output {output.name!r}'
            raise model.ProgramError(output.port_spec, f'{port} has no source', hints=[
                model.CompilerHint(self.instantiation, f'While instantiating {name!r}'),
            ])

    # TODO: Clean up
    @property
    def all_input_ports(self):
        res = []
        for input in self.input_ports:
            if input.is_list:
                # TODO: Currently the individual entries of the list are already in input_ports
                # res += input.members
                pass
            else:
                res.append(input)
        return res

    def specialize(self, *, singletons, variable_map, all_variables, state_map, state_variables, instances, path, parent_instance):
        """Resolve one concrete use of a populated reusable instance."""
        if self.population_state is not ResolutionState.COMPLETE:
            raise RuntimeError('Specialization requires a populated instance')
        if self.node_type.is_singleton:
            if self.node_type in singletons:
                return

            # TODO: This probably should not need an instance of InstanceNode here
            singletons[self.node_type] = self

            # TODO: This is an ugly hack, remove
            if hasattr(self.node_type, 'singleton_instance'):
                raise Exception("hasattr(self.node_type, 'singleton_instance')")
            # print(f'Using {self} as instance for {self.node_type}')
            self.node_type.singleton_instance = self

            path = (self.node_type,)

        if self.node_type.is_singleton:
            instance = model.NodeTypeInstance(self.node_type, parent_instance=None)
        else:
            instance = model.NodeTypeInstance(self, parent_instance=parent_instance)

        if path != instance.path:
            raise Exception(f'path {path} != instance.path {instance.path}')

        if instance.path in instances:
            raise Exception(f'Duplicate path {path}')
        instances[instance.path] = instance
        if not self.is_root:
            self.validate(path)

        for name, child_node in self.node_type.instance_nodes.items():
            child_path = path + (child_node,)
            child_node.specialize(singletons=singletons, variable_map=variable_map, all_variables=all_variables, state_map=state_map, state_variables=state_variables, instances=instances, path=child_path, parent_instance=instance)

        # TODO: Clean up
        # path_prefix = '.'.join([node.name for node in path])
        path_prefix = self.path_name(path)
        if path_prefix:
            path_prefix += '.'
        for state_var in self.state_variables:
            var_type = resolve_type(state_var.var_type, instance.static_values)
            source = (self.node_type.value_nodes[state_var.name]
                      if self.is_root else self.instantiation)
            check_runtime_type(var_type, source)
            v = Variable(path_prefix + state_var.name, var_type)
            state_map[path, state_var] = v
            variable_map[path, state_var.var_identifier] = InputSource(v, new_state=False)
            state_variables.append(v)

    def bind_variables(self, *, path, variable_map, resolve_source):
        for var in self.all_input_ports:
            # A member forwarding a whole multi input is resolved through its members.
            if isinstance(var.port_spec, model.PortTypeSpecificationResource) or model.forwards_multi_input(var):
                continue
            variable_map[path, var.var_identifier] = resolve_source(path, var)
        for var in self.output_ports:
            if isinstance(var.port_spec, model.PortTypeSpecificationResource):
                continue
            variable_map[path, var.var_identifier] = resolve_source(path, var)
        for var in self.temporaries:
            variable_map[path, var.var_identifier] = resolve_source(path, var)

    # TODO: Probably should be merged into specialize()
    def get_procedure_paths(self, prefix):
        path = model.path_resolve_singleton(prefix + (self,))
        res = [(path, proc) for proc in self.procedures]
        for child_node in self.node_type.instance_nodes.values():
            res += child_node.get_procedure_paths(path)
        return res

    # TODO: Probably should be merged into specialize()
    def get_all_node_paths(self, prefix):
        path = model.path_resolve_singleton(prefix + (self,))
        res = [path]
        for child_node in self.node_type.instance_nodes.values():
            res += child_node.get_all_node_paths(path)
        return res


class InstanceElement(abc.ABC):
    def __init__(self, node: 'InstanceNode', name: str):
        if not isinstance(node, InstanceNode):
            raise Exception('node parameter is not an InstanceNode')
        if not isinstance(name, str):
            raise Exception('name parameter is not a str')
        name = str(name)

        self.node = node
        self.name = name

    def __repr__(self):
        return f"{self.node}.{self.name}"


class InstanceSource(InstanceElement, abc.ABC):
    @property
    @abc.abstractmethod
    def temporary(self):
        ...


class InstanceTarget(InstanceElement, abc.ABC):
    pass


# TODO: Handle var_type == None better
# TODO: Clean up, move more functionality to TypeInputPort
class InstanceInputPort(InstanceSource):
    def __init__(self, node: 'InstanceNode', name: str, is_list: bool, var_type: model.types.Type, *, type_input_port: model.TypeInputPort):
        super().__init__(node, name)
        if not isinstance(is_list, bool):
            raise Exception('is_list parameter is not of type bool')
        if var_type is not None and not isinstance(var_type, model.types.Type):
            raise Exception('var_type parameter is not a Type')

        self.is_list = bool(is_list)
        self.var_type = var_type
        self.type_input_port = type_input_port
        copy_attributes(self, type_input_port)

        self.port_spec = self.type_input_port.port_spec
        self.resource_type = self.type_input_port.resource_type

        self.impl_var_type: model.types.Type | None
        if isinstance(self.port_spec, model.PortTypeSpecificationData):
            if self.port_spec.kind_spec.kind == model.ConnectionKind.Value:
                self.impl_var_type = self.var_type
            elif self.port_spec.kind_spec.kind == model.ConnectionKind.Event:
                self.impl_var_type = model.types.TypeOption(self.var_type)
            else:
                raise Exception(f'Unknown ConnectionKind: {self.port_spec.kind_spec.kind}')
        else:
            # TODO: ?
            self.impl_var_type = None

        if self.is_list:
            self.members: list[InstanceInputPort] = []
        else:
            self.connected_source = None

        self.node.input_ports.append(self)
        if self.name in self.node.input_ports_byname:
            raise Exception(f'Duplicate input name {name!r}')
        self.node.input_ports_byname[self.name] = self

    def __repr__(self):
        return f"{self.node}.{self.name}"

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
        if not isinstance(output, (InstanceOutputPort, model.TypeInputPort, model.StateNode, model.TemporaryNode, model.StateRead, model.ProcedureOutputPort,
                                   model.StaticSource, model.LocalResource)):
            raise Exception('output parameter value is not a data or resource source')
        if self.is_list:
            # TODO: Clean up
            # TODO: What should be used for type_input_port here?
            input = InstanceInputPort(self.node, f'{self.name}.{len(self.members)}', False, self.var_type, type_input_port=self)
            input.connect(output)
            self.members.append(input)
        else:
            if self.connected_source is not None:
                raise Exception(f'{self} is already connected to {self.connected_source}, trying to connect to {output}')

            self.connected_source = output

    # TODO: Remove and replace with self, InstanceInputPort should contain a reference to NodeType, not to InstanceNode
    @property
    def var_identifier(self):
        return 'INP ' + self.node.type_name + '.' + self.name


# TODO: Handle var_type == None better
# TODO: Clean up, move more functionality to TypeOutputPort
class InstanceOutputPort(InstanceTarget):
    def __init__(self, node: 'InstanceNode', name: str, var_type: model.types.Type, input_dependencies=[], *, type_output_port: model.TypeOutputPort):
        super().__init__(node, name)
        if var_type is not None and not isinstance(var_type, model.types.Type):
            raise Exception('var_type parameter is not a Type')

        self.var_type = var_type
        self.type_output_port = type_output_port
        copy_attributes(self, type_output_port)

        self.port_spec = self.type_output_port.port_spec
        self.resource_type = self.type_output_port.resource_type

        self.impl_var_type: model.types.Type | None
        if isinstance(self.port_spec, model.PortTypeSpecificationData):
            if self.port_spec.kind_spec.kind == model.ConnectionKind.Value:
                self.impl_var_type = self.var_type
            elif self.port_spec.kind_spec.kind == model.ConnectionKind.Event:
                self.impl_var_type = model.types.TypeOption(self.var_type)
            else:
                raise Exception(f'Unknown ConnectionKind: {self.port_spec.kind_spec.kind}')
        else:
            # TODO: ?
            self.impl_var_type = None

        self.input_dependencies = []
        for dependency in input_dependencies:
            if not isinstance(dependency, InstanceInputPort):
                raise Exception('input_dependencies parameter value is not an Input instance')
            if dependency.node != self.node:
                raise Exception('input_dependencies parameter value is for another node')
            self.input_dependencies.append(dependency)

        self.node.output_ports.append(self)
        if self.name in self.node.output_ports_byname:
            raise Exception(f'Duplicate output name {name!r}')
        self.node.output_ports_byname[self.name] = self

    @property
    def name_desc(self):
        if self.name == '':
            return 'default output'
        else:
            return self.name

    def __repr__(self):
        return f"{self.node}.{self.name}"

    # TODO: Remove and replace with self, InstanceOutputPort should contain a reference to NodeType, not to InstanceNode
    @property
    def var_identifier(self):
        return 'OUT ' + self.node.type_name + '.' + self.name


# TODO: Allow both temporaries and output values to be references to other values
class InstanceTemporary(InstanceSource, InstanceTarget):
    def __init__(self, node: 'InstanceNode', name: str, var_type: model.types.Type, *, impl_var_type, port=None):
        super().__init__(node, name)
        # The procedure output port stored here, or None for declared temporaries.
        self.port = port
        # The source a value or event binding refers to.
        self.alias_of = None
        if not isinstance(var_type, model.types.Type):
            raise Exception('var_type parameter is not a Type')

        self.var_type = var_type

        self.impl_var_type = impl_var_type

        self.producer = None

        self.node.temporaries.append(self)

    def __repr__(self):
        return f"{self.node}.{self.name}"

    @property
    def temporary(self):
        return self

    # TODO: Remove and replace with self, InstanceTemporary should contain a reference to NodeType, not to InstanceNode
    @property
    def var_identifier(self):
        separator = '.' if self.port is None else ':'
        return 'TEMP ' + self.node.type_name + separator + self.name


class PortSources(InstanceSource):
    """The sources connected to a multi procedure port."""

    def __init__(self, node: 'InstanceNode', name: str, members, *, port_spec):
        super().__init__(node, name)
        self.members = members
        self.port_spec = port_spec
        self.is_list = True

    @property
    def temporary(self):
        raise Exception('Trying to get temporary for list')


class InstanceStateVariable(InstanceSource, InstanceTarget):
    def __init__(self, node: 'InstanceNode', name: str, var_type: model.types.Type, initial_value):
        super().__init__(node, name)
        if not isinstance(var_type, model.types.Type):
            raise Exception('var_type parameter is not a Type')

        self.var_type = var_type
        self.initial_value = initial_value

        self.impl_var_type = self.var_type

        self.producer = None
        # The source copied into this state, or None if a procedure port stores it.
        self.copy_source = None

        self.node.state_variables.append(self)

    def __repr__(self):
        return f"StateVariable[{self}]"

    def __str__(self):
        return f"{self.node}.{self.name}"

    # TODO
    @property
    def temporary(self):
        return self

    @property
    def var_identifier(self):
        return 'STATE ' + self.node.type_name + '.' + self.name


class InstanceProcedure:
    # TODO: Make name mandatory
    def __init__(self, node: 'InstanceNode', dependencies, outputs, can_trigger=False, name=None, *, static_context, declaration_procedure):
        if not isinstance(node, InstanceNode):
            raise Exception('node parameter is not an InstanceNode')

        self.node = node
        self.name = name
        self.declaration_procedure = declaration_procedure
        copy_attributes(self, declaration_procedure)
        # TODO: Is this a complete static context? Probably not because the context might also depend on the parameters to the current node. Move this somewhere else?
        self.static_context = static_context

        self.node.last_proc_id += 1
        self.id = self.node.last_proc_id

        self.dependencies = []
        for dependency in dependencies:
            if not isinstance(dependency, (InstanceSource, InstanceOutputPort, model.StateRead, model.StaticSource,
                                           model.InstanceLocalResource)):
                raise Exception('dependencies parameter value is not an Input or Temporary instance')
            # Old-state dependencies have no producer edge; new-state reads depend on the writer.
            # TODO: Allow dependencies on other nodes? Probably yes. Maybe check where dependency.node is a child of the current node?
            # if dependency.node != self.node:
            #     raise Exception('dependencies parameter value is for another node')
            self.dependencies.append(dependency)

        self.outputs = []
        for output in outputs:
            if not isinstance(output, (InstanceTemporary, InstanceStateVariable)):
                raise Exception(f'outputs parameter value is not a Temporary or StateVariable instance: {type(output)}')
            if output.node != self.node:
                raise Exception('outputs parameter value is for another node')
            self.outputs.append(output)

            # TODO:  whether dependencies of output include required inputs as dependencies

            # TODO: Clean up temporary / output stuff
            previous = output.temporary.producer
            if previous is not None:
                target = f'Variable {output.name!r}'
                raise model.ProgramError(
                    declaration_procedure, f'{target} is already produced by procedure {previous.name!r}',
                    hints=[model.CompilerHint(previous.declaration_procedure, 'Previous producer was here')])
            # print(f'Setting output for {output} / {output.temporary}')
            output.temporary.producer = self

        self.can_trigger = can_trigger

        self.node.procedures.append(self)

    def __repr__(self):
        if self.name is not None:
            return f"{self.node}.{self.name}"
        else:
            return f"{self.node}.proc{self.id}"

    # TODO: Remove and replace with self, InstanceProcedure should contain a reference to NodeType, not to InstanceNode
    @property
    def proc_identifier(self):
        return 'PROC ' + self.node.type_name + '.' + self.name


# A single instance of a node. A node might have multiple instances if the node
# type containing it is instantiated multiple times.
# TODO: Is this used for something except NodeTypeInstance?
class NodeInstance:
    @check_parameters
    def __init__(self, node: typing.Optional[model.GraphElement], parent_instance):
        self.node = node

        if parent_instance is not None and not isinstance(parent_instance, NodeTypeInstance):
            raise Exception('parent_instance is not None and not isinstance(parent_instance, NodeTypeInstance)')
        self.parent_instance = parent_instance


class NodeTypeInstance(NodeInstance):
    @check_parameters
    def __init__(self, node, parent_instance):
        if isinstance(node, model.InstanceNode):
            super().__init__(node, parent_instance=parent_instance)

            self.is_singleton = False

            self.node_type = self.node.node_type
        elif isinstance(node, model.NodeType):
            if parent_instance != None:
                raise Exception('parent_instance != None')
            # TODO: Clean up? Create separate type?
            super().__init__(None, parent_instance=None)

            self.is_singleton = True

            self.node_type = node
        else:
            raise Exception(f'Unknown value for node: {node}')

        if self.is_singleton:
            self.parameter_values = {}
        else:
            self.parameter_values = dict(self.node.par_values)

        all_par_values = {}
        next_parent = self.parent_instance
        while next_parent is not None:
            for key, value in next_parent.static_values.items():
                all_par_values.setdefault(key, value)
            next_parent = next_parent.parent_instance

        all_par_values.update(self.parameter_values)
        all_par_values = ParameterValues(all_par_values, instance=self)
        parameter_values_resolved = {}
        parameter_keys = list(self.parameter_values)
        parameter_keys.extend(
            parameter for parameter in self.node_type.parsed_parameters.values()
            if parameter not in self.parameter_values and parameter.default_value is not None
        )
        for key in parameter_keys:
            if key in all_par_values:
                parameter_values_resolved[key] = all_par_values[key]
        self.parameter_values = parameter_values_resolved
        with self.node_type.coordinator.warnings.instance(self.instantiation_hint()):
            try:
                self.constant_values = {
                    declaration: all_par_values[declaration]
                    for declaration in self.node_type.parsed_constants.values()
                }
            except model.ProgramError as error:
                hint = self.instantiation_hint()
                if hint is not None:
                    error.hints = [*error.hints, hint]
                raise
            self.static_values = ParameterValues({
                key: all_par_values[key] for key in all_par_values if key in all_par_values
            }, instance=self)
            self.static_values.update(self.parameter_values)
            self.static_values.update(self.constant_values)

            for assertion in self.node_type.assertions:
                assertion.evaluate(self)

            from .static_bindings import evaluate_bindings
            initializer = self.node_type.initializer
            self.initializer_values = evaluate_bindings(
                initializer.uses if initializer is not None else None, self,
            )

        parameter_values_bystr = {key.name.id_str: value for key, value in parameter_values_resolved.items()}
        parameters = native_parameters(parameter_values_bystr)
        name = InstanceNode.path_name(self.path, qualified=True) or '<global>'
        self.static_context = model.StaticNodeContext(name=name, parameters=parameters)

    def instantiation_hint(self):
        """Return a hint at the instantiation of this instance, or None for the root."""
        owner = self.node or self.node_type.singleton_instance
        if owner.is_root:
            return None
        name = InstanceNode.path_name(self.path, qualified=True)
        return model.CompilerHint(owner.instantiation, f'While instantiating {name!r}')

    @property
    def path(self):
        if self.is_singleton:
            return (self.node_type,)

        if self.parent_instance is None:
            base_path = ()
        else:
            base_path = self.parent_instance.path
        return base_path + (self.node,)
