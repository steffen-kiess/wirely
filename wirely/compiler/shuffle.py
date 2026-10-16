"""Randomized declaration order for testing order independence."""

import hashlib
import json
import random

from wirely.compiler import model
from .ast import SourceElement


# Order-independent lists in each AST class. Arguments, statements,
# connection sources, and struct literal fields, which evaluate in written
# order, keep their order.
_SHUFFLED_ATTRIBUTES = {
    model.NodeType: ('declarations',),
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
_LAYOUT_ATTRIBUTES = {
    model.DeclarationStructType: ('fields',),
}


def shuffled_source_order(order, seed):
    """Return a seeded sort key independent of traversal and Python hash seeds."""
    data = json.dumps((seed, *order), ensure_ascii=True).encode('ascii')
    return (hashlib.sha256(data).digest(), *order)


def shuffle_declarations(declarations, seed, *, layout=False):
    """Shuffle a declaration list and the order-independent lists in it.

    The same seed and input give the same order. Connections keep their
    relative order, which determines the order of multi input sources.
    With layout, fields of structs and members of closed enums that are not
    native are also shuffled.
    """
    rng = random.Random(seed)
    attributes = {**_SHUFFLED_ATTRIBUTES, **_LAYOUT_ATTRIBUTES} if layout else _SHUFFLED_ATTRIBUTES
    _shuffle_list(rng, declarations)
    visited = set()
    for declaration in declarations:
        _visit(rng, declaration, attributes, visited, layout)


def _shuffled(rng, items):
    result = list(items)
    rng.shuffle(result)
    connections = iter([item for item in items if isinstance(item, model.DeclarationConnection)])
    return [next(connections) if isinstance(item, model.DeclarationConnection) else item
            for item in result]


def _shuffle_list(rng, items):
    # Shuffle in place to keep list subclasses and their attributes.
    items[:] = _shuffled(rng, items)


def _visit(rng, value, attributes, visited, layout):
    if isinstance(value, (list, tuple)):
        for item in value:
            _visit(rng, item, attributes, visited, layout)
        return
    if not isinstance(value, SourceElement) or id(value) in visited:
        return
    visited.add(id(value))
    for cls, names in attributes.items():
        if isinstance(value, cls):
            # Closed member order defines member numbers.
            if cls is model.DeclarationEnumType and not value.is_open and (value.is_native or not layout):
                continue
            if cls is model.DeclarationStructType and value.is_native:
                continue
            for name in names:
                items = getattr(value, name)
                if isinstance(items, list):
                    _shuffle_list(rng, items)
                else:
                    setattr(value, name, type(items)(_shuffled(rng, items)))
    for child in vars(value).values():
        _visit(rng, child, attributes, visited, layout)
