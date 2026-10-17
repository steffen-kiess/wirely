"""Parsed attribute metadata and compiler attribute definitions."""

from __future__ import annotations

import contextlib
import os
import math
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, NoReturn, TypeGuard

if TYPE_CHECKING:
    from . import model
    from .diagnostics import WarningSink


@dataclass(frozen=True)
class MetadataLiteral:
    value: object
    source_position: model.SourcePosition


@dataclass(frozen=True)
class Metadata:
    path: tuple[str, ...]
    kind: str
    value: Any  # A literal, a tuple of arguments, or None, according to kind.
    source_position: model.SourcePosition

    @property
    def name(self) -> str:
        return '.'.join(self.path)


@dataclass(frozen=True)
class Attribute:
    metadata: Metadata
    style: str
    source_position: model.SourcePosition

    @property
    def name(self) -> str:
        return self.metadata.name


@dataclass(frozen=True)
class ValidatedAttribute:
    name: str
    value: object
    style: str
    source_position: model.SourcePosition


@dataclass(frozen=True)
class AttributeDefinition:
    targets: frozenset[str]
    validate: Callable[[Attribute], object]
    repeatable: bool = False


@dataclass(frozen=True)
class FileMetadata:
    filename: str | os.PathLike
    inner_attributes: tuple[Attribute, ...]
    attributes: tuple[ValidatedAttribute, ...]


class ParsedFile(list['model.Declaration']):
    """A declaration list retaining the enclosing file/body's attributes."""

    def __init__(self, declarations: Iterable[model.Declaration] = (), *,
                 inner_attributes: Iterable[Attribute] = ()) -> None:
        super().__init__(declarations)
        self.inner_attributes = tuple(inner_attributes)


def _error(source: Attribute | Metadata | MetadataLiteral | model.ast.SourceElement, message: str) -> NoReturn:
    from .ast import ProgramError, SourceElement
    raise ProgramError(SourceElement(source_position=source.source_position), message)


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and type(value) in (int, float) and math.isfinite(value)


def _position(attribute: Attribute) -> tuple[int | float, int | float]:
    metadata = attribute.metadata
    if metadata.kind != 'list':
        _error(attribute, f'{attribute.name} requires X and Y arguments')
    coordinates = {}
    for argument in metadata.value:
        if not isinstance(argument, Metadata) or argument.name not in ('X', 'Y'):
            _error(argument, f'Unknown {attribute.name} argument; expected X or Y')
        name = argument.name
        if name in coordinates:
            _error(argument, f'Duplicate {attribute.name} argument {name}')
        if argument.kind != 'value' or not isinstance(argument.value, MetadataLiteral) \
                or not _number(argument.value.value):
            _error(argument, f'{attribute.name} {name} requires a finite numeric literal')
        assert _number(argument.value.value)
        coordinates[name] = argument.value.value
    if set(coordinates) != {'X', 'Y'}:
        _error(attribute, f'{attribute.name} requires X and Y arguments')
    return coordinates['X'], coordinates['Y']


def _literal(attribute: Attribute) -> object:
    metadata = attribute.metadata
    if metadata.kind != 'value' or not isinstance(metadata.value, MetadataLiteral):
        return None
    return metadata.value.value


def _size(attribute: Attribute) -> int | float:
    value = _literal(attribute)
    if not _number(value) or value <= 0:
        _error(attribute, f'{attribute.name} requires a positive finite number, as in {attribute.name} = 8')
    return value


def _row(attribute: Attribute) -> int:
    value = _literal(attribute)
    if type(value) is not int or value < 0:
        _error(attribute, f'{attribute.name} requires a non-negative integer, as in {attribute.name} = 0')
    return value


def _warning_names(attribute: Attribute) -> tuple[str, ...]:
    from .diagnostics import WARNINGS

    metadata = attribute.metadata
    if metadata.kind != 'list' or not metadata.value:
        _error(attribute, f'{attribute.name} requires warning names, as in {attribute.name}(CoincidentTriggers)')
    names = []
    for argument in metadata.value:
        if not isinstance(argument, Metadata) or argument.kind != 'flag':
            _error(argument, f'{attribute.name} arguments must be warning names')
        if argument.name not in WARNINGS:
            _error(argument, f'Unknown warning {argument.name}')
        names.append(argument.name)
    return tuple(names)


def _flag(attribute: Attribute) -> bool:
    if attribute.metadata.kind != 'flag':
        _error(attribute, f'{attribute.name} must be a flag without arguments or a value')
    return True


