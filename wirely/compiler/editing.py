"""Source-preserving edits of declarations, connections, and attributes.

The functions take all sources of a program as (filename, text) pairs and
return TextEdit lists for apply_edits(). They only parse the sources, so they
also work on programs that do not compile, for example because of unknown
types or missing libraries. Only the files containing the edited
declarations need to parse. Comments and formatting outside the edits are
left unchanged.

Targets and scopes are the IDs and paths from wirely.compiler.outline.
Within a scope, the sources of a multi input are ordered by the textual
order of their connections; connect() places new connections accordingly.
Added declarations are set apart from their neighbours by blank lines;
consecutive connect statements stay together.
"""

import math
import re
from dataclasses import dataclass, replace

import lark

from . import ast
from . import parser
from .expressions import ExpressionBlock, IfLetExpression, LocalBinding, PropertyAccess
from .node_type import NodeType as _NodeType
from wirely import types as _types
from .outline import (
    Connection, Constant, Node, NodeType, Parameter, Port, Procedure, ProcedurePort, State, Temporary,
    outline,
)


@dataclass(frozen=True)
class TextEdit:
    """Replace text[start:end] of the named file by text."""
    filename: str
    start: int
    end: int
    text: str


def apply_edits(sources, edits):
    """Return the (filename, text) pairs with the edits applied.

    Insertions at the same offset keep their order in edits.
    """
    sources = list(sources)
    by_file = {filename: [] for filename, _ in sources}
    for edit in edits:
        if edit.filename not in by_file:
            raise ValueError(f'Unknown file {edit.filename!r}')
        by_file[edit.filename].append(edit)
    result = []
    for filename, text in sources:
        file_edits = sorted(by_file[filename], key=lambda edit: (edit.start, edit.end))
        for previous, edit in zip(file_edits, file_edits[1:]):
            if previous.end > edit.start:
                raise ValueError(f'Overlapping edits in {filename!r}')
        for edit in reversed(file_edits):
            text = text[:edit.start] + edit.text + text[edit.end:]
        result.append((filename, text))
    return result


def format_metadata(name, **arguments):
    """Return attribute metadata such as 'Diagram.Position(X = 1, Y = 2.5)'.

    Argument values must be integers, finite floats, or booleans.
    """
    if not arguments:
        return name
    formatted = ', '.join(f'{key} = {_format_literal(value)}' for key, value in arguments.items())
    return f'{name}({formatted})'


def format_identifier(name):
    """Return name as an identifier, as a raw identifier r#name for keywords."""
    for candidate in (name, f'r#{name}'):
        try:
            parsed = parser.parse_str(f'temporary {candidate}: T;', filename='<identifier>')
        except (lark.UnexpectedInput, lark.exceptions.VisitError, ast.ProgramError):
            continue
        if parsed[0].name_str == name:
            return candidate
    raise ValueError(f'Invalid identifier {name!r}')


def format_node(name, type_text, parameters=()):
    """Return a node declaration; parameters are (name, value text) pairs."""
    bindings = ', '.join(f'{format_identifier(key)}: {value}' for key, value in parameters)
    return _checked(f'node {format_identifier(name)}: {type_text} {{ {bindings} }}' if bindings
                    else f'node {format_identifier(name)}: {type_text} {{}}')


def format_state(name, type_text, initial_text):
    return _checked(f'state {format_identifier(name)}: {type_text} = {initial_text};')


def format_temporary(name, type_text):
    return _checked(f'temporary {format_identifier(name)}: {type_text};')


def format_constant(name, type_text, value_text):
    return _checked(f'constant {format_identifier(name)}: {type_text} = {value_text};')


def format_parameter(name, type_text, default_text=None, optional=False):
    if optional and default_text is not None:
        raise ValueError('An optional parameter cannot have a default value')
    prefix = 'optional ' if optional else ''
    default = '' if default_text is None else f' = {default_text}'
    return _checked(f'{prefix}parameter {format_identifier(name)}: {type_text}{default};')


def format_port(direction, name, kind, type_text, is_list=False):
    """Return a port declaration; name is None for the default port.

    direction is 'input' or 'output', kind 'value', 'event', or 'resource'.
    """
    if direction not in ('input', 'output'):
        raise ValueError(f'Invalid port direction {direction!r}')
    if kind not in ('value', 'event', 'resource'):
        raise ValueError(f'Invalid port kind {kind!r}')
    head = f'default {direction}' if name is None else f'{direction} {format_identifier(name)}'
    multi = 'multi ' if is_list else ''
    return _checked(f'{head}: {multi}{kind} {type_text};')


def fresh_name(sources, scope, base):
    """Return base, or base followed by a number, unused in the scope."""
    used = _used_names(_scope(outline(sources), scope))
    name = base
    number = 2
    while name in used:
        name = f'{base}{number}'
        number += 1
    return name


