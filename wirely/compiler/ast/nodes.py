"""Instances, connections, state, and temporaries."""

import typing

from wirely.compiler.attributes import copy_attributes
from wirely.compiler import model
from wirely.util import check_parameters
from wirely.compiler.types import resolve_type, check_runtime_type, type_parameters

from .base import SourcePosition, SourceElement, CompilerHint, ProgramError, Identifier, Declaration, Value
from .specifiers import PortDirection
from .values import Reference


class ParameterBinding(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, value: Value, *, source_position: SourcePosition, is_optional=False):
        super().__init__(source_position=source_position)
        self.name = name
        self.value = value
        self.is_optional = is_optional

    @staticmethod
    def optional(name, value, *, source_position):
        return ParameterBinding(name, value, source_position=source_position, is_optional=True)

    def __repr__(self):
        return f'Parameter <{self.name}> = <{self.value}>'


        # return f'Parameter <{self.name}> [{self.name.source_position.start_line}] = <{self.value}>'


class Instantiation(SourceElement):
    @check_parameters
    def __init__(self, type_name: Reference, parameters: list,
                 *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.type_name = type_name
        self.parameters = parameters
        self.inner_attributes = getattr(parameters, 'inner_attributes', ())


class DeclarationInstance(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, instantiation: Instantiation, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.instantiation = instantiation
        self.inner_attributes = instantiation.inner_attributes

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.node_declarations:
            raise ProgramError(self.name, f'Duplicate node name {name!r}', hints=[
                CompilerHint(graph.node_declarations[name], 'Previous node was here'),
            ])
        register_name(graph, self.name, self)
        graph.node_declarations[name] = self

    def compile_pass1_5(self, graph):
        name = self.name
        inst = self.instantiation

        self.node_name = name.id_str
        parameter_bindings = inst.parameters

        self.node_type = graph.resolve_node_type(inst.type_name)
        # print(self.node_name, self.node_type, parameter_bindings)

        # The node type may be declared after this node.
        self.node_type.compile_pass1_5_if_needed()

        self.par_values = {}
        seen = set()
        for parameter in parameter_bindings:
            key = parameter.name
            value = parameter.value

            if key.id_str in self.node_type.parsed_constants:
                raise ProgramError(key, f'Cannot override constant {key.id_str!r}')
            if key.id_str not in self.node_type.parameters_by_name:
                raise ProgramError(key, f'Unknown parameter for {self.node_type.name.id_str}: {key}')
            par = self.node_type.parameters_by_name[key.id_str]
            if par in seen:
                raise ProgramError(parameter, f'Duplicate parameter binding {key.id_str!r}')
            seen.add(par)
            if parameter.is_optional:
                if not par.is_optional:
                    raise ProgramError(parameter, 'Optional binding requires an optional or defaulted parameter')
                from ..expressions import compile_constant
                compile_constant(graph, value, model.types.TypeOption(par.type_resolved), {})
                self.par_values[par] = model.value.OptionalBinding(graph, value, par)

        pending = {self.node_type.parameters_by_name[parameter.name.id_str]: parameter
                   for parameter in parameter_bindings if not parameter.is_optional}

        def bind(par):
            parameter = pending.pop(par, None)
            if parameter is None:
                return
            # Bind the parameters this parameter's type refers to first.
            for dependency in type_parameters(par.type_resolved):
                bind(dependency)
            value = parameter.value
            par_type = resolve_type(par.type_resolved, self.par_values)
            # TODO: Is passing par_values=self.par_values here the correct thing to do?
            if par_type == model.types.ValueType:
                val = graph.get_const_value(value, par_type, par_values=self.par_values)
                self.par_values[par] = (val.value if isinstance(val, model.ConstantValue) else val)
            else:
                val = graph.get_const_value(value, par_type, par_values=self.par_values)
                self.par_values[par] = val

        for par in list(pending):
            bind(par)

    def compile_pass2(self, graph):
        node = self.node_type.create(graph=graph, par_values=self.par_values, name=self.node_name, instantiation=self)

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass


class DeclarationConnection(Declaration):
    @check_parameters
    def __init__(self, sources: typing.List[Reference], target: Reference, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.sources = sources
        self.target = target

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        sources = self.sources
        target = self.target

        # print(source_node_name, source_output_name, target_node_name, target_input_name)
        res_sources = [source.resolve(graph, PortDirection.Output, 'source') for source in sources]

        res_target = target.resolve(graph, PortDirection.Input, 'target')

        # TODO: Disallow multiple connect statements to a list input?
        with self.context:
            for source, attributes in zip(res_sources, self.edge_attributes):
                model.connect(source.port, res_target.port, self, attributes=attributes)

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass


class DeclarationState(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, var_type: Value, initial_value: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.var_type = var_type
        self.initial_value = initial_value

        self.name_str = self.name.id_str

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.value_nodes:
            hints = [
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ]
            raise ProgramError(self, f'Duplicate name {name!r}', hints=hints)
        register_name(graph, self.name, self)
        graph.value_nodes[name] = self

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        ty = self.interface.resolve_type(self.var_type)
        check_runtime_type(ty, self)
        # TODO: Resolve initial_value
        variable = model.StateNode(new_graph, ty, name=self.name_str, initial_value=self.initial_value)
        copy_attributes(variable, self)


def validate_nodes(graph):
    """Require a source for each procedure input port and variable."""
    for procedure in graph.procedures.values():
        procedure.validate_ports()
    for name, variable in graph.value_nodes.items():
        is_state = isinstance(variable, DeclarationState)
        node = (graph.state_nodes if is_state else graph.temporary_nodes)[name]
        if node.connected_source is None:
            raise ProgramError(variable, f'{"State" if is_state else "Temporary"} {name!r} has no source')
    for node in (*graph.state_nodes.values(), *graph.temporary_nodes.values()):
        check_alias_cycle(graph, node)


def passed_on_variable(source):
    """Return the temporary or copied state whose value source passes on, if any."""
    if isinstance(source, model.TemporaryNode):
        return source
    if isinstance(source, model.StateRead) and source.phase == 'new' and source.state.copy_source is not None:
        return source.state
    return None


def check_alias_cycle(graph, start):
    """Reject a variable whose value is passed back to it without a procedure."""
    node = start
    seen = set()
    while node is not None and node not in seen:
        seen.add(node)
        node = passed_on_variable(node.copy_source if isinstance(node, model.StateNode) else node.connected_source)
        if node is start:
            connection = next(connection for connection in graph.connections if connection.target is start)
            raise ProgramError(connection.source_element, 'Circular connection')


def register_name(graph, name, declaration):
    """Give nodes and named ports one namespace."""
    previous = graph.name_declarations.get(name.id_str)
    if previous is not None:
        raise ProgramError(name, f'Duplicate name {name.id_str!r}', hints=[
            CompilerHint(previous, 'Previous declaration was here'),
        ])
    graph.name_declarations[name.id_str] = declaration


class DeclarationTemporary(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, var_type: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.var_type = var_type

        self.name_str = self.name.id_str

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.value_nodes:
            hints = [
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ]
            raise ProgramError(self, f'Duplicate name {name!r}', hints=hints)
        register_name(graph, self.name, self)
        graph.value_nodes[name] = self

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        ty = self.interface.resolve_type(self.var_type)
        check_runtime_type(ty, self)
        variable = model.TemporaryNode(new_graph, ty, name=self.name_str)
        copy_attributes(variable, self)