# Extensions must explicitly define qualified names as well as unqualified ones.
# Attribute names, their namespaces, and their arguments use PascalCase.
_DIAGRAM_ITEMS = frozenset({'node', 'port', 'state', 'binding', 'constant', 'parameter',
                            'procedure'})

ATTRIBUTE_REGISTRY = {
    'Internal': AttributeDefinition(frozenset({
        'node', 'node_type', 'function', 'namespace', 'struct', 'enum',
        'resource_type', 'constant', 'parameter', 'port', 'state',
        'binding', 'struct_field', 'enum_member',
    }), _flag),
    'Unordered': AttributeDefinition(frozenset({'port'}), _flag),
    # Suppresses warnings inside the item, see WarningSink.
    'Allow': AttributeDefinition(frozenset({
        'file', 'namespace', 'node_type', 'node', 'constant', 'parameter', 'state',
        'binding', 'assertion', 'procedure', 'statement',
    }), _warning_names, repeatable=True),
    # Diagram layout, see doc/diagram-layout.md.
    'Diagram.Position': AttributeDefinition(_DIAGRAM_ITEMS, _position),
    'Diagram.Width': AttributeDefinition(_DIAGRAM_ITEMS, _size),
    'Diagram.Height': AttributeDefinition(_DIAGRAM_ITEMS, _size),
    'Diagram.DefaultWidth': AttributeDefinition(frozenset({'node_type'}), _size),
    'Diagram.DefaultHeight': AttributeDefinition(frozenset({'node_type'}), _size),
    'Diagram.Row': AttributeDefinition(frozenset({'port'}), _row),
    'Diagram.ShowValue': AttributeDefinition(frozenset({'parameter'}), _flag),
}


def _direction_name(port: model.DeclarationPort) -> str:
    from . import ast
    return 'input' if port.direction == ast.PortDirection.Input else 'output'