def add_declaration(sources, scope, declaration, *, attributes=(), filename=None):
    """Insert one declaration into a scope with attributes.

    Attributes are inner attributes of nodes and node types and outer
    attributes of other declarations.

    It follows the last declaration of the same kind, or the last of an
    earlier kind in the order parameter, constant, port, state, temporary,
    node, procedure, node type; otherwise it starts the scope's body. With
    filename, it is inserted into that file's part of the scope.
    """
    declaration = declaration.strip()
    item = _parse_declaration(declaration)
    if isinstance(item, ast.DeclarationConnection):
        raise ValueError('Use connect() to add connections')
    for metadata in attributes:
        _metadata_name(metadata)
    result = outline(sources)
    record = _scope(result, scope)
    kind = _KIND_ORDER.index(type(item)) if type(item) in _KIND_ORDER else len(_KIND_ORDER)
    groups = _kind_records(record)
    anchor = None
    for index in range(min(kind, len(groups) - 1), -1, -1):
        candidates = [candidate for candidate in groups[index]
                      if filename is None or candidate.filename == filename]
        if candidates:
            anchor = candidates[-1]
            break
    texts = dict(sources)
    target = anchor.filename if anchor is not None else _block(record, filename)[0]
    if attributes and isinstance(item, (ast.DeclarationInstance, _NodeType)):
        if isinstance(item, _NodeType):
            site = _body_site(declaration, item, item.inner_attributes, _end(item.name), _end(item) - 1)
        else:
            site = _body_site(declaration, item, item.instantiation.inner_attributes,
                              _end(item.instantiation.type_name), _end(item.instantiation) - 1)
        declaration = _insert_inner(declaration, site, [f'#![{metadata}]' for metadata in attributes],
                                    indent_unit=_indent_unit(texts[target]))
        attributes = ()
    lines = [f'#[{metadata}]' for metadata in attributes] + declaration.split('\n')
    if anchor is not None:
        return [_insert_after(texts, anchor.filename, _outer_start(anchor._declaration),
                              _attached_end(texts, record, anchor), lines, 'declaration')]
    return [_insert_at_body_start(texts, _block(record, filename), lines, 'declaration')]


def remove_declaration(sources, target, *, cascade=True):
    """Remove a declaration and, with cascade, the connections referencing it."""
    if not isinstance(target, (Node, NodeType, State, Temporary, Constant, Parameter, Port, Procedure)):
        raise TypeError(f'Unsupported declaration target {target!r}')
    result = outline(sources)
    record = _single(result.find(target), target)
    texts = dict(sources)
    edits = [_removal(texts, record.filename, _outer_start(record._declaration), record.span[1])]
    if cascade:
        edges = []
        for scope in _scopes(result.scope):
            for edge in scope.edges:
                if edge.kind == 'connect' and _references(result, edge, target):
                    edges.append(edge)
        edits += _disconnect_edges(texts, edges)
    return _merge_removals(texts, edits)


def connect(sources, scope, source, target, *, index=None, attributes=()):
    """Add a connection from source to target, written as in a connect statement.

    It follows the target's last connection, or with index, precedes the
    target's index-th source. Without connections to the target, it goes
    directly below the target's declaration and the connections to other
    ports of it there. Inside a connect statement with several sources,
    attributes before connect also apply to the new connection.
    """
    source, target = _parse_references(source, target)
    for metadata in attributes:
        _metadata_name(metadata)
    result = outline(sources)
    record = _scope(result, scope)
    texts = dict(sources)
    attribute_text = ''.join(f'#[{metadata}] ' for metadata in attributes)
    lines = [f'#[{metadata}]' for metadata in attributes] + [f'connect {source} -> {target};']
    connects = [edge for edge in record.edges if edge.kind == 'connect']
    existing = [edge for edge in connects if edge.target.text == _normalized(target)]
    if index is not None and index < len(existing):
        edge = existing[index]
        statement = edge._declaration
        if len(statement.sources) > 1:
            start = _outer_start(edge._source)
            return [TextEdit(edge.filename, start, start, f'{attribute_text}{source}, ')]
        return [_insert_before(texts, edge.filename, _outer_start(statement), lines)]
    declaration = None if existing else _target_record(record, target)
    if declaration is not None:
        return [_insert_after(texts, declaration.filename, _outer_start(declaration._declaration),
                              _attached_end(texts, record, declaration), lines, 'connect', attached=True)]
    if existing:
        anchor = existing[-1]
    else:
        filename = _block(record, None)[0]
        candidates = [edge for edge in connects if edge.filename == filename]
        if not candidates:
            candidates = [candidate for group in _kind_records(record) for candidate in group
                          if candidate.filename == filename]
            candidates.sort(key=lambda candidate: candidate.span)
        if not candidates:
            return [_insert_at_body_start(texts, _block(record, filename), lines, 'connect')]
        anchor = candidates[-1]
    return [_insert_after(texts, anchor.filename, _outer_start(anchor._declaration), anchor.span[1],
                          lines, 'connect')]


def disconnect(sources, connection):
    """Remove one connection, and its connect statement if it has no other sources."""
    edge = _single(outline(sources).find(connection), connection)
    return _disconnect_edges(dict(sources), [edge])


def reconnect(sources, connection, *, source=None, target=None):
    """Change the source or target of one connection.

    A connection moved to another target is added to that target like a new
    connection, see connect().
    """
    result = outline(sources)
    edge = _single(result.find(connection), connection)
    texts = dict(sources)
    new_source, new_target = _parse_references(source or edge.source.text, target or edge.target.text)
    if target is None or _normalized(new_target) == edge.target.text:
        return [TextEdit(edge.filename, *edge.source_span, new_source)] if source is not None else []
    statement = edge._declaration
    text = texts[edge.filename]
    attributes = [text[_start(attribute) + 2:_end(attribute) - 1]
                  for attribute in (*statement.outer_attributes, *edge._source.outer_attributes)]
    # Remove first, then add to the new target, so nearby edits cannot overlap.
    removed = apply_edits(sources, _disconnect_edges(texts, [edge]))
    added = apply_edits(removed, connect(removed, connection.scope, new_source, new_target,
                                         attributes=attributes))
    return [edit for filename, new in added for edit in _diff_edit(filename, texts[filename], new)]


