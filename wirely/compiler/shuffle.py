"""Randomized declaration order for testing order independence."""

import dataclasses
import hashlib
import json
import random
from collections.abc import Sequence
from typing import Any, TypeVar

from wirely.compiler import model
from .ast import SourceElement, SyntaxElement


T = TypeVar('T')


# Order-independent lists in each AST class. Arguments, statements,
# connection sources, and struct literal fields, which evaluate in written
# order, keep their order.
_SHUFFLED_ATTRIBUTES: dict[type, tuple[str, ...]] = {
    model.DeclarationNodeType: ('declarations',),
    model.DeclarationLibrary: ('declarations',),
    model.Instantiation: ('parameters',),
    model.DependsDeclaration: ('dependencies',),
    model.ProducesDeclaration: ('outputs',),
    model.UsesDeclaration: ('bindings',),
    model.DeclarationEnumType: ('members',),
}

# Lists whose order defines the runtime representation. Closed enum members
# also define it and are handled in _visit. The order of native struct fields
# and native closed enum members is the contract with native code, so they
# are never shuffled.
_LAYOUT_ATTRIBUTES: dict[type, tuple[str, ...]] = {
    model.DeclarationStructType: ('fields',),
}


def shuffled_source_order(order: tuple[str, int], seed: int) -> tuple[bytes, str, int]:
    """Return a seeded sort key independent of traversal and Python hash seeds."""
    data = json.dumps((seed, *order), ensure_ascii=True).encode('ascii')
    return (hashlib.sha256(data).digest(), *order)


def shuffle_declarations(declarations: list[model.Declaration], seed: int, *, layout: bool = False) -> None:
    """Shuffle a declaration list and the order-independent lists in it.

    The same seed and input give the same order. Connections keep their
    relative order, which determines the order of multi input sources.
    With layout, fields of structs and members of closed enums that are not
    native are also shuffled.
    """
    rng = random.Random(seed)
    attributes = {**_SHUFFLED_ATTRIBUTES, **_LAYOUT_ATTRIBUTES} if layout else _SHUFFLED_ATTRIBUTES
    _shuffle_list(rng, declarations)
    visited: dict[int, tuple[object, object]] = {}
    _visit(rng, declarations, attributes, visited, layout)


def _shuffled(rng: random.Random, items: Sequence[T]) -> list[T]:
    result = list(items)
    rng.shuffle(result)
    connections = iter([item for item in items if isinstance(item, model.DeclarationConnection)])
    return [next(connections) if isinstance(item, model.DeclarationConnection) else item
            for item in result]


def _shuffle_list(rng: random.Random, items: list[T]) -> None:
    # Shuffle in place to keep list subclasses and their attributes.
    items[:] = _shuffled(rng, items)


def _visit(rng: random.Random, value: T, attributes: dict[type, tuple[str, ...]],
           visited: dict[int, tuple[object, object]], layout: bool) -> T:
    """Shuffle the lists in value and return it, or a shuffled copy of a syntax element.

    visited maps the ids of visited elements to the elements, which keeps their ids unique, and their results.
    """
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _visit(rng, item, attributes, visited, layout)
        return value
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
                if value.is_native:
                    continue
            for name in names:
                items = getattr(value, name)
                if isinstance(items, list):
                    _shuffle_list(rng, items)
                else:
                    changes[name] = type(items)(_shuffled(rng, items))
    for name, child in vars(value).items():
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
    for name, child in changes.items():
        setattr(value, name, child)
    return value
