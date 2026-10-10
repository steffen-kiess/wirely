"""The edited program: analysis of the open sources and edit commands.

Commands return TextEdits for the user files; they never edit the standard
library sources added for the analysis.
"""

import re

from wirely.compiler import editing
from wirely.compiler.analysis import analyze
from wirely.compiler.outline import Connection, NodeType


class Program:
    def __init__(self, sources):
        self.refresh(sources)

    def refresh(self, sources):
        """Analyze new (filename, text) pairs of the user files."""
        self.sources = tuple(sources)
        self.analysis = analyze(self.sources, add_std=True)
        filenames = {filename for filename, _ in self.sources}
        self.parse_errors = {file.filename: file.error for file in self.analysis.outline.files
                             if file.error is not None and file.filename in filenames}

    @property
    def outline(self):
        return self.analysis.outline

    def scope(self, path):
        """Return the scope record with the given path, or None if missing or ambiguous."""
        records = self.outline.find_scope(path)
        return records[0] if len(records) == 1 else None

    def scope_state(self, path, filenames=()):
        """Return 'live', 'frozen', or 'missing' for a graph of a scope.

        filenames are the files the scope was last seen in. A scope is
        frozen while one of them, or for a missing scope any user file,
        fails to parse.
        """
        if any(filename in self.parse_errors for filename in filenames):
            return 'frozen'
        if self.scope(path) is None:
            return 'frozen' if self.parse_errors else 'missing'
        return 'live'

    def user_scopes(self):
        """Return the paths of the global scope and the scopes declared in user files."""
        filenames = {filename for filename, _ in self.sources}
        paths = [()]

        def visit(scope):
            for child in (*scope.namespaces, *(node_type.scope for node_type in scope.node_types)):
                if filenames.intersection(child.filenames):
                    paths.append(child.path)
                visit(child)

        visit(self.outline.scope)
        return paths

    def is_user_node_type(self, node_type):
        records = self.outline.find(node_type)
        return len(records) == 1 and records[0].filename in dict(self.sources)

    def span_of(self, target):
        """Return (filename, (start, end)) of a declaration or connection, or None."""
        records = self.outline.find(target)
        if not records:
            return None
        return records[0].filename, records[0].span

    def element_at(self, filename, offset):
        """Return (scope path, ID) of the innermost element at an offset, or None."""
        matches = []

        def add(scope_path, record, element_id):
            start, end = record.span
            if record.filename == filename and start <= offset <= end:
                matches.append((end - start, scope_path, element_id))

        def visit(scope):
            for record in (*scope.nodes, *scope.states, *scope.temporaries, *scope.constants,
                           *scope.parameters, *scope.ports, *scope.procedures):
                add(scope.path, record, record.id)
            for edge in scope.edges:
                if edge.id is not None:
                    add(scope.path, edge, edge.id)
            for node_type in scope.node_types:
                add(scope.path, node_type, node_type.id)
                visit(node_type.scope)
            for namespace in scope.namespaces:
                visit(namespace)

        visit(self.outline.scope)
        if not matches:
            return None
        _, scope_path, element_id = min(matches, key=lambda match: match[0])
        return scope_path, element_id

    def can_connect(self, scope, source, target, *, replacing=None):
        return self.analysis.can_connect(scope, source, target, replacing=replacing)

    def move(self, positions):
        """Set the positions of elements, given as {ID: (x, y)}."""
        return self._combined(
            lambda sources, target=target, x=x, y=y: editing.set_attribute(
                sources, target, editing.format_metadata('position', x=_coordinate(x), y=_coordinate(y)))
            for target, (x, y) in positions.items())

    def connect(self, scope, source, target, *, index=None):
        return editing.connect(self.sources, scope, source, target, index=index)

    def reconnect(self, connection, *, source=None, target=None):
        return editing.reconnect(self.sources, connection, source=source, target=target)

    def delete(self, targets):
        """Remove connections and declarations, with the connections referencing them."""
        connections = sorted((target for target in targets if isinstance(target, Connection)),
                             key=lambda connection: connection.occurrence or 0, reverse=True)
        declarations = [target for target in targets if not isinstance(target, Connection)]
        steps = [lambda sources, connection=connection: editing.disconnect(sources, connection)
                 for connection in connections]
        steps += [lambda sources, target=target: editing.remove_declaration(sources, target)
                  for target in declarations]
        return self._combined(steps)

    def add_declaration(self, scope, declaration, position=None):
        attributes = () if position is None else (
            editing.format_metadata('position', x=_coordinate(position[0]), y=_coordinate(position[1])),)
        return editing.add_declaration(self.sources, scope, declaration, attributes=attributes)

    def add_node(self, scope, type_text, position=None):
        name = self.fresh_name(scope, node_name(type_text))
        return self.add_declaration(scope, editing.format_node(name, type_text), position)

    def fresh_name(self, scope, base):
        return editing.fresh_name(self.sources, scope, base)

    def set_parameter(self, node, name, value_text):
        """Bind a parameter of a node, or remove its binding if value_text is empty."""
        if not value_text.strip():
            return editing.remove_parameter_binding(self.sources, node, name)
        return editing.set_parameter_binding(self.sources, node, name, value_text)

    def set_node_type(self, node, type_text):
        return editing.set_node_type(self.sources, node, type_text)

    def rename(self, target, new_name):
        return editing.rename(self.sources, target, new_name)

    def _combined(self, steps):
        """Apply edit functions one after another; return one TextEdit per changed file."""
        original = dict(self.sources)
        current = list(self.sources)
        for step in steps:
            current = editing.apply_edits(current, step(current))
        return [_difference(filename, original[filename], text)
                for filename, text in current if text != original[filename]]


def node_name(type_text):
    """Return a snake_case node name derived from a node type, e.g. debug_printer for Std.DebugPrinter."""
    base = type_text.split('[')[0].split('.')[-1].strip()
    base = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', base).lower()
    return base if base.isidentifier() else 'node'


def renamed_path(path, target, new_name):
    """Return path after renaming target, for paths inside a renamed node type."""
    if isinstance(target, NodeType) and path[:len(target.path)] == target.path:
        return target.path[:-1] + (new_name,) + path[len(target.path):]
    return path


def _coordinate(value):
    return round(value)


def _difference(filename, old, new):
    start = 0
    limit = min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end = 0
    while end < limit - start and old[len(old) - 1 - end] == new[len(new) - 1 - end]:
        end += 1
    return editing.TextEdit(filename, start, len(old) - end, new[start:len(new) - end])