def set_parameter_binding(sources, node, name, value_text, *, optional=False):
    """Set a parameter binding of a node, replacing bindings with the same name.

    A new binding follows the style of the existing ones; in a multi-line
    body without bindings, it ends with a comma.
    """
    binding = f'{format_identifier(name)}{"?:" if optional else ":"} {value_text.strip()}'
    _parse_declaration(f'node x: T {{ {binding} }}')
    record = _single(outline(sources).find(node), node)
    filename = record.filename
    text = dict(sources)[filename]
    instantiation = record._declaration.instantiation
    bindings = instantiation.parameters
    matching = [item for item in bindings if item.name.id_str == name]
    if matching:
        first, *rest = matching
        return ([TextEdit(filename, _start(first), _end(first), binding)]
                + _merge_removals(dict(sources), _binding_removals(filename, text, bindings, rest)))
    if bindings:
        last = bindings[-1]
        comma = _skip_trivia(text, _end(last))
        has_comma = text[comma] == ','
        line_start = _line_start(text, _start(last))
        if text[line_start:_start(last)].strip():
            if has_comma:
                return [TextEdit(filename, comma + 1, comma + 1, f' {binding},')]
            return [TextEdit(filename, _end(last), _end(last), f', {binding}')]
        indent = text[line_start:_start(last)]
        position = _line_end(text, comma + 1 if has_comma else _end(last))
        edits = [] if has_comma else [TextEdit(filename, _end(last), _end(last), ',')]
        return edits + [TextEdit(filename, position, position,
                                 f'\n{indent}{binding}{"," if has_comma else ""}')]
    open_brace = _skip_trivia(text, _end(instantiation.type_name))
    close_brace = _end(instantiation) - 1
    if not text[open_brace + 1:close_brace].strip() and '\n' not in text[open_brace:close_brace]:
        return [TextEdit(filename, open_brace, close_brace + 1, f'{{ {binding} }}')]
    site = _body_site(text, record._declaration, instantiation.inner_attributes,
                      _end(instantiation.type_name), close_brace)
    # A binding on its own line ends with a comma, like further ones will.
    return _diff_edit(filename, text, _insert_inner(text, site, [binding + ',']))


def remove_parameter_binding(sources, node, name):
    """Remove the bindings called name; there are no edits if none exist."""
    record = _single(outline(sources).find(node), node)
    text = dict(sources)[record.filename]
    bindings = record._declaration.instantiation.parameters
    removed = [item for item in bindings if item.name.id_str == name]
    return _merge_removals(dict(sources), _binding_removals(record.filename, text, bindings, removed))


def set_node_type(sources, node, type_text):
    """Change the node type a node instantiates."""
    type_text = type_text.strip()
    _parse_declaration(f'node x: {type_text} {{}}')
    record = _single(outline(sources).find(node), node)
    type_name = record._declaration.instantiation.type_name
    return [TextEdit(record.filename, _start(type_name), _end(type_name), type_text)]


def replace_declaration(sources, target, declaration):
    """Replace a state, temporary, constant, parameter, or port declaration.

    The new declaration must be of the same kind and name, and a port must
    keep its direction; attributes and comments around it are kept.
    """
    if not isinstance(target, (State, Temporary, Constant, Parameter, Port)):
        raise TypeError(f'Unsupported declaration target {target!r}')
    declaration = declaration.strip()
    item = _parse_declaration(declaration)
    if item.outer_attributes:
        raise ValueError('Attributes of a replaced declaration are kept; edit them separately')
    record = _single(outline(sources).find(target), target)
    old = record._declaration
    if type(item) is not type(old) or _declaration_name(item) != _declaration_name(old):
        raise ValueError(f'Expected a declaration of the same kind and name as {target}')
    if isinstance(item, ast.DeclarationPort) and item.direction != old.direction:
        raise ValueError('A replaced port must keep its direction')
    return [TextEdit(record.filename, _start(old), _end(old), declaration)]


def rename(sources, target, new_name):
    """Rename a declaration and update the references to it.

    Procedure ports that referred to the declaration without an alias get
    the old name as alias, and native procedures keep their Python name.
    Renaming fails if a reference would resolve differently afterwards.
    """
    if not isinstance(target, (Node, State, Temporary, Constant, Parameter, Port, Procedure, NodeType)):
        raise TypeError(f'Unsupported rename target {target!r}')
    before = outline(sources)
    record = _single(before.find(target), target)
    declaration = record._declaration
    old = target.path[-1]
    if isinstance(target, Port) and declaration.name is None:
        raise ValueError('Default ports cannot be renamed')
    new_text = format_identifier(new_name)
    if new_name == old:
        return []
    parent = _scope(before, target.path[:-1])
    if isinstance(target, NodeType):
        if record.is_native:
            raise ValueError('Native node types cannot be renamed')
        if any(item.name == new_name for item in parent.node_types) or any(
                namespace.path[-1] == new_name for namespace in parent.namespaces):
            raise ValueError(f'Name {new_name!r} is already used')
    elif new_name in _used_names(parent):
        raise ValueError(f'Name {new_name!r} is already used')
    texts = dict(sources)
    edits = [TextEdit(record.filename, _start(declaration.name), _end(declaration.name), new_text)]
    if isinstance(target, Procedure) and declaration.is_native and declaration.alias is None:
        # Keep the native procedure name.
        owner = _single(before.find(NodeType(target.path[:-1])), NodeType(target.path[:-1]))._declaration
        prefix = (owner.alias.value if owner.is_native and owner.alias is not None
                  else '.'.join(target.path[:-1]))
        edits.append(TextEdit(record.filename, _end(declaration.name), _end(declaration.name),
                              f' as {_format_string(f"{prefix}:{old}")}'))
    if isinstance(target, NodeType):
        for scope in _scopes(before.scope):
            for node in scope.nodes:
                if node.node_type == target:
                    name = node._declaration.instantiation.type_name.identifiers[-1]
                    edits.append(TextEdit(node.filename, _start(name), _end(name), new_text))
    else:
        old_text = format_identifier(old)
        for filename, item in _scope_items(parent):
            for reference, port in _endpoint_references(item):
                root = reference.identifiers[0]
                if root.id_str != old:
                    continue
                edits.append(TextEdit(filename, _start(root), _end(root), new_text))
                if port is not None and port.alias is None and reference.length == 1:
                    edits.append(TextEdit(filename, _end(reference), _end(reference), f' as {old_text}'))
        if isinstance(target, Port):
            edits += _port_reference_edits(before, NodeType(target.path[:-1]), old, new_text)
        if isinstance(target, (Constant, Parameter)):
            edits += _static_rename_edits(parent, old, new_name, new_text)
        if isinstance(target, Parameter):
            for scope in _scopes(before.scope):
                for node in scope.nodes:
                    if node.node_type == NodeType(target.path[:-1]):
                        for binding in node._declaration.instantiation.parameters:
                            if binding.name.id_str == old:
                                edits.append(TextEdit(node.filename, _start(binding.name), _end(binding.name),
                                                      new_text))
    _verify_rename(sources, edits, before, target, new_name)
    return edits


