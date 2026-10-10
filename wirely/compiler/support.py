from collections.abc import Mapping

from . import model
from .instance import InstanceNode
from .graph import Connection, StateRead, StaticSource
from .types import resolve_type


debug_variable_resolution = False


SOURCE_TYPES = (model.InstanceOutputPort, model.TypeInputPort, model.StateNode, model.TemporaryNode,
                StateRead, model.ProcedureOutputPort, StaticSource)
TARGET_TYPES = (model.InstanceInputPort, model.TypeOutputPort, model.ProcedureInputPort, model.TemporaryNode,
                model.StateNode)


def describe_target(target):
    if isinstance(target, model.InstanceInputPort):
        port = 'Default input' if target.name == '' else f'Input {target.name!r}'
        return f'{port} of node {target.node.name!r}'
    if isinstance(target, model.TypeOutputPort):
        return 'Default output' if target.output_name == '' else f'Output {target.output_name!r}'
    if isinstance(target, (model.ProcedureInputPort, model.ProcedureOutputPort)):
        return target.description[0].upper() + target.description[1:]
    kind = 'State' if isinstance(target, model.StateNode) else 'Temporary'
    return f'{kind} {target.name!r}'


def connection_kind(port):
    spec = getattr(port, 'port_spec', None)
    return model.ConnectionKind.Value if spec is None else spec.kind_spec.kind


def connect(source, target, source_element, *, attributes=()):
    # TODO: Clean up
    if not isinstance(source, SOURCE_TYPES):
        raise Exception('source parameter is not a data or resource source')
    # TODO: Clean up
    if not isinstance(target, TARGET_TYPES):
        raise Exception(f'target parameter is not a connection target: {target!r}')

    if source.node.graph != target.node.graph:
        # TODO: Can this happen from program code? If yes, make error message clearer?
        raise model.ProgramError(source_element, f'source has graph {source.node.graph} but target has graph {target.node.graph}')

    def declaration(port):
        if isinstance(port, model.InstanceInputPort):
            return port.type_input_port.port_spec
        if isinstance(port, model.InstanceOutputPort):
            return port.type_output_port.port_spec
        if isinstance(port, (model.TypeInputPort, model.TypeOutputPort)):
            return port.port_spec
        if isinstance(port, (model.ProcedureInputPort, model.ProcedureOutputPort, StaticSource)):
            return port.declaration
        return port.graph.value_nodes[port.name]

    def incompatible(message):
        raise model.ProgramError(source_element, message, hints=[
            model.CompilerHint(declaration(source), 'Source was declared here'),
            model.CompilerHint(declaration(target), 'Target was declared here'),
        ])

    def previous_connection_hints():
        previous = next((connection.source_element for connection in source.node.graph.connections
                         if connection.target is target), None)
        return [] if previous is None else [model.CompilerHint(previous, 'Previous connection was here')]

    # Single inputs, ports, outputs, and variables take one source.
    is_multi = getattr(target, 'is_list', False) and isinstance(target, (model.InstanceInputPort, model.ProcedureInputPort))
    if is_multi and isinstance(target, model.ProcedureInputPort) and (
            target.connected_source is not None or (getattr(source, 'is_list', False) and target.sources)):
        # A forwarded multi input is the port's only source.
        raise model.ProgramError(source_element, f'{describe_target(target)} cannot combine a multi input with other sources',
                                  hints=previous_connection_hints())
    if not is_multi and target.connected_source is not None:
        message = f'{describe_target(target)} already has a source'
        if isinstance(target, model.InstanceInputPort):
            message += '; only multi inputs accept several'
        raise model.ProgramError(source_element, message, hints=previous_connection_hints())

    def record(kind):
        actual_target = target.members[-1] if isinstance(target, model.InstanceInputPort) and target.is_list else target
        source.node.graph.connections.append(Connection(
            source, actual_target, attributes, source_element, kind,
        ))

    source_resource = isinstance(getattr(source, 'port_spec', None), model.PortTypeSpecificationResource)
    target_resource = isinstance(getattr(target, 'port_spec', None), model.PortTypeSpecificationResource)
    if source_resource or target_resource:
        if not (source_resource and target_resource):
            incompatible('Cannot connect resource and data ports')
        if source.resource_type is not target.resource_type:
            incompatible(f'source has resource type {source.resource_type} but target has resource type {target.resource_type}')
        target.connect(source)
        record('resource')
        return

    source_kind = connection_kind(source)
    target_kind = connection_kind(target)
    if source_kind != target_kind:
        incompatible(f'source has kind {source_kind} but target has kind {target_kind}')

    source_type = source.var_type
    if isinstance(source.node, model.InstanceNode):
        source_type = resolve_type(source_type, source.node.par_values)
    target_type = target.var_type
    if isinstance(target.node, model.InstanceNode):
        target_type = resolve_type(target_type, target.node.par_values)
    if source_type != target_type:
        incompatible(f'source has type {source_type} but target has type {target_type}')

    target.connect(source)
    # A procedure port stores its first state; later states copy it.
    if (isinstance(target, model.StateNode) and isinstance(source, model.ProcedureOutputPort)
            and source.state is None):
        source.state = target
    record('state' if isinstance(source, (model.StateNode, StateRead)) else 'data')


