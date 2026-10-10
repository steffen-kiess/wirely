"""Resolved types and diagnostics of programs that may not compile.

analyze() runs the compiler stages up to the creation of child instances and
their ports. It continues after errors in a declaration, instance, or
parameter binding, so the types of the other declarations stay available and
all errors are reported. Connections are not checked; programs with
unconnected inputs or missing sources still resolve.

To preview the ports a node would have, apply the edits adding it with
wirely.compiler.editing in memory and analyze the result.
"""

from dataclasses import dataclass, field
from types import MappingProxyType

from wirely.compiler import model
from . import outline as _outline
from .attributes import validate_files
from .coordinator import CompilerCoordinator
from .diagnostics import Diagnostic
from .native_functions import validate_collisions
from .outline import Constant, Node, NodeType, Parameter, Port, State, Temporary
from .types import resolve_type


@dataclass(frozen=True)
class ElementDiagnostic:
    diagnostic: Diagnostic
    # The ID of the innermost outline element containing the diagnostic, if any.
    element: object


@dataclass(frozen=True)
class ResolvedPort:
    # None for a default port.
    name: str | None
    direction: str
    kind: str
    is_list: bool
    # None if the type did not resolve, or for resources.
    type: object
    resource_type: object


@dataclass(frozen=True)
class ResolvedParameter:
    name: str
    # None if the type did not resolve.
    type: object
    optional: bool
    bound: bool


@dataclass(frozen=True)
class Analysis:
    outline: _outline.Outline
    diagnostics: tuple[ElementDiagnostic, ...]
    _global: object = field(default=None, compare=False, repr=False)

    def ports(self, target):
        """Return the ports of a node with resolved types, or of a node type with declared types.

        It returns None if the node or node type failed to resolve.
        """
        if isinstance(target, Node):
            node = self._instance(target)
            if node is None:
                return None
            return tuple(_resolved_port(port, direction, node.par_values)
                         for direction, ports in (('input', node.custom_inputs), ('output', node.custom_outputs))
                         for port in ports)
        if isinstance(target, NodeType):
            node_type = self._compiled_scope(target.path)
            if node_type is None:
                return None
            return tuple(_resolved_port(port, direction, None)
                         for direction, ports in (('input', node_type.input_ports),
                                                  ('output', node_type.output_ports))
                         for port in ports.values())
        raise TypeError(f'Unsupported port target {target!r}')

    def parameters(self, target):
        """Return the parameters of a node's type, with types resolved for the node."""
        node = self._instance(target)
        if node is None:
            return None
        return tuple(ResolvedParameter(name, _resolve(getattr(parameter, 'type_resolved', None), node.par_values),
                                       parameter.is_optional, parameter in node.par_values)
                     for name, parameter in node.node_type.parsed_parameters.items())

    def value_type(self, target):
        """Return the type of a state, temporary, constant, parameter, or node type port."""
        scope = self._compiled_scope(target.path[:-1])
        name = target.path[-1]
        if scope is None:
            return None
        if isinstance(target, State):
            node = scope.state_nodes.get(name)
        elif isinstance(target, Temporary):
            node = scope.temporary_nodes.get(name)
        elif isinstance(target, (Constant, Parameter)):
            declaration = scope.static_declarations.get(name)
            return getattr(declaration, 'type_resolved', None)
        elif isinstance(target, Port):
            node = (scope.input_ports if target.direction == 'input' else scope.output_ports).get(name)
        else:
            raise TypeError(f'Unsupported value target {target!r}')
        return None if node is None else node.var_type

    def _compiled_scope(self, path):
        if not path:
            return self._global
        records = self.outline.find(NodeType(tuple(path)))
        if len(records) != 1 or not hasattr(records[0]._declaration, 'input_ports'):
            return None
        return records[0]._declaration

    def _instance(self, target):
        records = self.outline.find(target)
        scope = self._compiled_scope(target.path[:-1])
        if len(records) != 1 or scope is None:
            return None
        node = scope.instance_nodes.get(target.path[-1])
        if node is None or node.instantiation is not records[0]._declaration:
            return None
        return node


def analyze(sources, *, add_std=False):
    """Resolve the types of (filename, text) pairs, continuing after errors."""
    files, parsed_files = _outline.parse(sources, add_std=add_std)
    result = _outline.outline_parsed(files, parsed_files)
    diagnostics = [file.error for file in files if file.error is not None]
    declarations = [declaration for _, parsed in parsed_files for declaration in parsed]
    position = model.SourcePosition(filename='<global>', meta=None, input_data='')
    global_node_type = model.NodeType([], None, declarations, source_position=position)
    global_node_type.services = MappingProxyType({})
    coordinator = CompilerCoordinator(global_node_type, diagnostics=diagnostics)
    global_node_type.file_metadata = validate_files(parsed_files, contain=coordinator.contain)
    with coordinator.contain(None):
        validate_collisions(declarations)
    coordinator.compile_interfaces()
    elements = _element_spans(result)
    return Analysis(result, tuple(ElementDiagnostic(diagnostic, _element(elements, diagnostic))
                                  for diagnostic in diagnostics), global_node_type)


def _resolve(value_type, parameter_values):
    if value_type is None or parameter_values is None:
        return value_type
    try:
        return resolve_type(value_type, parameter_values)
    except Exception:
        return None


def _resolved_port(port, direction, parameter_values):
    spec = port.port_spec
    if isinstance(spec, model.PortTypeSpecificationResource):
        kind = 'resource'
    else:
        kind = 'value' if spec.kind_spec.kind == model.ConnectionKind.Value else 'event'
    name = port.name
    return ResolvedPort(None if name == f'default {direction}' else name, direction, kind,
                        spec.list_spec.is_list, _resolve(port.var_type, parameter_values), port.resource_type)


def _element_spans(result):
    elements = []

    def visit(scope):
        for record in (*scope.nodes, *scope.states, *scope.temporaries, *scope.constants,
                       *scope.parameters, *scope.ports, *scope.procedures, *scope.node_types):
            elements.append((record.filename, record.span, record.id))
        for edge in scope.edges:
            if edge.id is not None:
                elements.append((edge.filename, edge.span, edge.id))
        for node_type in scope.node_types:
            visit(node_type.scope)
        for namespace in scope.namespaces:
            visit(namespace)

    visit(result.scope)
    return elements


def _element(elements, diagnostic):
    """Return the ID of the smallest element containing the diagnostic."""
    if diagnostic.start is None:
        return None
    end = diagnostic.start if diagnostic.end is None else diagnostic.end
    matches = [(span[1] - span[0], element) for filename, span, element in elements
               if filename == diagnostic.filename and span[0] <= diagnostic.start and end <= span[1]]
    return min(matches, key=lambda match: match[0])[1] if matches else None