def _immutable(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    if isinstance(value, set):
        return frozenset(_immutable(item) for item in value)
    return value


def validate_attributes(attributes: Iterable[Attribute], target: str) -> tuple[ValidatedAttribute, ...]:
    result = []
    seen = set()
    for attribute in attributes:
        definition = ATTRIBUTE_REGISTRY.get(attribute.name)
        if definition is None:
            _error(attribute, f'Unknown attribute {attribute.name}')
        if target not in definition.targets:
            _error(attribute, f'Attribute {attribute.name} is not permitted on {target}')
        if attribute.name in seen and not definition.repeatable:
            _error(attribute, f'Duplicate attribute {attribute.name}')
        seen.add(attribute.name)
        result.append(ValidatedAttribute(
            attribute.name, _immutable(definition.validate(attribute)),
            attribute.style, attribute.source_position,
        ))
    return tuple(result)


def validate_files(files: Sequence[tuple[str | os.PathLike, ParsedFile]], *,
                   contain: Callable[[model.ast.SourceElement | None], AbstractContextManager[None]] | None = None,
                   warnings: WarningSink) -> tuple[FileMetadata, ...]:
    """Validate all metadata before the ordinary compilation passes.

    contain is CompilerCoordinator.contain when continuing after errors.
    Allow attributes are registered with the WarningSink warnings.
    """
    if contain is None:
        contain = lambda element: contextlib.nullcontext()
    from . import ast
    from .expressions import ExpressionBlock
    from .node_type import NodeType

    def allow(item: model.ast.SourceElement, attributes: Iterable[ValidatedAttribute]) -> None:
        position = item.source_position
        for attribute in attributes:
            if attribute.name == 'Allow':
                assert isinstance(attribute.value, tuple)
                assert position.meta is not None
                for name in attribute.value:
                    warnings.allow(name, position.filename, position.meta.start_pos, position.meta.end_pos)

    def visit_blocks(element: model.ast.SourceElement) -> None:
        """Validate the attributes of statements in blocks inside element."""
        if isinstance(element, ExpressionBlock):
            for statement in element.statements:
                with contain(None):
                    statement.attributes = validate_attributes(statement.outer_attributes, 'statement')
                allow(statement, statement.attributes)
        for name, child in vars(element).items():
            if name in ('source_position', 'outer_attributes', 'inner_attributes', 'attributes'):
                continue
            for item in child if isinstance(child, (tuple, list)) else (child,):
                if isinstance(item, ast.SourceElement):
                    visit_blocks(item)

    targets = {
        NodeType: 'node_type', ast.DeclarationLibrary: 'namespace',
        ast.DeclarationInstance: 'node',
        ast.DeclarationStructType: 'struct', ast.DeclarationEnumType: 'enum',
        ast.DeclarationResourceType: 'resource_type',
        ast.DeclarationConnection: 'connection',
        ast.DeclarationState: 'state',
        ast.DeclarationBinding: 'binding',
        ast.DeclarationPort: 'port',
        ast.DeclarationParameter: 'parameter',
        ast.DeclarationConstant: 'constant',
        ast.DeclarationAssertion: 'assertion',
        ast.DeclarationProcedure: 'procedure',
        ast.DeclarationInitializer: 'initializer',
        ast.DeclarationNativeFunction: 'function',
        ast.StructField: 'struct_field', ast.EnumMember: 'enum_member',
    }
    namespaces: dict[Any, Any] = {}

    def visit(item: model.ast.SourceElement, scope: tuple[str | model.NodeType, ...]) -> None:
        target = targets[type(item)]
        with contain(None):
            item.attributes = validate_attributes(item.outer_attributes + item.inner_attributes, target)
        allow(item, item.attributes)
        if not isinstance(item, (NodeType, ast.DeclarationLibrary)):
            visit_blocks(item)
        child_scope = scope
        if isinstance(item, ast.DeclarationLibrary):
            child_scope = scope + (item.name.id_str,)
            previous = namespaces.get(child_scope)
            if previous is None:
                namespaces[child_scope] = item
            else:
                previous.outer_attributes += item.outer_attributes
                previous.inner_attributes += item.inner_attributes
                merged = previous.attributes + item.attributes
                seen = set()
                with contain(None):
                    for attribute in merged:
                        if attribute.name in seen and not ATTRIBUTE_REGISTRY[attribute.name].repeatable:
                            _error(attribute, f'Duplicate attribute {attribute.name}')
                        seen.add(attribute.name)
                previous.attributes = merged
        elif isinstance(item, NodeType):
            child_scope = scope + (item,)
        if isinstance(item, ast.DeclarationConnection):
            with contain(None):
                item.edge_attributes = tuple(validate_attributes(
                    item.outer_attributes + source.outer_attributes, 'connection',
                ) for source in item.sources)
        for child in getattr(item, 'declarations', ()):
            visit(child, child_scope)
        for child in getattr(item, 'fields', ()):
            visit(child, child_scope)
        for child in getattr(item, 'members', ()):
            visit(child, child_scope)
        if isinstance(item, NodeType):
            check_rows(item.declarations)
        if isinstance(item, ast.DeclarationPort):
            check_unordered(item)

    def check_unordered(port: model.DeclarationPort) -> None:
        for attribute in getattr(port, 'attributes', ()):
            if attribute.name == 'Unordered' and not (
                    port.direction == ast.PortDirection.Input and port.port_spec.list_spec.is_list):
                with contain(None):
                    _error(attribute, 'Unordered requires a multi input')

    def check_rows(declarations: Iterable[model.Declaration]) -> None:
        """Reject two ports on the same side of a diagram item with the same row."""
        used: dict[Any, str] = {}
        for declaration in declarations:
            if not isinstance(declaration, ast.DeclarationPort):
                continue
            for attribute in getattr(declaration, 'attributes', ()):
                if attribute.name != 'Diagram.Row':
                    continue
                key = (declaration.direction, attribute.value)
                with contain(None):
                    if key in used:
                        _error(attribute, f'Port row {attribute.value} is already used by {used[key]}')
                used.setdefault(key, declaration.name_str if declaration.name is None
                                else f'{_direction_name(declaration)} {declaration.name_str}')

    metadata = []
    for filename, parsed in files:
        attributes: tuple[ValidatedAttribute, ...] = ()
        with contain(None):
            attributes = validate_attributes(parsed.inner_attributes, 'file')
        for attribute in attributes:
            if attribute.name == 'Allow':
                assert isinstance(attribute.value, tuple)
                for name in attribute.value:
                    warnings.allow(name, filename)
        metadata.append(FileMetadata(filename, parsed.inner_attributes, attributes))
        for declaration in parsed:
            visit(declaration, ())
    check_rows([declaration for _, parsed in files for declaration in parsed])
    return tuple(metadata)


# AST objects and instantiated graph objects share these metadata fields.
def copy_attributes(target: Any, source: Any) -> None:
    target.outer_attributes = source.outer_attributes
    target.inner_attributes = source.inner_attributes
    target.attributes = source.attributes