def add_attribute(sources, target, metadata):
    """Add an attribute, preferring an inner attribute on its own line."""
    _metadata_name(metadata)
    filename, text, site = _locate(sources, target)
    return _diff_edit(filename, text, _insert(text, site, metadata))


def set_attribute(sources, target, metadata):
    """Replace the attributes named like metadata in place, or add one."""
    name = _metadata_name(metadata)
    filename, original, site = _locate(sources, target)
    existing = _matching(site, name)
    if not existing:
        return _diff_edit(filename, original, _insert(original, site, metadata))
    first, *rest = existing
    text = original
    for attribute in sorted(rest, key=_start, reverse=True):
        text = _remove_span(text, _start(attribute), _end(attribute))
    opening = '#![' if first.style == 'inner' else '#['
    text = text[:_start(first)] + opening + metadata + ']' + text[_end(first):]
    return _diff_edit(filename, original, text)


def remove_attribute(sources, target, name):
    """Remove all attributes called name; there are no edits if none exist."""
    filename, original, site = _locate(sources, target)
    text = original
    for attribute in sorted(_matching(site, name), key=_start, reverse=True):
        text = _remove_span(text, _start(attribute), _end(attribute))
    return _diff_edit(filename, original, text)


# Declarations that only have outer attributes.
_OUTER_TARGETS = (State, Temporary, Constant, Parameter, Port, Procedure)

_KIND_ORDER = (
    ast.DeclarationParameter, ast.DeclarationConstant, ast.DeclarationPort, ast.DeclarationState,
    ast.DeclarationTemporary, ast.DeclarationInstance, ast.DeclarationProcedure, _NodeType,
)


def _kind_records(scope):
    return [scope.parameters, scope.constants, scope.ports, scope.states, scope.temporaries,
            scope.nodes, scope.procedures, scope.node_types]


def _scopes(scope):
    yield scope
    for node_type in scope.node_types:
        yield from _scopes(node_type.scope)
    for namespace in scope.namespaces:
        yield from _scopes(namespace)


def _scope(result, path):
    return _single(result.find_scope(path), f'scope {tuple(path)}')


def _block(scope, filename):
    blocks = [block for block in scope._blocks if filename is None or block[0] == filename]
    if not blocks:
        raise ValueError(f'No part of scope {scope.path} in {filename!r}')
    return blocks[-1]


def _references(result, edge, target):
    for endpoint in (edge.source, edge.target):
        if endpoint.element == target:
            return True
        if isinstance(target, Port) and isinstance(endpoint.element, Node):
            name = None if target.path[-1] == f'default {target.direction}' else target.path[-1]
            if endpoint.port != name or (endpoint is edge.target) != (target.direction == 'input'):
                continue
            if any(node.node_type == NodeType(target.path[:-1]) for node in result.find(endpoint.element)):
                return True
    return False


def _used_names(scope):
    """Return the names of a scope's nodes, ports, statics, and procedures."""
    used = {item.name for item in (*scope.nodes, *scope.parameters, *scope.constants,
                                   *scope.states, *scope.temporaries, *scope.procedures)}
    used.update(port.name for port in scope.ports if port.name is not None)
    return used


def _scope_items(scope):
    """Yield (filename, declaration) for the declarations directly in a scope."""
    for filename, block in scope._blocks:
        declarations = block if isinstance(block, list) else block.declarations
        for item in declarations:
            if not isinstance(item, (_NodeType, ast.DeclarationLibrary)):
                yield filename, item


def _endpoint_references(item):
    """Yield (reference, reference port or None) for connection and procedure port references."""
    if isinstance(item, ast.DeclarationConnection):
        for source in item.sources:
            yield source, None
        yield item.target, None
    elif isinstance(item, (ast.DeclarationProcedure, ast.DeclarationInitializer)):
        ports = [*item.depends.dependencies, *item.produces.outputs]
        for port in ports:
            if isinstance(port, ast.ReferencePort):
                yield port.reference, port


def _port_reference_edits(result, node_type, old, new_text):
    """Rename node.old references to a port of node_type in all scopes."""
    edits = []
    for scope in _scopes(result.scope):
        for filename, item in _scope_items(scope):
            for reference, _ in _endpoint_references(item):
                if reference.length != 2 or reference.identifiers[1].id_str != old:
                    continue
                nodes = result.find(Node(scope.path + (reference.identifiers[0].id_str,)))
                if any(node.node_type == node_type for node in nodes):
                    name = reference.identifiers[1]
                    edits.append(TextEdit(filename, _start(name), _end(name), new_text))
    return edits


