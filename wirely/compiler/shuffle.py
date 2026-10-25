"""Randomized declaration order for testing order independence."""

import dataclasses
import hashlib
import json
import random
from collections.abc import Sequence
from typing import Any, TypeVar

from wirely.compiler import model
from .ast.base import SourceElement, SyntaxElement
from .attributes import ParsedFile


T = TypeVar('T')


# Order-independent lists in each AST class. Arguments, statements,
# connection sources, and struct literal fields, which evaluate in written
# order, keep their order.
_SHUFFLED_ATTRIBUTES: dict[type, tuple[str, ...]] = {
    model.DeclarationNodeType: ('declarations',),
    model.DeclarationLibrary: ('declarations',),
    model.Instantiation: ('parameters',),
    model.ReadsDeclaration: ('entries',),
    model.WritesDeclaration: ('outputs',),
    model.DeclarationEnumType: ('members',),
}

# Lists whose order defines the runtime representation. Closed enum members
# also define it and are handled in _visit. The order of native struct fields
# and native closed enum members is the contract with native code, and opaque
# struct fields must match their built-in type, so they are never shuffled.
_LAYOUT_ATTRIBUTES: dict[type, tuple[str, ...]] = {
    model.DeclarationStructType: ('fields',),
}


def shuffled_source_order(order: tuple[str, int], seed: int) -> tuple[bytes, str, int]:
    """Return a seeded sort key independent of traversal and Python hash seeds."""
    data = json.dumps((seed, *order), ensure_ascii=True).encode('ascii')
    return (hashlib.sha256(data).digest(), *order)


def shuffle_declarations(declarations: Sequence[model.Declaration], seed: int, *,
                         layout: bool = False) -> list[model.Declaration]:
    """Return shuffled copies, preserving connection order and native layouts."""
    result, _ = _shuffle(declarations, seed, layout)
    return result


def shuffle_files(parsed_files: Sequence[tuple[T, ParsedFile]], seed: int, *,
                  layout: bool = False) -> tuple[list[tuple[T, ParsedFile]], list[model.Declaration]]:
    """Shuffle file declarations together and retain their enclosing file metadata."""
    declarations = [declaration for _, parsed in parsed_files for declaration in parsed]
    result, visited = _shuffle(declarations, seed, layout)
    files = [(filename, ParsedFile((visited[id(declaration)][1] for declaration in parsed),
                                   inner_attributes=parsed.inner_attributes))
             for filename, parsed in parsed_files]
    return files, result


def _shuffle(declarations: Sequence[model.Declaration], seed: int, layout: bool
             ) -> tuple[list[model.Declaration], dict[int, tuple[Any, Any]]]:
    rng = random.Random(seed)
    attributes = {**_SHUFFLED_ATTRIBUTES, **_LAYOUT_ATTRIBUTES} if layout else _SHUFFLED_ATTRIBUTES
    visited: dict[int, tuple[Any, Any]] = {}
    result = [_visit(rng, declaration, attributes, visited, layout)
              for declaration in _shuffled(rng, declarations)]
    return result, visited


def _shuffled(rng: random.Random, items: Sequence[T]) -> list[T]:
    result = list(items)
    rng.shuffle(result)
    connections = iter([item for item in items if isinstance(item, model.DeclarationConnection)])
    return [next(connections) if isinstance(item, model.DeclarationConnection) else item
            for item in result]


def _visit(rng: random.Random, value: T, attributes: dict[type, tuple[str, ...]],
           visited: dict[int, tuple[object, object]], layout: bool) -> T:
    """Return a shuffled copy of a syntax element or sequence.

    visited maps the ids of visited elements to the elements, which keeps their ids unique, and their results.
    """
    if isinstance(value, list):
        return [_visit(rng, item, attributes, visited, layout) for item in value]  # type: ignore[return-value]
    if isinstance(value, tuple):
        items = tuple(_visit(rng, item, attributes, visited, layout) for item in value)
        if any(new is not old for new, old in zip(items, value)):
            return type(value)(items)  # type: ignore[return-value]
        return value
    if not isinstance(value, SourceElement):
        return value
    if id(value) in visited:
        return visited[id(value)][1]  # type: ignore[return-value]
    visited[id(value)] = (value, value)
    changes: dict[str, Any] = {}
    for cls, names in attributes.items():
        if isinstance(value, cls):
            # Closed member order defines member numbers.
            if cls is model.DeclarationEnumType:
                assert isinstance(value, model.DeclarationEnumType)
                if not value.is_open and (value.is_native or not layout):
                    continue
            if cls is model.DeclarationStructType:
                assert isinstance(value, model.DeclarationStructType)
                if value.is_native or value.is_opaque:
                    continue
            for name in names:
                items = getattr(value, name)
                changes[name] = type(items)(_shuffled(rng, items))
    # Fields derived in __post_init__ are recomputed by dataclasses.replace.
    derived = {field.name for field in dataclasses.fields(value) if not field.init} \
        if dataclasses.is_dataclass(value) else set()
    for name, child in vars(value).items():
        if name in derived:
            continue
        child = changes.get(name, child)
        visited_child = _visit(rng, child, attributes, visited, layout)
        if visited_child is not child or name in changes:
            changes[name] = visited_child
    if not changes:
        return value
    if isinstance(value, SyntaxElement):
        result = dataclasses.replace(value, **changes)
        visited[id(value)] = (value, result)
        return result
    assert not changes, f'Cannot replace mutable source element {type(value).__name__}'
    return value
