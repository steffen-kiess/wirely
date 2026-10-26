"""Parsed attribute metadata and compiler attribute definitions."""

from __future__ import annotations

import contextlib
import os
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, NoReturn, TypeGuard, overload

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


@dataclass(frozen=True)
class ValidatedMetadata:
    """The validated attributes of the files of a compilation and their declarations."""

    files: tuple[FileMetadata, ...] = ()
    # By declaration, including struct fields and enum members.
    attributes: Mapping[model.SourceElement, tuple[ValidatedAttribute, ...]] = field(
        default_factory=lambda: MappingProxyType({}))
    # The attributes of each edge of a connection, in source order.
    edge_attributes: Mapping[model.DeclarationConnection, tuple[tuple[ValidatedAttribute, ...], ...]] = field(
        default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, eq=False, init=False)
class ParsedFile(Sequence['model.Declaration']):
    """An immutable declaration sequence with its file/body attributes."""

    declarations: tuple[model.Declaration, ...]
    inner_attributes: tuple[Attribute, ...]

    def __init__(self, declarations: Iterable[model.Declaration] = (), *,
                 inner_attributes: Iterable[Attribute] = ()) -> None:
        object.__setattr__(self, 'declarations', tuple(declarations))
        object.__setattr__(self, 'inner_attributes', tuple(inner_attributes))

    def __len__(self) -> int:
        return len(self.declarations)

    @overload
    def __getitem__(self, index: int) -> model.Declaration: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[model.Declaration, ...]: ...

    def __getitem__(self, index: int | slice) -> model.Declaration | tuple[model.Declaration, ...]:
        return self.declarations[index]


def _error(source: Attribute | ValidatedAttribute | Metadata | MetadataLiteral | model.SourceElement,
           message: str) -> NoReturn:
    from .ast.base import ProgramError, SourceElement
    raise ProgramError(SourceElement(source_position=source.source_position), message)


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and type(value) in (int, float) and math.isfinite(value)


def _position(attribute: Attribute) -> tuple[int | float, int | float]:
    metadata = attribute.metadata
    if metadata.kind != 'list':
        _error(attribute, f'`{attribute.name}` requires `X` and `Y` arguments')
    coordinates = {}
    for argument in metadata.value:
        if not isinstance(argument, Metadata) or argument.name not in ('X', 'Y'):
            _error(argument, f'Unknown `{attribute.name}` argument; expected `X` or `Y`')
        name = argument.name
        if name in coordinates:
            _error(argument, f'Duplicate `{attribute.name}` argument `{name}`')
        if argument.kind != 'value' or not isinstance(argument.value, MetadataLiteral) \
                or not _number(argument.value.value):
            _error(argument, f'`{attribute.name}` `{name}` requires a finite numeric literal')
        assert _number(argument.value.value)
        coordinates[name] = argument.value.value
    if set(coordinates) != {'X', 'Y'}:
        _error(attribute, f'`{attribute.name}` requires `X` and `Y` arguments')
    return coordinates['X'], coordinates['Y']


def _literal(attribute: Attribute) -> object:
    metadata = attribute.metadata
    if metadata.kind != 'value' or not isinstance(metadata.value, MetadataLiteral):
        return None
    return metadata.value.value


def _json_name(attribute: Attribute) -> str:
    value = _literal(attribute)
    if not isinstance(value, str):
        _error(attribute, '`JSON.Name` requires a string literal')
    return value


def _size(attribute: Attribute) -> int | float:
    value = _literal(attribute)
    if not _number(value) or value <= 0:
        _error(attribute, f'`{attribute.name}` requires a positive finite number, as in `{attribute.name} = 8`')
    return value


def _row(attribute: Attribute) -> int:
    value = _literal(attribute)
    if type(value) is not int or value < 0:
        _error(attribute, f'`{attribute.name}` requires a non-negative integer, as in `{attribute.name} = 0`')
    return value


def _warning_names(attribute: Attribute) -> tuple[str, ...]:
    from .diagnostics import WARNINGS

    metadata = attribute.metadata
    if metadata.kind != 'list' or not metadata.value:
        _error(attribute, f'`{attribute.name}` requires warning names, as in `{attribute.name}(CoincidentTriggers)`')
    names = []
    for argument in metadata.value:
        if not isinstance(argument, Metadata) or argument.kind != 'flag':
            _error(argument, f'`{attribute.name}` arguments must be warning names')
        if argument.name not in WARNINGS:
            _error(argument, f'Unknown warning `{argument.name}`')
        names.append(argument.name)
    return tuple(names)