def _static_rename_edits(scope, old, new, new_text):
    """Rename expression references to a parameter or constant within its scope."""
    if new in ('true', 'false', 'None') or new in _types.type_by_name:
        raise ValueError(f'Name {new!r} is reserved')
    sites = []
    uses = []
    for filename, item in _scope_items(scope):
        for expression in _expression_fields(item):
            sites += [(filename, identifier, bound) for identifier, bound in _reference_roots(expression)]
        if isinstance(item, (ast.DeclarationProcedure, ast.DeclarationInitializer)) and item.uses is not None:
            for binding in item.uses.bindings:
                sites += [(filename, identifier, bound) for identifier, bound in _reference_roots(binding.expression)]
                expression = binding.expression
                if (binding.alias is None and isinstance(expression, ast.Reference) and expression.length == 1
                        and expression.identifiers[0].id_str == old):
                    uses.append(TextEdit(filename, _end(expression), _end(expression), f' as {format_identifier(old)}'))
    edits = []
    for filename, identifier, bound in sites:
        if identifier.id_str == new:
            raise ValueError(f'Name {new!r} is already referenced in this scope')
        if identifier.id_str == old and old not in bound:
            if new in bound:
                raise ValueError(f'Name {new!r} is bound locally where {old!r} is used')
            edits.append(TextEdit(filename, _start(identifier), _end(identifier), new_text))
    return edits + uses


def _expression_fields(item):
    """Yield the expressions of a declaration that can refer to statics of its scope."""
    if isinstance(item, ast.DeclarationParameter):
        yield item.ty
        if item.default_value is not None:
            yield item.default_value
    elif isinstance(item, ast.DeclarationState):
        yield item.var_type
        yield item.initial_value
    elif isinstance(item, ast.DeclarationTemporary):
        yield item.var_type
    elif isinstance(item, ast.DeclarationPort):
        if isinstance(item.port_spec, ast.PortTypeSpecificationData):
            yield item.port_spec.ty
    elif isinstance(item, ast.DeclarationAssertion):
        yield item.condition
        if item.message is not None:
            yield item.message
    elif isinstance(item, ast.DeclarationInstance):
        for binding in item.instantiation.parameters:
            yield binding.value
    elif isinstance(item, ast.DeclarationProcedure):
        for port in (*item.depends.dependencies, *item.produces.outputs):
            if isinstance(port, ast.TypedPort) and isinstance(port.port_spec, ast.PortTypeSpecificationData):
                yield port.port_spec.ty
        if item.body is not None:
            yield item.body
    elif isinstance(item, ast.DeclarationEnumType):
        yield item.underlying_type
        for member in item.members:
            yield member.value
    elif isinstance(item, ast.DeclarationStructType):
        for field in item.fields:
            yield field.field_type


_SYNTAX_SKIPPED = ('source_position', 'outer_attributes', 'inner_attributes', 'attributes')


def _reference_roots(element, bound=frozenset()):
    """Yield (identifier, locally bound names) for the root of each value reference."""
    if isinstance(element, ast.Reference):
        yield element.identifiers[0], bound
    elif isinstance(element, ast.FunctionCall):
        if not (isinstance(element.fun, ast.Reference) and element.fun.length == 1):
            yield from _reference_roots(element.fun, bound)
        for parameter in element.parameters:
            yield from _reference_roots(parameter, bound)
    elif isinstance(element, ast.StructLiteral):
        for field in element.fields:
            yield from _reference_roots(field.value, bound)
    elif isinstance(element, PropertyAccess):
        yield from _reference_roots(element.receiver, bound)
    elif isinstance(element, IfLetExpression):
        yield from _reference_roots(element.scrutinee, bound)
        yield from _reference_roots(element.then, bound | {element.name.id_str})
        yield from _reference_roots(element.otherwise, bound)
    elif isinstance(element, ExpressionBlock):
        for statement in element.statements:
            if isinstance(statement, LocalBinding):
                if statement.annotation is not None:
                    yield from _reference_roots(statement.annotation, bound)
                yield from _reference_roots(statement.initializer, bound)
                bound = bound | {statement.name.id_str}
            else:
                yield from _reference_roots(statement, bound)
    elif isinstance(element, (list, tuple)):
        for item in element:
            yield from _reference_roots(item, bound)
    elif isinstance(element, ast.SourceElement):
        for name, child in vars(element).items():
            if name not in _SYNTAX_SKIPPED and isinstance(child, (ast.SourceElement, list, tuple)):
                yield from _reference_roots(child, bound)


def _map_id(element, target, new_target):
    """Map an ID from before a rename to the ID afterwards."""
    if element == target:
        return new_target
    if isinstance(element, ProcedurePort):
        return replace(element, procedure=_map_id(element.procedure, target, new_target))
    path = getattr(element, 'path', None)
    if isinstance(target, NodeType) and path is not None and path[:len(target.path)] == target.path:
        return replace(element, path=new_target.path + path[len(target.path):])
    return element


def _verify_rename(sources, edits, before, target, new_name):
    """Check that references resolve to the same declarations after a rename."""
    new_target = replace(target, path=target.path[:-1] + (new_name,))
    after = outline(apply_edits(sources, edits))
    port_type = NodeType(target.path[:-1]) if isinstance(target, Port) else None

    def renamed(endpoint):
        port = endpoint.port
        if (port_type is not None and port == target.path[-1] and isinstance(endpoint.element, Node)
                and any(node.node_type == port_type for node in before.find(endpoint.element))):
            port = new_name
        return _map_id(endpoint.element, target, new_target), port

    def summary(result, endpoint_key, node_type_key):
        scopes = list(_scopes(result.scope))
        return ([(endpoint_key(edge.source), endpoint_key(edge.target)) for scope in scopes for edge in scope.edges],
                [node_type_key(node.node_type) for scope in scopes for node in scope.nodes])

    expected = summary(before, renamed, lambda node_type: _map_id(node_type, target, new_target))
    actual = summary(after, lambda endpoint: (endpoint.element, endpoint.port), lambda node_type: node_type)
    if expected != actual or len(after.find(new_target)) != 1:
        raise ValueError('Renaming would change what references resolve to')


