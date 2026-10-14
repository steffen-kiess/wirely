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
from wirely import types as _types
from . import outline as _outline
from . import support
from .ast import PortDirection
from .attributes import validate_files
from .coordinator import CompilerCoordinator
from .diagnostics import Diagnostic, WarningSink
from .native_functions import validate_collisions, validate_native_names
from .outline import Binding, Constant, Node, NodeType, Parameter, Port, ProcedurePort, State
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
class ConnectionCheck:
    allowed: bool
    # Why the connection is not allowed, as reported by the compiler.
    message: str | None


@dataclass(frozen=True)
class ParameterEntry:
    name: str
    # None if the type did not resolve.
    type: object
    type_text: str
    default_text: str | None
    optional: bool


@dataclass(frozen=True)
class NodeTypeEntry:
    id: NodeType
    # The type as written in a node declaration in the given scope.
    type_text: str
    is_native: bool
    is_singleton: bool
    parameters: tuple[ParameterEntry, ...]
    ports: tuple[ResolvedPort, ...]


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

    def can_connect(self, scope, source, target, *, replacing=None):
        """Check whether a connection from source to target is allowed in a scope.

        References are written as in a connect statement. A replaced
        connection does not count as a source of its target.
        """
        from .editing import _parse_declaration, _parse_references
        source, target = _parse_references(source, target)
        statement = _parse_declaration(f'connect {source} -> {target};')
        graph = self._compiled_scope(scope)
        if graph is None:
            return ConnectionCheck(False, f'Scope {tuple(scope)} did not resolve')
        try:
            source_port = statement.sources[0].resolve(graph, PortDirection.Output, 'source').port
            target_port = statement.target.resolve(graph, PortDirection.Input, 'target').port
            existing = self._sources_of(scope, graph, statement.target, replacing)
            is_multi = (getattr(target_port, 'is_list', False)
                        and isinstance(target_port, (model.InstanceInputPort, model.ProcedureInputPort)))
            if is_multi and isinstance(target_port, model.ProcedureInputPort) and existing and (
                    getattr(source_port, 'is_list', False)
                    or any(getattr(port, 'is_list', False) for port in existing)):
                return ConnectionCheck(False, f'{support.describe_target(target_port)} '
                                              'cannot combine a multi input with other sources')
            if not is_multi and existing:
                return ConnectionCheck(False, support.occupied_message(target_port))
            support.check_compatible(source_port, target_port, statement)
        except model.ProgramError as error:
            return ConnectionCheck(False, str(error))
        except (AttributeError, KeyError, RuntimeError):
            # Ends of failed declarations lack their compiler objects.
            return ConnectionCheck(False, f'{source} -> {target} did not resolve')
        return ConnectionCheck(True, None)

    def connection_candidates(self, scope, *, source=None, target=None):
        """Return the references that can be connected to a source or from a target."""
        if (source is None) == (target is None):
            raise ValueError('Give either source or target')
        graph = self._compiled_scope(scope)
        if graph is None:
            return ()
        if source is not None:
            return tuple(candidate for candidate in _target_references(graph)
                         if self.can_connect(scope, source, candidate).allowed)
        return tuple(candidate for candidate in _source_references(graph)
                     if self.can_connect(scope, candidate, target).allowed)

    def node_types(self, scope=(), *, include_internal=False):
        """Return the node types that nodes in a scope can instantiate."""
        entries = []

        def add(record, type_text, internal):
            internal = internal or _is_internal(record.attributes)
            compiled = record._declaration
            if (internal and not include_internal) or len(self.outline.find(record.id)) != 1 \
                    or getattr(compiled, 'failed', False) or not hasattr(compiled, 'input_ports'):
                return
            parameters = tuple(ParameterEntry(parameter.name, self.value_type(parameter.id), parameter.type_text,
                                              parameter.default_text, parameter.is_optional)
                               for parameter in record.parameters)
            entries.append(NodeTypeEntry(record.id, type_text, record.is_native, record.is_singleton,
                                         parameters, self.ports(record.id)))

        def visit(namespace, internal):
            for record in namespace.node_types:
                add(record, '.'.join(record.id.path), internal)
            for child in namespace.namespaces:
                visit(child, internal or any(_is_internal(_block_attributes(block)) for _, block in child._blocks))

        visit(self.outline.scope, False)
        for length in range(1, len(scope) + 1):
            for enclosing in self.outline.find(NodeType(tuple(scope[:length]))):
                for record in enclosing.scope.node_types:
                    add(record, record.name, False)
        return tuple(sorted(entries, key=lambda entry: entry.type_text))

    def type_names(self, scope=(), *, include_internal=False):
        """Return the names of value types usable in a scope, as written in declarations."""
        names = [name for name in _types.type_by_name if name != 'ValueType'] + ['Option', 'List']
        declared = []

        def add(declarations, prefix, internal):
            for name, declaration in declarations.items():
                declaration = getattr(declaration, 'declaration', declaration)
                if include_internal or not (internal or _is_internal(declaration.attributes)):
                    declared.append(prefix + name)

        def visit(library, prefix, internal):
            for name, child in library.libraries.items():
                child_internal = internal or _is_internal(child.attributes)
                add(child.struct_types, f'{prefix}{name}.', child_internal)
                add(child.enum_types, f'{prefix}{name}.', child_internal)
                visit(child, f'{prefix}{name}.', child_internal)

        compiled = self._compiled_scope(scope)
        if compiled is None:
            return tuple(names)
        current = compiled
        while current is not None:
            add(current.struct_types, '', False)
            add(current.enum_types, '', False)
            current = current.parent_graph
        visit(self._global, '', False)
        parameters = [name for name, parameter in compiled.parsed_parameters.items()
                      if getattr(parameter, 'type_resolved', None) == model.types.ValueType]
        return tuple(names + sorted(set(declared)) + parameters)

    def _sources_of(self, scope, graph, target, replacing):
        """Return the compiled sources of the existing connections to a target reference."""
        replaced = self.outline.find(replacing) if replacing is not None else ()
        text = '.'.join(identifier.id_str for identifier in target.identifiers)
        records = self.outline.find_scope(scope)
        sources = []
        for edge in (records[0].edges if len(records) == 1 else ()):
            if edge.target.text != text or any(edge is record for record in replaced):
                continue
            port = None
            reference = edge._source if edge.kind == 'connect' else None
            if reference is not None:
                try:
                    port = reference.resolve(graph, PortDirection.Output, 'source').port
                except (model.ProgramError, AttributeError, KeyError, RuntimeError):
                    pass
            sources.append(port)
        return sources

    def value_type(self, target):
        """Return the type of a state, value binding, constant, parameter, or port of a node type or procedure."""
        path = target.procedure.path if isinstance(target, ProcedurePort) else target.path
        scope = self._compiled_scope(path[:-1])
        name = path[-1]
        if scope is None:
            return None
        if isinstance(target, State):
            node = scope.state_nodes.get(name)
        elif isinstance(target, Binding):
            node = scope.temporary_nodes.get(name)
        elif isinstance(target, (Constant, Parameter)):
            declaration = scope.static_declarations.get(name)
            return getattr(declaration, 'type_resolved', None)
        elif isinstance(target, Port):
            node = (scope.input_ports if target.direction == 'input' else scope.output_ports).get(name)
        elif isinstance(target, ProcedurePort):
            procedure = scope.procedures.get(target.procedure.path[-1])
            if procedure is None or getattr(procedure, 'failed', False):
                return None
            port = (procedure.input_ports if target.direction == 'input' else procedure.output_ports).get(target.key)
            try:
                return None if port is None else port.var_type
            except RuntimeError:
                # The reference of the port did not resolve.
                return None
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
    warnings = WarningSink(diagnostics.append)
    coordinator = CompilerCoordinator(global_node_type, diagnostics=diagnostics, warnings=warnings)
    global_node_type.file_metadata = validate_files(parsed_files, contain=coordinator.contain, warnings=warnings)
    with coordinator.contain(None):
        validate_collisions(declarations)
        validate_native_names(declarations)
    coordinator.compile_interfaces()
    coordinator.resolve_procedure_ports()
    elements = _element_spans(result)
    return Analysis(result, tuple(ElementDiagnostic(diagnostic, _element(elements, diagnostic))
                                  for diagnostic in diagnostics), global_node_type)


