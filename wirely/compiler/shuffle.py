"""Randomized declaration order for testing order independence."""

import random

from wirely.compiler import model
from .ast import SourceElement


# Order-independent lists in each AST class. Arguments, statements, and
# connection sources are positional, so these keep their order.
_SHUFFLED_ATTRIBUTES = {
    model.NodeType: ('declarations',),
    model.DeclarationLibrary: ('declarations',),
    model.Instantiation: ('parameters',),
    model.StructLiteral: ('fields',),
    model.DependsDeclaration: ('dependencies',),
    model.ProducesDeclaration: ('outputs',),
    model.UsesDeclaration: ('bindings',),
}

# Lists whose order defines serialization or enum default values.
_LAYOUT_ATTRIBUTES = {
    model.DeclarationStructType: ('fields',),
    model.DeclarationEnumType: ('members',),
}


def shuffle_declarations(declarations, seed, *, layout=False):
    """Shuffle a declaration list and the order-independent lists in it.

    The same seed and input give the same order. Connections keep their
    relative order, which determines the order of multi input sources.
    With layout, struct fields and enum members are also shuffled.
    """
    rng = random.Random(seed)
    attributes = {**_SHUFFLED_ATTRIBUTES, **_LAYOUT_ATTRIBUTES} if layout else _SHUFFLED_ATTRIBUTES
    _shuffle_list(rng, declarations)
    visited = set()
    for declaration in declarations:
        _visit(rng, declaration, attributes, visited)


def _shuffled(rng, items):
    result = list(items)
    rng.shuffle(result)
    connections = iter([item for item in items if isinstance(item, model.DeclarationConnection)])
    return [next(connections) if isinstance(item, model.DeclarationConnection) else item
            for item in result]


def _shuffle_list(rng, items):
    # Shuffle in place to keep list subclasses and their attributes.
    items[:] = _shuffled(rng, items)


def _visit(rng, value, attributes, visited):
    if isinstance(value, (list, tuple)):
        for item in value:
            _visit(rng, item, attributes, visited)
        return
    if not isinstance(value, SourceElement) or id(value) in visited:
        return
    visited.add(id(value))
    for cls, names in attributes.items():
        if isinstance(value, cls):
            for name in names:
                items = getattr(value, name)
                if isinstance(items, list):
                    _shuffle_list(rng, items)
                else:
                    setattr(value, name, type(items)(_shuffled(rng, items)))
    for child in vars(value).values():
        _visit(rng, child, attributes, visited)