def _declaration_name(item):
    return item.name_str if isinstance(item, ast.DeclarationPort) else item.name.id_str


def _binding_removals(filename, text, bindings, removed):
    """Remove bindings with their commas, and lines they leave empty."""
    removed_ids = {id(item) for item in removed}
    edits = []
    inline = []
    for item in bindings:
        if id(item) not in removed_ids:
            inline.append(False)
            continue
        end = _end(item)
        comma = _skip_trivia(text, end)
        if comma < len(text) and text[comma] == ',':
            end = comma + 1
        line_start = _line_start(text, _start(item))
        rest = text[end:_line_end(text, end)].strip()
        if not text[line_start:_start(item)].strip() and (not rest or rest.startswith('//')):
            edits.append(TextEdit(filename, *_removal_range(text, _start(item), end), ''))
            inline.append(False)
        else:
            inline.append(True)
    if any(inline):
        if all(inline):
            for item in bindings:
                end = _end(item)
                comma = _skip_trivia(text, end)
                if comma < len(text) and text[comma] == ',':
                    end = comma + 1
                while end < len(text) and text[end] in ' \t':
                    end += 1
                edits.append(TextEdit(filename, _start(item), end, ''))
        else:
            edits += _list_item_removals(filename, [_start(item) for item in bindings],
                                         [_end(item) for item in bindings], inline)
    return edits


def _single(matches, target):
    if not matches:
        raise ValueError(f'No match for {target}')
    if len(matches) > 1:
        raise ValueError(f'Multiple matches for {target}')
    return matches[0]


def _checked(declaration):
    _parse_declaration(declaration)
    return declaration


def _parse_declaration(declaration):
    try:
        parsed = parser.parse_str(declaration, filename='<declaration>')
    except (lark.UnexpectedInput, lark.exceptions.VisitError, ast.ProgramError) as error:
        raise ValueError(f'Malformed declaration {declaration!r}') from error
    if len(parsed) != 1 or parsed.inner_attributes:
        raise ValueError(f'Expected one declaration: {declaration!r}')
    return parsed[0]


def _parse_references(source, target):
    source, target = source.strip(), target.strip()
    item = _parse_declaration(f'connect {source} -> {target};')
    if len(item.sources) != 1 or item.outer_attributes or item.sources[0].outer_attributes:
        raise ValueError(f'Malformed connection {source!r} -> {target!r}')
    return source, target


def _normalized(reference):
    item = _parse_declaration(f'connect x -> {reference};')
    return '.'.join(identifier.id_str for identifier in item.target.identifiers)


def _outer_start(element):
    return min([_start(element), *map(_start, element.outer_attributes)])


def _diff_edit(filename, old, new):
    if old == new:
        return []
    prefix = 0
    limit = min(len(old), len(new))
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return [TextEdit(filename, prefix, len(old) - suffix, new[prefix:len(new) - suffix])]


def _insert_after(texts, filename, start, end, lines, kind=None, *, attached=False):
    """Insert lines after the item at start:end, on new lines when possible.

    kind is the _item_kind() of the inserted item, which decides the blank lines
    around it; without it, none are added. With attached, no blank line
    separates the lines from the item.
    """
    text = texts[filename]
    line_end = _line_end(text, end)
    rest = text[end:line_end].strip()
    if rest and not rest.startswith('//') and len(lines) == 1:
        return TextEdit(filename, end, end, ' ' + lines[0])
    indent = _indentation(text, start)
    inserted = ''.join(f'\n{indent}{line}' for line in lines)
    if not attached and _blank_between(_item_kind(text, start), kind):
        inserted = '\n' + inserted
    if _blank_between(kind, _item_kind(text, line_end)) and text[line_end + 1:_line_end(text, line_end + 1)].strip():
        inserted += '\n'
    return TextEdit(filename, line_end, line_end, inserted)


def _target_record(scope, target):
    """Return the declaration in scope that a connection target refers to, or None."""
    reference = _parse_declaration(f'connect x -> {target};').target
    name = reference.identifiers[0].id_str
    if reference.length == 1:
        for port in scope.ports:
            if port.direction == 'output' and (port.name or 'default output') == name:
                return port
    if reference.length <= 2:
        for records in (scope.states, scope.temporaries, scope.procedures, scope.nodes):
            for record in records:
                if record.name == name:
                    return record
    return None


def _attached_end(texts, scope, record):
    """Return the end of a declaration and the connect statements to it directly below.

    The statements form a block without blank lines and target the
    declaration or one of its ports.
    """
    text = texts[record.filename]
    statements = {}
    for edge in scope.edges:
        if edge.kind == 'connect' and edge.filename == record.filename and edge.target.element == record.id:
            statement = edge._declaration
            statements[_outer_start(statement)] = _end(statement)
    end = record.span[1]
    while True:
        following = _skip_trivia(text, end)
        if following not in statements or re.search(r'\n[ \t]*\n', text[end:following]):
            return end
        end = statements[following]


def _item_kind(text, position):
    """Return 'connect' or 'declaration' for the next item at position, or None if there is none."""
    position = _skip_trivia(text, position)
    while text.startswith('#[', position):
        depth = 0
        while position < len(text):
            depth += {'[': 1, ']': -1}.get(text[position], 0)
            position += 1
            if depth == 0:
                break
        position = _skip_trivia(text, position)
    if position >= len(text) or text[position] == '}' or text.startswith('#![', position):
        return None
    return 'connect' if re.match(r'connect\b', text[position:position + 8]) else 'declaration'