def _is_internal(attributes):
    return any(attribute.name == 'Internal' for attribute in attributes)


def _block_attributes(block):
    return (*getattr(block, 'outer_attributes', ()), *getattr(block, 'inner_attributes', ()))


def _reference(*names):
    from .editing import format_identifier
    return '.'.join(name if name.startswith('default ') else format_identifier(name) for name in names)


def _target_references(graph):
    for name, node in graph.instance_nodes.items():
        for port in node.custom_inputs:
            yield _reference(name) if port.name == 'default input' else _reference(name, port.name)
    yield from (_reference(name) for name in graph.output_ports)
    yield from (_reference(name) for name in (*graph.state_nodes, *graph.temporary_nodes))
    for name, procedure in graph.procedures.items():
        if not getattr(procedure, 'failed', False):
            yield from (_reference(name, key) for key in procedure.input_ports)


def _source_references(graph):
    for name, node in graph.instance_nodes.items():
        for port in node.custom_outputs:
            yield _reference(name) if port.name == 'default output' else _reference(name, port.name)
    yield from (_reference(name) for name in graph.input_ports)
    for name in graph.state_nodes:
        yield from (_reference(name, 'old'), _reference(name, 'new'))
    yield from (_reference(name) for name in graph.temporary_nodes)
    for name, procedure in graph.procedures.items():
        if not getattr(procedure, 'failed', False):
            yield from (_reference(name, key) for key in procedure.output_ports)
    yield from (_reference(name) for name in graph.static_declarations)


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
        for record in (*scope.nodes, *scope.states, *scope.bindings, *scope.constants,
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