def _flag(attribute: Attribute) -> bool:
    if attribute.metadata.kind != 'flag':
        _error(attribute, f'`{attribute.name}` must be a flag without arguments or a value')
    return True


# Extensions must explicitly define qualified names as well as unqualified ones.
# Attribute names, their namespaces, and their arguments use PascalCase.
_DIAGRAM_ITEMS = frozenset({'node', 'port', 'state', 'binding', 'constant', 'parameter',
                            'procedure'})

ATTRIBUTE_REGISTRY = {
    'JSON': AttributeDefinition(frozenset({'enum', 'struct'}), _flag),
    'JSON.Name': AttributeDefinition(frozenset({'enum_member', 'struct_field'}), _json_name),
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
    from .ast.specifiers import PortDirection
    return 'input' if port.direction == PortDirection.Input else 'output'


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
            _error(attribute, f'Unknown attribute `{attribute.name}`')
        if target not in definition.targets:
            _error(attribute, f'Attribute `{attribute.name}` is not permitted on {target}')
        if attribute.name in seen and not definition.repeatable:
            _error(attribute, f'Duplicate attribute `{attribute.name}`')
        seen.add(attribute.name)
        result.append(ValidatedAttribute(
            attribute.name, _immutable(definition.validate(attribute)),
            attribute.style, attribute.source_position,
        ))
    return tuple(result)


def validate_files(files: Sequence[tuple[str | os.PathLike, ParsedFile]], *,
                   contain: Callable[[model.SourceElement | None], AbstractContextManager[None]] | None = None,
                   warnings: WarningSink) -> ValidatedMetadata:
    """Validate all metadata before the ordinary compilation passes.

    contain is CompilerCoordinator.contain when continuing after errors.
    Allow attributes are registered with the WarningSink warnings.
    """
    if contain is None:
        contain = lambda element: contextlib.nullcontext()
    from .ast.parameter_declarations import DeclarationAssertion, DeclarationConstant, DeclarationParameter
    from .ast.nodes import DeclarationBinding, DeclarationConnection, DeclarationInstance, DeclarationState
    from .ast.type_declarations import (
        DeclarationEnumType,
        DeclarationResourceType,
        DeclarationStructType,
        EnumMember,
        StructField,
    )
    from .ast.procedures import DeclarationInitializer, DeclarationLoadAssertion, DeclarationProcedure
    from .ast.library import DeclarationLibrary
    from .ast.functions import DeclarationNativeFunction
    from .ast.node_types import DeclarationNodeType
    from .ast.operators import DeclarationOperator
    from .ast.ports import DeclarationPort
    from .ast.specifiers import PortDirection
    from .ast.base import SourceElement
    from .expressions import ExpressionBlock

    def allow(item: model.SourceElement, attributes: Iterable[ValidatedAttribute]) -> None:
        position = item.source_position
        for attribute in attributes:
            if attribute.name == 'Allow':
                assert isinstance(attribute.value, tuple)
                assert position.meta is not None
                for name in attribute.value:
                    warnings.allow(name, position.filename, position.meta.start_pos, position.meta.end_pos)

    def visit_blocks(element: model.SourceElement) -> None:
        """Validate the attributes of statements in blocks inside element."""
        if isinstance(element, ExpressionBlock):
            for statement in element.statements:
                statement_attributes: tuple[ValidatedAttribute, ...] = ()
                with contain(None):
                    statement_attributes = validate_attributes(statement.outer_attributes, 'statement')
                allow(statement, statement_attributes)
        for name, child in vars(element).items():
            if name in ('source_position', 'outer_attributes', 'inner_attributes'):
                continue
            for item in child if isinstance(child, (tuple, list)) else (child,):
                if isinstance(item, SourceElement):
                    visit_blocks(item)

    targets = {
        DeclarationNodeType: 'node_type', DeclarationLibrary: 'namespace',
        DeclarationInstance: 'node',
        DeclarationStructType: 'struct', DeclarationEnumType: 'enum',
        DeclarationResourceType: 'resource_type',
        DeclarationConnection: 'connection',
        DeclarationState: 'state',
        DeclarationBinding: 'binding',
        DeclarationPort: 'port',
        DeclarationParameter: 'parameter',
        DeclarationConstant: 'constant',
        DeclarationAssertion: 'assertion',
        DeclarationLoadAssertion: 'assertion',
        DeclarationProcedure: 'procedure',
        DeclarationInitializer: 'initializer',
        DeclarationNativeFunction: 'function',
        DeclarationOperator: 'operator',
        StructField: 'struct_field', EnumMember: 'enum_member',
    }
    # The attributes of the namespace blocks so far, by namespace path.
    namespaces: dict[tuple[str | model.DeclarationNodeType, ...], tuple[ValidatedAttribute, ...]] = {}
    attributes: dict[model.SourceElement, tuple[ValidatedAttribute, ...]] = {}
    edge_attributes: dict[model.DeclarationConnection, tuple[tuple[ValidatedAttribute, ...], ...]] = {}

    def visit(item: model.SourceElement, scope: tuple[str | model.DeclarationNodeType, ...]) -> None:
        target = targets[type(item)]
        item_attributes: tuple[ValidatedAttribute, ...] = ()
        with contain(None):
            item_attributes = validate_attributes(item.outer_attributes + item.inner_attributes, target)
        attributes[item] = item_attributes
        allow(item, item_attributes)
        if not isinstance(item, (DeclarationNodeType, DeclarationLibrary)):
            visit_blocks(item)
        child_scope = scope
        if isinstance(item, DeclarationLibrary):
            child_scope = scope + (item.name.id_str,)
            previous = namespaces.get(child_scope)
            if previous is None:
                namespaces[child_scope] = item_attributes
            else:
                merged = previous + item_attributes
                seen = set()
                with contain(None):
                    for attribute in merged:
                        if attribute.name in seen and not ATTRIBUTE_REGISTRY[attribute.name].repeatable:
                            _error(attribute, f'Duplicate attribute `{attribute.name}`')
                        seen.add(attribute.name)
                namespaces[child_scope] = merged
        elif isinstance(item, DeclarationNodeType):
            child_scope = scope + (item,)
        if isinstance(item, DeclarationConnection):
            with contain(None):
                edge_attributes[item] = tuple(validate_attributes(
                    item.outer_attributes + source.outer_attributes, 'connection',
                ) for source in item.sources)
        for child in getattr(item, 'declarations', ()):
            visit(child, child_scope)
        for child in getattr(item, 'fields', ()):
            visit(child, child_scope)
        for child in getattr(item, 'members', ()):
            visit(child, child_scope)
        if isinstance(item, DeclarationNodeType):
            check_rows(item.declarations)
        if isinstance(item, DeclarationPort):
            check_unordered(item)

    def check_unordered(port: model.DeclarationPort) -> None:
        for attribute in attributes[port]:
            if attribute.name == 'Unordered' and not (
                    port.direction == PortDirection.Input and port.port_spec.list_spec.is_list):
                with contain(None):
                    _error(attribute, '`Unordered` requires a `multi` input')

    def check_rows(declarations: Iterable[model.Declaration]) -> None:
        """Reject two ports on the same side of a diagram item with the same row."""
        used: dict[Any, str] = {}
        for declaration in declarations:
            if not isinstance(declaration, DeclarationPort):
                continue
            for attribute in attributes.get(declaration, ()):
                if attribute.name != 'Diagram.Row':
                    continue
                key = (declaration.direction, attribute.value)
                with contain(None):
                    if key in used:
                        _error(attribute, f'Port row {attribute.value} is already used by `{used[key]}`')
                used.setdefault(key, declaration.name_str if declaration.name is None
                                else f'{_direction_name(declaration)} {declaration.name_str}')

    metadata = []
    for filename, parsed in files:
        file_attributes: tuple[ValidatedAttribute, ...] = ()
        with contain(None):
            file_attributes = validate_attributes(parsed.inner_attributes, 'file')
        for attribute in file_attributes:
            if attribute.name == 'Allow':
                assert isinstance(attribute.value, tuple)
                for name in attribute.value:
                    warnings.allow(name, filename)
        metadata.append(FileMetadata(filename, parsed.inner_attributes, file_attributes))
        for declaration in parsed:
            visit(declaration, ())
    check_rows([declaration for _, parsed in files for declaration in parsed])
    return ValidatedMetadata(tuple(metadata), MappingProxyType(attributes), MappingProxyType(edge_attributes))


# AST objects and instantiated graph objects share these metadata fields.
def copy_attributes(target: Any, source: Any, attributes: tuple[ValidatedAttribute, ...]) -> None:
    """Copy the parsed attributes of source and set the validated attributes of target."""
    target.outer_attributes = source.outer_attributes
    target.inner_attributes = source.inner_attributes
    target.attributes = attributes