def _blank_between(first, second):
    """Whether adjacent items of these kinds are separated by a blank line."""
    return first is not None and second is not None and not first == second == 'connect'


def _insert_before(texts, filename, start, lines):
    text = texts[filename]
    line_start = _line_start(text, start)
    indent = text[line_start:start]
    if indent.strip():
        return TextEdit(filename, start, start, ' '.join(lines) + ' ')
    return TextEdit(filename, line_start, line_start, ''.join(f'{indent}{line}\n' for line in lines))


def _insert_at_body_start(texts, block, lines, kind):
    filename, element = block
    text = texts[filename]
    if isinstance(element, list):
        # A file: before its first declaration, after its inner attributes.
        if element:
            first = element[0]
            start = _outer_start(first)
            line_start = _line_start(text, start)
            if not text[line_start:start].strip():
                indent = text[line_start:start]
                blank = '\n' if _blank_between(kind, _item_kind(text, start)) else ''
                return TextEdit(filename, line_start, line_start,
                                ''.join(f'{indent}{line}\n' for line in lines) + blank)
            return TextEdit(filename, start, start, ' '.join(lines) + ' ')
        if element.inner_attributes:
            end = _end(element.inner_attributes[-1])
            return _insert_after(texts, filename, _start(element.inner_attributes[0]), end, lines, kind)
        separator = '' if not text or text.endswith('\n') else '\n'
        return TextEdit(filename, len(text), len(text), separator + ''.join(f'{line}\n' for line in lines))
    site = _body_site(text, element, element.inner_attributes, _end(element.name), _end(element) - 1)
    blank_after = _blank_between(kind, _item_kind(text, site.anchor))
    return _diff_edit(filename, text, _insert_inner(text, site, lines, blank_after=blank_after))[0]


def _removal(texts, filename, start, end):
    """Remove an item and the lines it empties, leaving no doubled blank line behind."""
    text = texts[filename]
    start, end = _removal_range(text, start, end)
    if start == _line_start(text, start) and (end == len(text) or text[end - 1] == '\n'):
        before_blank = start > 0 and not text[_line_start(text, start - 1):start].strip()
        after = text[end:_line_end(text, end)].strip()
        after_blank = end < len(text) and not after
        if after_blank and (before_blank or start == 0 or text[:start].rstrip().endswith('{')):
            end = min(_line_end(text, end) + 1, len(text))
        elif before_blank and (end == len(text) or after.startswith('}')):
            start = _line_start(text, start - 1)
    return TextEdit(filename, start, end, '')


def _merge_removals(texts, edits):
    """Merge overlapping removals, e.g. of declarations sharing a line."""
    merged = []
    for edit in sorted(edits, key=lambda edit: (edit.filename, edit.start)):
        previous = merged[-1] if merged else None
        if previous is None or previous.filename != edit.filename or previous.end <= edit.start:
            merged.append(edit)
            continue
        # Recompute the range from the combined text so emptied lines are removed.
        text = texts[edit.filename]
        start, end = previous.start, max(previous.end, edit.end)
        while text[start] in ' \t\r\n':
            start += 1
        while text[end - 1] in ' \t\r\n':
            end -= 1
        merged[-1] = _removal(texts, edit.filename, start, end)
    return merged


def _disconnect_edges(texts, edges):
    """Remove the given connections, grouped by connect statement."""
    statements = {}
    for edge in edges:
        statements.setdefault(id(edge._declaration), (edge, set()))[1].add(id(edge._source))
    edits = []
    for edge, removed in statements.values():
        statement = edge._declaration
        filename = edge.filename
        sources = statement.sources
        if len(removed) == len(sources):
            edits.append(_removal(texts, filename, _outer_start(statement), _end(statement)))
            continue
        edits += _list_item_removals(filename, [_outer_start(source) for source in sources],
                                     [_end(source) for source in sources],
                                     [id(source) in removed for source in sources])
    return edits


def _list_item_removals(filename, starts, ends, removed):
    """Remove some items of a comma-separated list along with their commas."""
    edits = []
    tail = len(removed)
    while removed[tail - 1]:
        tail -= 1
    if tail < len(removed):
        edits.append(TextEdit(filename, ends[tail - 1], ends[-1], ''))
    for index in range(tail):
        if removed[index]:
            edits.append(TextEdit(filename, starts[index], starts[index + 1], ''))
    return edits


@dataclass
class _Site:
    attributes: list
    # Same-named attributes here also apply to other edges.
    shared_attributes: list
    # 'inner' inserts into the body; 'outer' and 'source' insert before anchor.
    kind: str
    anchor: int
    declaration_start: int = None
    open_brace: int = None
    close_brace: int = None