def path_resolve_singleton(path):
    if isinstance(path[-1], model.InstanceNode) and path[-1].node_type.is_singleton:
        return (path[-1].node_type,)
    else:
        return path


def incoming_connection(var):
    """Return the connection supplying var, or None for a lookup step of resolve_variable()."""
    if isinstance(var, model.InstanceInputPort):
        graph, target = var.node.graph, var
    elif isinstance(var, model.InstanceOutputPort):
        graph, target = var.node.node_type, var.type_output_port
    elif isinstance(var, model.InstanceTemporary) and var.port is None:
        graph = var.node.node_type
        target = graph.temporary_nodes[var.name]
    elif isinstance(var, model.TemporaryNode):
        graph, target = var.graph, var
    elif isinstance(var, StateRead) and var.phase == 'new' and var.state.copy_source is not None:
        if isinstance(var.state, model.InstanceStateVariable):
            graph = var.state.node.node_type
            target = graph.state_nodes[var.state.name]
        else:
            graph, target = var.state.graph, var.state
    else:
        return None
    return next((connection for connection in graph.connections if connection.target is target), None)


def traced_connections(trace):
    """Return the connections passed by resolve_variable() steps, in data-flow order."""
    return [connection for connection in map(incoming_connection, reversed(trace)) if connection is not None]


