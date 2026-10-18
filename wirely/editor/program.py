"""The edited program: analysis of the open sources and edit commands.

Commands return TextEdits for the user files; they never edit the standard
library sources added for the analysis.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal

from wirely.compiler import ParseCache, editing
from wirely.compiler import outline as _outline
from wirely.compiler.analysis import ConnectionCheck, analyze
from wirely.compiler.outline import Connection, NodeType


class Program:
    def __init__(self, sources: editing.Sources, *, cache: ParseCache | None = None) -> None:
        self._parse_cache = ParseCache() if cache is None else cache
        self.refresh(sources)

    def refresh(self, sources: editing.Sources) -> None:
        """Analyze new (filename, text) pairs of the user files."""
        self.sources = tuple(sources)
        self.analysis = analyze(self.sources, add_std=True, cache=self._parse_cache)
        filenames = {filename for filename, _ in self.sources}
        self.parse_errors = {file.filename: file.error for file in self.analysis.outline.files
                             if file.error is not None and file.filename in filenames}

    @property
    def outline(self) -> _outline.Outline:
        return self.analysis.outline

    def scope(self, path: Sequence[str]) -> _outline.Scope | None:
        """Return the scope record with the given path, or None if missing or ambiguous."""
        records = self.outline.find_scope(path)
        return records[0] if len(records) == 1 else None

    def scope_state(self, path: Sequence[str], filenames: Iterable[str] = ()) -> Literal['live', 'frozen', 'missing']:
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

    def user_scopes(self) -> list[tuple[str, ...]]:
        """Return the paths of the global scope and the scopes declared in user files."""
        filenames = {filename for filename, _ in self.sources}
        paths: list[tuple[str, ...]] = [()]

        def visit(scope: _outline.Scope) -> None:
            for child in (*scope.namespaces, *(node_type.scope for node_type in scope.node_types)):
                if filenames.intersection(child.filenames):
                    paths.append(child.path)
                visit(child)

        visit(self.outline.scope)
        return paths

    def is_user_node_type(self, node_type: _outline.NodeType) -> bool:
        records = self.outline.find(node_type)
        return len(records) == 1 and records[0].filename in dict(self.sources)

    def span_of(self, target: _outline.ElementId) -> tuple[str, tuple[int, int]] | None:
        """Return (filename, (start, end)) of a declaration or connection, or None."""
        records = self.outline.find(target)
        if not records:
            return None
        return records[0].filename, records[0].span

    def element_at(self, filename: str, offset: int) -> tuple[tuple[str, ...], _outline.ElementId] | None:
        """Return (scope path, ID) of the innermost element at an offset, or None."""
        matches: list[tuple[int, tuple[str, ...], _outline.ElementId]] = []

        def add(scope_path: tuple[str, ...], record: _outline.Record, element_id: _outline.ElementId) -> None:
            start, end = record.span
            if record.filename == filename and start <= offset <= end:
                matches.append((end - start, scope_path, element_id))

        def visit(scope: _outline.Scope) -> None:
            records: tuple[_outline.DeclarationRecord, ...] = (
                *scope.nodes, *scope.states, *scope.bindings, *scope.constants,
                *scope.parameters, *scope.ports, *scope.procedures)
            for record in records:
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

    def can_connect(self, scope: Sequence[str], source: str, target: str, *,
                    replacing: Connection | None = None) -> ConnectionCheck:
        return self.analysis.can_connect(scope, source, target, replacing=replacing)

    def move(self, positions: Mapping[_outline.DeclarationId | Connection, tuple[float, float]]) -> list[editing.TextEdit]:
        """Set grid positions of elements, given as {ID: (x, y)}."""
        steps: list[Callable[[editing.Sources], list[editing.TextEdit]]] = []
        for target, (x, y) in positions.items():
            def step(sources: editing.Sources, target: _outline.DeclarationId | Connection = target,
                     x: float = x, y: float = y) -> list[editing.TextEdit]:
                return editing.set_attribute(
                    sources, target, editing.format_metadata('Diagram.Position', X=_coordinate(x), Y=_coordinate(y)),
                    cache=self._parse_cache)
            steps.append(step)
        return self._combined(steps)

    def reset_positions(self, targets: Iterable[_outline.DeclarationId | Connection]) -> list[editing.TextEdit]:
        """Remove the stored positions of elements, so they are placed automatically."""
        steps: list[Callable[[editing.Sources], list[editing.TextEdit]]] = []
        for target in targets:
            def step(sources: editing.Sources, target: _outline.DeclarationId | Connection = target) -> list[editing.TextEdit]:
                return editing.remove_attribute(sources, target, 'Diagram.Position', cache=self._parse_cache)
            steps.append(step)
        return self._combined(steps)

    def connect(self, scope: Sequence[str], source: str, target: str, *, index: int | None = None) -> list[editing.TextEdit]:
        return editing.connect(self.sources, scope, source, target, index=index, cache=self._parse_cache)

    def reconnect(self, connection: Connection, *, source: str | None = None,
                  target: str | None = None) -> list[editing.TextEdit]:
        return editing.reconnect(self.sources, connection, source=source, target=target, cache=self._parse_cache)

    def delete(self, targets: Iterable[_outline.DeclarationId | Connection]) -> list[editing.TextEdit]:
        """Remove connections and declarations, with the connections referencing them."""
        connections = sorted((target for target in targets if isinstance(target, Connection)),
                             key=lambda connection: connection.occurrence or 0, reverse=True)
        declarations = [target for target in targets if not isinstance(target, Connection)]
        steps: list[Callable[[editing.Sources], list[editing.TextEdit]]] = []
        for connection in connections:
            def disconnect(sources: editing.Sources, connection: Connection = connection) -> list[editing.TextEdit]:
                return editing.disconnect(sources, connection, cache=self._parse_cache)
            steps.append(disconnect)
        for target in declarations:
            def remove(sources: editing.Sources, target: _outline.DeclarationId = target) -> list[editing.TextEdit]:
                return editing.remove_declaration(sources, target, cache=self._parse_cache)
            steps.append(remove)
        return self._combined(steps)

    def add_declaration(self, scope: Sequence[str], declaration: str,
                        position: tuple[float, float] | None = None) -> list[editing.TextEdit]:
        attributes = () if position is None else (
            editing.format_metadata('Diagram.Position', X=_coordinate(position[0]), Y=_coordinate(position[1])),)
        return editing.add_declaration(self.sources, scope, declaration, attributes=attributes, cache=self._parse_cache)

    def add_node(self, scope: Sequence[str], type_text: str,
                 position: tuple[float, float] | None = None) -> list[editing.TextEdit]:
        name = self.fresh_name(scope, node_name(type_text))
        return self.add_declaration(scope, editing.format_node(name, type_text), position)

    def fresh_name(self, scope: Sequence[str], base: str) -> str:
        return editing.fresh_name(self.sources, scope, base, cache=self._parse_cache)

    def set_parameter(self, node: _outline.Node, name: str, value_text: str) -> list[editing.TextEdit]:
        """Bind a parameter of a node, or remove its binding if value_text is empty."""
        if not value_text.strip():
            return editing.remove_parameter_binding(self.sources, node, name, cache=self._parse_cache)
        return editing.set_parameter_binding(self.sources, node, name, value_text, cache=self._parse_cache)

    def set_node_type(self, node: _outline.Node, type_text: str) -> list[editing.TextEdit]:
        return editing.set_node_type(self.sources, node, type_text, cache=self._parse_cache)

    def rename(self, target: _outline.DeclarationId, new_name: str) -> list[editing.TextEdit]:
        return editing.rename(self.sources, target, new_name, cache=self._parse_cache)

    def _combined(self, steps: Iterable[Callable[[editing.Sources], list[editing.TextEdit]]]) -> list[editing.TextEdit]:
        """Apply edit functions one after another; return one TextEdit per changed file."""
        original = dict(self.sources)
        current = list(self.sources)
        for step in steps:
            current = editing.apply_edits(current, step(current))
        return [_difference(filename, original[filename], text)
                for filename, text in current if text != original[filename]]


def node_name(type_text: str) -> str:
    """Return a snake_case node name derived from a node type, e.g. debug_printer for Std.DebugPrinter."""
    base = type_text.split('[')[0].split('.')[-1].strip()
    base = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', base).lower()
    return base if base.isidentifier() else 'node'


def renamed_path(path: tuple[str, ...], target: _outline.DeclarationId, new_name: str) -> tuple[str, ...]:
    """Return path after renaming target, for paths inside a renamed node type."""
    if isinstance(target, NodeType) and path[:len(target.path)] == target.path:
        return target.path[:-1] + (new_name,) + path[len(target.path):]
    return path


def _coordinate(value: float) -> int | float:
    value = float(value)
    return int(value) if value.is_integer() else value


def _difference(filename: str, old: str, new: str) -> editing.TextEdit:
    start = 0
    limit = min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end = 0
    while end < limit - start and old[len(old) - 1 - end] == new[len(new) - 1 - end]:
        end += 1
    return editing.TextEdit(filename, start, len(old) - end, new[start:len(new) - end])