def _format_string(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _format_literal(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f'Attribute values must be finite: {value!r}')
        return repr(value)
    raise TypeError(f'Unsupported attribute value {value!r}')


def _metadata_name(metadata):
    try:
        parsed = parser.parse_str(f'#![{metadata}]', filename='<metadata>')
    except (lark.UnexpectedInput, lark.exceptions.VisitError, ast.ProgramError) as error:
        raise ValueError(f'Malformed attribute metadata {metadata!r}') from error
    if parsed or len(parsed.inner_attributes) != 1:
        raise ValueError(f'Malformed attribute metadata {metadata!r}')
    return parsed.inner_attributes[0].name


def _matching(site, name):
    if any(attribute.name == name for attribute in site.shared_attributes):
        raise ValueError(f'Attribute {name} applies to the whole connect statement')
    return [attribute for attribute in site.attributes if attribute.name == name]


def _start(element):
    return element.source_position.meta.start_pos


def _end(element):
    return element.source_position.meta.end_pos


def _locate(sources, target):
    if not isinstance(target, (Node, NodeType, Connection, *_OUTER_TARGETS)):
        raise TypeError(f'Unsupported attribute target {target!r}')
    record = _single(outline(sources).find(target), target)
    text = dict(sources)[record.filename]
    declaration = record._declaration
    if isinstance(target, Node):
        instantiation = declaration.instantiation
        site = _body_site(text, declaration,
                          declaration.outer_attributes + instantiation.inner_attributes,
                          _end(instantiation.type_name), _end(instantiation) - 1)
    elif isinstance(target, NodeType):
        site = _body_site(text, declaration,
                          declaration.outer_attributes + declaration.inner_attributes,
                          _end(declaration.name), _end(declaration) - 1)
    elif isinstance(target, _OUTER_TARGETS):
        site = _Site(list(declaration.outer_attributes), [], 'outer', _start(declaration))
    else:
        source = record._source
        if len(declaration.sources) == 1:
            site = _Site(list(declaration.outer_attributes + source.outer_attributes), [],
                         'outer', _start(declaration))
        else:
            site = _Site(list(source.outer_attributes), list(declaration.outer_attributes),
                         'source', _start(source))
    return record.filename, text, site


def _body_site(text, declaration, attributes, brace_search_start, close_brace):
    open_brace = _skip_trivia(text, brace_search_start)
    if text[open_brace] != '{':
        raise ValueError(f'Expected {{ at offset {open_brace}')
    inner = [attribute for attribute in attributes if attribute.style == 'inner']
    anchor = max(map(_end, inner)) if inner else open_brace + 1
    return _Site(list(attributes), [], 'inner', anchor, _start(declaration),
                 open_brace, close_brace)


_comment_delimiter = re.compile(r'/+\*|\*+/')


def _skip_trivia(text, position):
    while position < len(text):
        if text[position] in ' \t\f\r\n':
            position += 1
        elif text.startswith('//', position):
            newline = text.find('\n', position)
            position = len(text) if newline == -1 else newline
        elif text.startswith('/*', position):
            position += 2
            level = 1
            while level:
                match = _comment_delimiter.search(text, position)
                if match is None:
                    return len(text)
                level += 1 if match.group().endswith('*') else -1
                position = match.end()
        else:
            break
    return position


def _line_start(text, position):
    return text.rfind('\n', 0, position) + 1


def _line_end(text, position):
    newline = text.find('\n', position)
    return len(text) if newline == -1 else newline


def _indentation(text, position):
    start = _line_start(text, position)
    end = start
    while end < len(text) and text[end] in ' \t':
        end += 1
    return text[start:end]


def _indent_unit(text):
    lines = text.split('\n')
    for index, line in enumerate(lines):
        if not line.rstrip().endswith('{'):
            continue
        following = next((item for item in lines[index + 1:] if item.strip()), None)
        if following is None:
            break
        base = line[:len(line) - len(line.lstrip(' \t'))]
        indent = following[:len(following) - len(following.lstrip(' \t'))]
        if len(indent) > len(base) and indent.startswith(base):
            return indent[len(base):]
    return '  '


def _insert(text, site, metadata):
    if site.kind == 'inner':
        return _insert_inner(text, site, [f'#![{metadata}]'])
    attribute = f'#[{metadata}]'
    anchor = site.anchor
    before = text[_line_start(text, anchor):anchor]
    if site.kind == 'outer' and not before.strip():
        return text[:anchor] + attribute + '\n' + before + text[anchor:]
    return text[:anchor] + attribute + ' ' + text[anchor:]


def _insert_inner(text, site, lines, *, blank_after=False, indent_unit=None):
    """Insert lines at the start of a body; with blank_after, a blank line separates them from its members."""
    open_brace, close_brace, anchor = site.open_brace, site.close_brace, site.anchor
    outer_indent = _indentation(text, site.declaration_start)
    indent_unit = indent_unit or _indent_unit(text)
    if '\n' not in text[open_brace:close_brace]:
        indent = outer_indent + indent_unit
        parts = [part for part in (text[open_brace + 1:anchor].strip(), *lines) if part]
        following = text[anchor:close_brace].strip()
        if following:
            parts += ['', following] if blank_after else [following]
        body = ''.join(f'\n{indent}{part}' if part else '\n' for part in parts)
        return text[:open_brace + 1] + body + '\n' + outer_indent + text[close_brace:]

    line_end = _line_end(text, anchor)
    rest = text[anchor:line_end].strip()
    if rest and not rest.startswith('//') and len(lines) == 1:
        separator = '' if text[anchor].isspace() else ' '
        return text[:anchor] + ' ' + lines[0] + separator + text[anchor:]

    insert_at = line_end + 1
    content = _skip_whitespace(text, insert_at)
    if content < close_brace:
        indent = _indentation(text, content)
    else:
        indent = outer_indent + indent_unit
    blank = '\n' if blank_after and text[insert_at:_line_end(text, insert_at)].strip() else ''
    return text[:insert_at] + ''.join(f'{indent}{line}\n' for line in lines) + blank + text[insert_at:]


def _skip_whitespace(text, position):
    while position < len(text) and text[position] in ' \t\f\r\n':
        position += 1
    return position


def _removal_range(text, start, end):
    """Return the range removing text[start:end] and lines it leaves empty."""
    line_start = _line_start(text, start)
    line_end = _line_end(text, end)
    if not text[end:line_end].strip():
        if not text[line_start:start].strip():
            return line_start, min(line_end + 1, len(text))
        while start > line_start and text[start - 1] in ' \t':
            start -= 1
        return start, end
    while end < line_end and text[end] in ' \t':
        end += 1
    return start, end


def _remove_span(text, start, end):
    start, end = _removal_range(text, start, end)
    return text[:start] + text[end:]
