"""Parsed attribute metadata and compiler attribute definitions."""

import math
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Callable


@dataclass(frozen=True)
class MetadataLiteral:
    value: object
    source_position: object


@dataclass(frozen=True)
class Metadata:
    path: tuple[str, ...]
    kind: str
    value: object
    source_position: object

    @property
    def name(self):
        return '.'.join(self.path)


@dataclass(frozen=True)
class Attribute:
    metadata: Metadata
    style: str
    source_position: object

    @property
    def name(self):
        return self.metadata.name


@dataclass(frozen=True)
class ValidatedAttribute:
    name: str
    value: object
    style: str
    source_position: object


@dataclass(frozen=True)
class AttributeDefinition:
    targets: frozenset[str]
    validate: Callable[[Attribute], object]
    repeatable: bool = False


@dataclass(frozen=True)
class FileMetadata:
    filename: str
    inner_attributes: tuple[Attribute, ...]
    attributes: tuple[ValidatedAttribute, ...]


class ParsedFile(list):
    """A declaration list retaining the enclosing file/body's attributes."""

    def __init__(self, declarations=(), *, inner_attributes=()):
        super().__init__(declarations)
        self.inner_attributes = tuple(inner_attributes)


def _error(source, message):
    from .ast import ProgramError, SourceElement
    raise ProgramError(SourceElement(source_position=source.source_position), message)


def _position(attribute):
    metadata = attribute.metadata
    if metadata.kind != 'list':
        _error(attribute, 'position requires x and y arguments')
    coordinates = {}
    for argument in metadata.value:
        if not isinstance(argument, Metadata) or argument.name not in ('x', 'y'):
            _error(argument, 'Unknown position argument; expected x or y')
        name = argument.name
        if name in coordinates:
            _error(argument, f'Duplicate position argument {name}')
        if argument.kind != 'value' or not isinstance(argument.value, MetadataLiteral):
            _error(argument, f'position {name} requires a finite numeric literal')
        value = argument.value.value
        if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
            _error(argument, f'position {name} requires a finite numeric literal')
        coordinates[name] = value
    if set(coordinates) != {'x', 'y'}:
        _error(attribute, 'position requires x and y arguments')
    return coordinates['x'], coordinates['y']


# Extensions must explicitly define qualified names as well as unqualified ones.
ATTRIBUTE_REGISTRY = {
    'position': AttributeDefinition(frozenset({'node'}), _position),
}


def _immutable(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    if isinstance(value, set):
        return frozenset(_immutable(item) for item in value)
    return value


def validate_attributes(attributes, target):
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


def validate_files(files):
    """Validate all metadata before the ordinary compilation passes."""
    from . import ast
    from .block_type import BlockType

    targets = {
        BlockType: 'node_type', ast.DeclarationLibrary: 'namespace',
        ast.DeclarationBlockInstantiation: 'node',
        ast.DeclarationStructType: 'struct', ast.DeclarationEnumType: 'enum',
        ast.DeclarationResourceType: 'resource_type',
        ast.DeclarationConnection: 'connection',
        ast.DeclarationStateVariable: 'state',
        ast.DeclarationTemporaryVariable: 'temporary',
        ast.DeclarationConnector: 'connector',
        ast.DeclarationParameter: 'parameter',
        ast.DeclarationConstant: 'constant',
        ast.DeclarationProcedure: 'procedure',
        ast.DeclarationInitializer: 'initializer',
        ast.StructField: 'struct_field', ast.EnumMember: 'enum_member',
    }
    namespaces = {}

    def visit(item, scope):
        target = targets[type(item)]
        item.attributes = validate_attributes(item.outer_attributes + item.inner_attributes, target)
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
                for attribute in merged:
                    if attribute.name in seen and not ATTRIBUTE_REGISTRY[attribute.name].repeatable:
                        _error(attribute, f'Duplicate attribute {attribute.name}')
                    seen.add(attribute.name)
                previous.attributes = merged
        elif isinstance(item, BlockType):
            child_scope = scope + (item,)
        if isinstance(item, ast.DeclarationConnection):
            item.edge_attributes = tuple(validate_attributes(
                item.outer_attributes + source.outer_attributes, 'connection',
            ) for source in item.sources)
        for child in getattr(item, 'declarations', ()):
            visit(child, child_scope)
        for child in getattr(item, 'fields', ()):
            visit(child, child_scope)
        for child in getattr(item, 'members', ()):
            visit(child, child_scope)

    metadata = []
    for filename, parsed in files:
        metadata.append(FileMetadata(filename, parsed.inner_attributes,
                                     validate_attributes(parsed.inner_attributes, 'file')))
        for declaration in parsed:
            visit(declaration, ())
    return tuple(metadata)


def copy_attributes(target, source):
    target.outer_attributes = source.outer_attributes
    target.inner_attributes = source.inner_attributes
    target.attributes = source.attributes