def resolve_variable(path, var, seen=None):
    """Return the variable supplying var; seen collects the steps taken."""
    if debug_variable_resolution:
        print(f'resolve_variable({path}, {var})')

    # Values passed on through connections must not lead back to themselves.
    if seen is None:
        seen = {}
    key = path, id(var)
    if key in seen:
        cycle = list(seen.values())[list(seen).index(key):]
        port = next(item for item in cycle if isinstance(item, model.InstanceInputPort))
        # Data-flow order starting at the reported connection; a node type
        # used several times in the cycle lists its connections once.
        flow = traced_connections(cycle)
        start = flow.index(incoming_connection(port))
        connections = []
        for connection in flow[start:] + flow[:start]:
            if connection not in connections:
                connections.append(connection)
        raise model.ast.circular_connection(connections[0], connections[1:])
    seen[key] = var

    if isinstance(var, StateRead):
        owner_path, state = resolve_variable(path, var.state)
        # A copied state's new value is its source's value.
        if var.phase == 'new' and state.copy_source is not None:
            return resolve_variable(owner_path, state.copy_source, seen)
        return owner_path, StateRead(state, var.phase, var.reference)

    if isinstance(var.node, InstanceNode) and var.node.node_type.is_singleton:
        owner = var.node.node_type.singleton_instance
        if owner is not var.node:
            items = owner.input_ports + owner.output_ports + owner.temporaries + owner.state_variables
            item = next(item for item in items if type(item) is type(var) and item.var_identifier == var.var_identifier)
            return resolve_variable((owner.node_type,), item, seen)

    if isinstance(var, model.InstanceStateVariable):
        return path, var

    if isinstance(path[-1], model.InstanceNode) and path[-1].node_type.is_singleton:
        raise Exception('isinstance(path[-1], model.InstanceNode) and path[-1].node_type.is_singleton')

    if isinstance(path[-1], (InstanceNode, model.NodeType)):
        current_node = path[-1]
        if not isinstance(current_node, InstanceNode):
            current_node = current_node.singleton_instance
        # TODO: Clean this up?
        # print(path, var)
        if current_node == var.node:
            node = var.node
        elif var.node.graph == current_node.node_type:
            # Dependency on the result of a child node
            # TODO: Make this more generic
            # TODO: Modify path here?
            path = path_resolve_singleton(path + (var.node,))
            node = var.node
        else:
            raise Exception(f'Cannot resolve {var} from {current_node}')
        # print(type(var), type(var.node))

        # TODO: This is somewhat of a hack
        if (isinstance(var, model.InstanceOutputPort) or isinstance(var, model.InstanceTemporary)) and isinstance(node, model.TypeInputPort):
            input_name = node.input_name
            new_path = path[:-1]
            parent_node = new_path[-1]
            if not isinstance(parent_node, InstanceNode):
                parent_node = parent_node.singleton_instance
            input_var = parent_node.input_ports_byname[input_name]
            return resolve_variable(path_resolve_singleton(new_path), input_var, seen)

        # TODO: This is somewhat of a hack
        if (isinstance(var, model.InstanceInputPort)) and isinstance(node, model.TypeOutputPort):
            output_name = node.output_name
            new_path = path[:-1]
            parent_node = new_path[-1]
            if not isinstance(parent_node, InstanceNode):
                parent_node = parent_node.singleton_instance
            output_var = parent_node.output_ports_byname[output_name]
            return resolve_variable(path_resolve_singleton(new_path), output_var, seen)

    if isinstance(var, model.InstanceInputPort):
        src = var.connected_source
        if src is None:
            raise Exception('src is None')
        new_path = path[:-1] + (src.node,)
        # print(src, type(src), src.node, new_path)
        return resolve_variable(path_resolve_singleton(new_path), src, seen)

    if isinstance(var, model.InstanceOutputPort):
        source = var.type_output_port.connected_source
        if source is not None:
            return resolve_variable(path_resolve_singleton(path + (source.node,)), source, seen)
        if isinstance(var.port_spec, model.PortTypeSpecificationResource):
            return path, var
        raise Exception(f'Output {var} is not connected')

    if isinstance(var, model.InstanceTemporary):
        temporary = var.temporary
        if temporary != var:
            raise Exception('temporary != var')
        if temporary.node != path[-1]:
            if isinstance(path[-1], model.InstanceNode):  # TODO: Handle singletons here?
                raise Exception(f'temporary.node {temporary.node} != path[-1] {path[-1]}')
        # A declared temporary is another name for its source.
        if temporary.alias_of is not None:
            return resolve_variable(path, temporary.alias_of, seen)
        if temporary.producer is None:
            raise Exception('temporary.producer is None')
        if debug_variable_resolution:
            print(f'resolve_variable: returning {path}, {temporary}')
        return path, temporary

    if isinstance(var, model.TemporaryNode):
        # TODO: Clean up
        if path[-1] != var:
            raise Exception('path[-1] != var')
        node = path[-2]
        if not isinstance(node, InstanceNode):
            node = node.singleton_instance
        var_inst = None
        for t in node.temporaries:
            if t.port is None and t.name == var.name:
                var_inst = t
                break
        if var_inst is None:
            raise Exception(f'Cannot find temporary {var.name!r}')
        return resolve_variable(path[:-1], var_inst, seen)

    if isinstance(var, model.ProcedureOutputPort):
        if path[-1] != var:
            raise Exception('path[-1] != var')
        node = path[-2]
        if not isinstance(node, InstanceNode):
            node = node.singleton_instance
        storage = node.port_storage[var]
        if isinstance(storage, model.InstanceStateVariable):
            # Other consumers of a port writing state read the new value.
            return path[:-1], StateRead(storage, 'new', var.declaration)
        return resolve_variable(path[:-1], storage, seen)

    # TODO: Clean up
    if isinstance(var, model.TypeInputPort):
        if path[-1] != var:
            raise Exception('path[-1] != var')
        input_name = var.input_name
        new_path = path[:-1]
        parent_node = new_path[-1]
        if not isinstance(parent_node, InstanceNode):
            parent_node = parent_node.singleton_instance
        input_var = parent_node.input_ports_byname[input_name]
        return resolve_variable(path_resolve_singleton(new_path), input_var, seen)

    if isinstance(var, StaticSource):
        # The instance owning the declaration supplies its value.
        if path[-1] != var:
            raise Exception('path[-1] != var')
        return path[:-1], var

    # TODO: Clean up
    if isinstance(var, model.StateNode):
        if path[-1] != var:
            raise Exception('path[-1] != var')
        owner_path = path[:-1]
        owner = owner_path[-1]
        if not isinstance(owner, model.InstanceNode):
            owner = owner.singleton_instance
        state = next(state for state in owner.state_variables if state.name == var.name)
        return owner_path, state

    print(f'resolve_variable: Unhandled {path}, {var} {type(var)} {type(var.node)}')

    if debug_variable_resolution:
        print(f'resolve_variable: returning {path}, {var.temporary}')
    # TODO: This code seems to be unused
    # TODO: Avoid using .temporary (should be handled here)
    return path, var.temporary


class StaticNodeContext:
    def __init__(self, *, name, parameters):
        self.parameters: Mapping[str, object] = parameters
        self.name = name

    def __repr__(self):
        return f'StaticNodeContext({self.__dict__!r})'


class StaticProcedureContext:
    def __init__(self, *, sources, targets, name, node_parameter_values, node_context=None):
        self._sources = sources
        self._targets = targets
        self._input_bindings = []
        self._resource_bindings = []
        self._output_types = {}
        self.name = name
        self.node_context = node_context
        self.node_parameter_values = node_parameter_values

    def __repr__(self):
        return f'StaticProcedureContext({self.__dict__!r})'
