"""Procedures, initializers, and their ports and static bindings."""

import typing

from wirely.program import RunStage
from wirely.compiler import model
from wirely.util import check_parameters
from wirely.compiler.types import check_runtime_type

from .base import SourcePosition, SourceElement, CompilerHint, ProgramError, Identifier, Declaration
from .specifiers import PortDirection, PortTypeSpecification, PortTypeSpecificationData, PortTypeSpecificationResource, NativeSpecifier
from .values import StringConstant, Reference
from .nodes import DeclarationState, register_name


class StageDeclaration(SourceElement):
    @check_parameters
    def __init__(self, stage: RunStage, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.stage = stage

    @staticmethod
    def create_input(*, source_position: SourcePosition):
        return StageDeclaration(stage=RunStage.INPUT, source_position=source_position)

    @staticmethod
    def create_default(*, source_position: SourcePosition):
        return StageDeclaration(stage=RunStage.DEFAULT, source_position=source_position)

    @staticmethod
    def create_output(*, source_position: SourcePosition):
        return StageDeclaration(stage=RunStage.OUTPUT, source_position=source_position)


class ReferencePort(SourceElement):
    def __init__(self, reference: Reference, alias: typing.Optional[typing.Union[StringConstant, Identifier]] = None, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.reference = reference
        self.alias = alias

    @property
    def key(self):
        if self.alias is not None:
            return (self.alias.id_str if isinstance(self.alias, Identifier)
                    else self.alias.value)
        names = [identifier.id_str for identifier in self.reference.identifiers]
        if len(names) != 1 or names[0] in ('default input', 'default output'):
            raise ProgramError(self, 'Default ports and complex references require an explicit alias')
        return names[0]

    @property
    def python_key(self):
        return self.key


class TypedPort(SourceElement):
    """A typed procedure port, which connections supply or consume.

    In native procedures, a string alias gives the Python key.
    """

    def __init__(self, name: Identifier, port_spec: PortTypeSpecification,
                 alias: typing.Optional[StringConstant] = None, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.port_spec = port_spec
        self.reference = None
        self.alias = alias

    @staticmethod
    def from_reference(reference: Reference, alias, port_spec, *, source_position):
        names = [identifier.id_str for identifier in reference.identifiers]
        if len(names) != 1 or names[0] in ('default input', 'default output'):
            raise ProgramError(reference, 'Typed port names must be identifiers')
        return TypedPort(reference.identifiers[0], port_spec, alias, source_position=source_position)

    @property
    def key(self):
        return self.name.id_str

    @property
    def python_key(self):
        return self.alias.value if self.alias is not None else self.key


def check_port_keys(ports, *, python=False):
    keys = {}
    for port in ports:
        key = port.python_key if python else port.key
        if key in keys:
            raise ProgramError(port, f'Duplicate port {"key" if python else "name"} {key!r}', hints=[
                CompilerHint(keys[key], 'Previous port was here'),
            ])
        keys[key] = port


class DependsDeclaration(SourceElement):
    @check_parameters
    def __init__(self, *dependencies: SourceElement, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.dependencies = dependencies

        for port in dependencies:
            if isinstance(port, TypedPort) and isinstance(port.port_spec, PortTypeSpecificationResource):
                port.port_spec.check_supported_input()

        self.resolved_dependencies_value = None
        self.resolved_dependencies_value_in_process = False

    def collect_declarations(self, graph, proc):
        self.graph = graph
        self.proc = proc

    # Trigger resolution of dependencies and return them
    def get_resolved_dependencies(self):
        if self.resolved_dependencies_value is not None:
            return self.resolved_dependencies_value
        if self.resolved_dependencies_value_in_process:
            raise Exception('self.resolved_dependencies_value_in_process')
        self.resolved_dependencies_value_in_process = True

        resolved = []
        check_port_keys(self.dependencies)
        for port in self.dependencies:
            dep = port.reference
            if dep.length == 2:
                name, member = [identifier.id_str for identifier in dep.identifiers]
                variable = self.graph.value_nodes.get(name)
                if isinstance(variable, DeclarationState) and member in ('old', 'new'):
                    resolved.append(model.StateRead(variable, member, dep))
                    continue
                child = self.graph.instance_nodes.get(name)
                if child is not None:
                    output = child.output_ports_byname.get(member)
                    if output is not None:
                        resolved.append(output)
                        continue
                raise ProgramError(dep, f'Cannot resolve procedure dependency {dep}')
            if dep.length != 1:
                raise ProgramError(dep, 'Unsupported procedure dependency reference')
            identifier = dep.identifiers[0]
            name = identifier.id_str
            # TODO: Should not be done using nodes
            if name in self.graph.instance_nodes:
                res = self.graph.instance_nodes[name]
            elif name in self.graph.value_nodes:
                res = self.graph.value_nodes[name]
                if isinstance(res, DeclarationState):
                    raise ProgramError(dep, 'State dependencies must explicitly reference .old or .new')
            elif name in self.graph.input_ports:
                res = self.graph.input_ports[name]
            elif name in self.graph.local_resources:
                res = self.graph.local_resources[name]
            else:
                raise ProgramError(dep, f'Could not find dependency')
            resolved.append(res)

        self.resolved_dependencies_value = resolved
        return self.resolved_dependencies_value

    def bind_body(self, graph, proc):
        # Make sure get_resolved_dependencies() is executed at some point
        self.get_resolved_dependencies()


class ProducesDeclaration(SourceElement):
    @check_parameters
    def __init__(self, *outputs: SourceElement, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.outputs = outputs

    def collect_declarations(self, graph, proc):
        self.graph = graph
        self.proc = proc


def dependency_is_resource(dependency):
    if isinstance(dependency, model.InstanceNode):
        dependency = dependency.output_ports_byname.get('')
    return isinstance(getattr(dependency, 'port_spec', None), PortTypeSpecificationResource)


class StaticBinding(SourceElement):
    def __init__(self, expression, alias=None, *, source_position):
        super().__init__(source_position=source_position)
        self.expression = expression
        self.alias = alias

    @property
    def key(self):
        if self.alias is not None:
            return self.alias.id_str if isinstance(self.alias, Identifier) else self.alias.value
        if isinstance(self.expression, Reference) and self.expression.length == 1:
            return self.expression.identifiers[0].id_str
        raise ProgramError(self, 'Static binding requires an alias')


class UsesDeclaration(SourceElement):
    def __init__(self, *bindings, source_position):
        super().__init__(source_position=source_position)
        self.bindings = bindings

    def check(self, scope):
        from ..expressions import compile_constant

        keys = set()
        for binding in self.bindings:
            with binding.context:
                key = binding.key
                if key in keys:
                    raise ProgramError(binding, f'Duplicate static binding key {key!r}')
                keys.add(key)
                expression = binding.expression
                if binding.alias is None:
                    if key not in scope.static_declarations:
                        raise ProgramError(expression, f'Unknown static declaration {key!r}')
                compile_constant(scope, expression, None, {})


class DeclarationInitializer(Declaration):
    def __init__(self, depends: DependsDeclaration, produces: ProducesDeclaration, uses=None, *,
                 source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.depends = depends
        self.produces = produces
        self.uses = uses
        self.output_keys = {}  # Produced resource output or local resource -> key.

    def collect_declarations(self, graph):
        if not isinstance(graph, model.NodeType) or not graph.is_native:
            raise ProgramError(self, 'Native initializer requires a native node type')
        if graph.initializer is not None:
            raise ProgramError(self, 'Duplicate native initializer', hints=[
                CompilerHint(graph.initializer, 'Previous initializer was here'),
            ])
        graph.initializer = self
        self.depends.collect_declarations(graph, self)
        self.produces.collect_declarations(graph, self)

    def bind_body(self, graph):
        for port in self.depends.dependencies:
            if isinstance(port, TypedPort):
                raise ProgramError(port, 'Initializer dependencies must reference resources')
        for port in self.produces.outputs:
            if isinstance(port, TypedPort):
                raise ProgramError(port, 'Initializer outputs must reference resource outputs')
        check_port_keys(self.produces.outputs)
        declarations = {}
        for port in self.produces.outputs:
            with port.context:
                output = port.reference.resolve(graph, PortDirection.Input, 'destination').port
            if not isinstance(output, model.LocalResource) and (
                    not isinstance(output, model.TypeOutputPort) or output.graph is not graph
                    or output.resource_type is None):
                raise ProgramError(port, 'Initializer outputs must be resource outputs or local resources of the node type')
            if output in declarations:
                raise ProgramError(port, 'Duplicate initializer output', hints=[
                    CompilerHint(declarations[output], 'Previous output was here'),
                ])
            declarations[output] = port
            self.output_keys[output] = port.key
        self.depends.bind_body(graph, self)
        if self.uses is not None:
            self.uses.check(graph)
        for port, dep in zip(self.depends.dependencies, self.depends.get_resolved_dependencies()):
            if not dependency_is_resource(dep):
                raise ProgramError(port, 'Initializer dependencies must be resources')


class DeclarationProcedure(Declaration):
    @check_parameters
    def __init__(self, native_spec: typing.Optional[NativeSpecifier], name: Identifier, alias: typing.Optional[StringConstant], stage: StageDeclaration, depends: DependsDeclaration, produces: ProducesDeclaration, uses=None, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.is_native = native_spec is not None
        self.name = name
        self.alias = alias
        self.stage = stage
        self.depends = depends
        self.produces = produces
        self.uses = uses
        self.body = None
        if alias is not None:
            prefix, _, method = alias.value.rpartition(':')
            if not prefix or not method:
                raise ProgramError(alias, "Native procedure names have the form 'Type:procedure'")

        # TODO: Allow this? In which cases? Does this need checks that the outputs are not used in earlier stages?
        # Currently this is used to update a state node after writing to a CSV file. The new value for the state node might depend on the result (success or not) of writing to the file. Probably at least updates to state variables should be allowed in the output stage.
        # Disallowing this would make it possible to save the state before the output stage (and therefore before any results of the current step become externally visible). Or state variables could be saved twice, once before and once after the output stage.
        # if self.stage.stage == RunStage.OUTPUT and len(self.produces.outputs) > 0:
        #     hints = [
        #         CompilerHint(self.depends, 'Output declared here'),
        #     ]
        #     raise ProgramError(self.stage, f'Procedure name {self.name} is declared as output stage but produces an output', hints=hints)

    @staticmethod
    def create_body(name, stage, depends, produces, body, *, source_position):
        procedure = DeclarationProcedure(None, name, None, stage, depends, produces,
                                         source_position=source_position)
        procedure.body = body
        for port in (*depends.dependencies, *produces.outputs):
            if isinstance(port, TypedPort) and port.alias is not None:
                raise ProgramError(port.alias, 'Typed port keys require a native procedure')
            if isinstance(port.alias, StringConstant):
                raise ProgramError(port.alias, 'Procedure aliases must be identifiers')
        return procedure

    def collect_declarations(self, graph):
        name = self.name.id_str
        if name in graph.procedures:
            hints = [
                CompilerHint(graph.procedures[name], 'Previous procedure was here'),
            ]
            raise ProgramError(self, f'Duplicate procedure name {name} in {graph}', hints=hints)
        register_name(graph, self.name, self)
        for previous in graph.procedures.values():
            if self.is_native and previous.is_native and previous.python_name == self.python_name:
                raise ProgramError(self.alias or self.name, f'Duplicate Python procedure name {self.python_name!r}', hints=[
                    CompilerHint(previous.alias or previous.name, 'Previous Python procedure name was here'),
                ])
        graph.procedures[name] = self
        self.graph = graph

        self.depends.collect_declarations(graph, self)
        self.produces.collect_declarations(graph, self)

    @property
    def python_name(self):
        """The method name, which native code also uses for triggers."""
        return self.alias.value.rpartition(':')[2] if self.alias is not None else self.name.id_str

    @property
    def native_name(self):
        """The native procedure name, such as 'Std.OptionSome:run'."""
        if self.alias is not None:
            return self.alias.value
        prefix = self.graph.native_name if self.graph.is_native else self.graph.qualified_name
        return f'{prefix}:{self.name.id_str}'

    def bind_body(self, graph):
        if graph.parent_graph is None and self.is_native:
            raise ProgramError(self, 'Native procedures must belong to a node type')
        self.bind_connections(graph)

    def bind_connections(self, graph):
        # Reference ports connect to the ports they reference.
        for declaration in self.depends.dependencies:
            if isinstance(declaration, ReferencePort):
                port = self.input_ports[declaration.key]
                with declaration.context:
                    model.connect(port.counterpart, port, declaration)
        for declaration in self.produces.outputs:
            if isinstance(declaration, ReferencePort):
                port = self.output_ports[declaration.key]
                if isinstance(port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(declaration, 'Procedures cannot produce resources')
                with declaration.context:
                    model.connect(port, port.counterpart, declaration)
        if self.uses is not None:
            self.uses.check(graph)
        if self.stage.stage == RunStage.INPUT:
            for port in self.input_ports.values():
                if not isinstance(port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(self.stage, f'Procedure name {self.name} is declared as input stage but depends on something else', hints=[
                        CompilerHint(self.depends, 'Dependencies declared here'),
                    ])

    def resolve_port(self, name, port_name, is_input):
        """Resolve a reference to a port, given as procedure and port name."""
        procedure = name.id_str
        if port_name is None:
            raise ProgramError(name, f'Procedure references require a port, as in {procedure}.port')
        ports, others = ((self.input_ports, self.output_ports) if is_input
                         else (self.output_ports, self.input_ports))
        port = ports.get(port_name.id_str)
        if port is None:
            if port_name.id_str in others:
                direction = 'output' if is_input else 'input'
                raise ProgramError(port_name, f'Port {port_name.id_str!r} of procedure {procedure!r} is an {direction}')
            raise ProgramError(port_name, f'Unable to find port {port_name.id_str!r} of procedure {procedure!r}')
        # Reference ports are connected to what they reference and are not addressable.
        if not isinstance(port.declaration, TypedPort):
            raise ProgramError(port_name, f'Port {port_name.id_str!r} of procedure {procedure!r} is not a typed port')
        return port

    def validate_ports(self):
        for port in self.input_ports.values():
            if not port.is_list and port.connected_source is None:
                raise ProgramError(port.declaration, f'Input port {port.name!r} of procedure {self.name.id_str!r} has no source')

    def prepare_interface(self, new_graph):
        check_port_keys(self.depends.dependencies)
        check_port_keys(self.produces.outputs)
        if self.is_native:
            check_port_keys(self.depends.dependencies, python=True)
            check_port_keys(self.produces.outputs, python=True)
        self.input_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=True)
            for declaration in self.depends.dependencies
        }
        self.output_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=False)
            for declaration in self.produces.outputs
        }

    def create_port(self, graph, declaration, *, is_input):
        port_type = model.ProcedureInputPort if is_input else model.ProcedureOutputPort
        if isinstance(declaration, ReferencePort):
            direction = PortDirection.Output if is_input else PortDirection.Input
            purpose = 'dependency' if is_input else 'destination'

            def counterpart():
                with declaration.context:
                    return declaration.reference.resolve(graph, direction, purpose).port
            return port_type(graph, self, declaration, counterpart=counterpart)

        spec = declaration.port_spec
        if spec.list_spec.is_list and not is_input:
            raise ProgramError(spec, 'Got multi specifier for non-input')
        if isinstance(spec, PortTypeSpecificationData):
            var_type = graph.resolve_type(spec.ty)
            check_runtime_type(var_type, spec)
            resource_type = None
        else:
            if not is_input:
                raise ProgramError(declaration, 'Procedures cannot produce resources')
            var_type = None
            resource_type = graph.resolve_resource_type(spec.ty)
        specification = model.PortSpecification(var_type, spec, resource_type, spec.list_spec.is_list)
        return port_type(graph, self, declaration, specification=specification)
