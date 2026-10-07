"""Source-preserving edits of attributes on nodes, node types, and connections.

The functions only parse the given text, so they also work on a single file
that does not compile, for example because of unknown types or missing
libraries. Everything outside the edited attributes is left unchanged.
"""

import math
import re
from dataclasses import dataclass

import lark

from . import ast
from . import parser
from .block_type import BlockType


@dataclass(frozen=True)
class Node:
    """A node instance, addressed by enclosing namespaces/node types and name."""
    path: tuple[str, ...]


@dataclass(frozen=True)
class NodeType:
    """A node type, addressed by enclosing namespaces/node types and name."""
    path: tuple[str, ...]


@dataclass(frozen=True)
class Connection:
    """The edge from source to target in a connect statement within scope.

    References are written as in the source, e.g. 'node.output' or
    'default input'.
    """
    scope: tuple[str, ...]
    source: str
    target: str


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


def format_metadata(name, **arguments):
    """Return attribute metadata such as 'position(x = 1, y = 2.5)'.

    Argument values must be integers, finite floats, or booleans.
    """
    if not arguments:
        return name
    formatted = ', '.join(f'{key} = {_format_literal(value)}' for key, value in arguments.items())
    return f'{name}({formatted})'


def add_attribute(text, target, metadata, *, filename='<input>'):
    """Add an attribute, preferring an inner attribute on its own line."""
    _metadata_name(metadata)
    site = _locate(parser.parse_str(text, filename=filename), target, text)
    return _insert(text, site, metadata)


def set_attribute(text, target, metadata, *, filename='<input>'):
    """Replace the attributes named like metadata in place, or add one."""
    name = _metadata_name(metadata)
    site = _locate(parser.parse_str(text, filename=filename), target, text)
    existing = _matching(site, name)
    if not existing:
        return _insert(text, site, metadata)
    first, *rest = existing
    for attribute in sorted(rest, key=_start, reverse=True):
        text = _remove_span(text, _start(attribute), _end(attribute))
    opening = '#![' if first.style == 'inner' else '#['
    return text[:_start(first)] + opening + metadata + ']' + text[_end(first):]


def remove_attribute(text, target, name, *, filename='<input>'):
    """Remove all attributes called name; the text is unchanged if none exist."""
    site = _locate(parser.parse_str(text, filename=filename), target, text)
    for attribute in sorted(_matching(site, name), key=_start, reverse=True):
        text = _remove_span(text, _start(attribute), _end(attribute))
    return text


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


def _reference_text(reference):
    return '.'.join(identifier.id_str for identifier in reference.identifiers)


def _walk(declarations, scope):
    for item in declarations:
        yield scope, item
        if isinstance(item, (ast.DeclarationLibrary, BlockType)):
            yield from _walk(item.declarations, scope + (item.name.id_str,))


def _single(matches, target):
    if not matches:
        raise ValueError(f'No match for {target}')
    if len(matches) > 1:
        raise ValueError(f'Multiple matches for {target}')
    return matches[0]


def _locate(declarations, target, text):
    if isinstance(target, Node):
        declaration = _single([
            item for scope, item in _walk(declarations, ())
            if isinstance(item, ast.DeclarationBlockInstantiation)
            and scope + (item.name.id_str,) == tuple(target.path)
        ], target)
        instantiation = declaration.instantiation
        return _body_site(text, declaration,
                          declaration.outer_attributes + instantiation.inner_attributes,
                          _end(instantiation.block_class_name), _end(instantiation) - 1)
    if isinstance(target, NodeType):
        declaration = _single([
            item for scope, item in _walk(declarations, ())
            if isinstance(item, BlockType)
            and scope + (item.name.id_str,) == tuple(target.path)
        ], target)
        return _body_site(text, declaration,
                          declaration.outer_attributes + declaration.inner_attributes,
                          _end(declaration.name), _end(declaration) - 1)
    if isinstance(target, Connection):
        declaration, source = _single([
            (item, source) for scope, item in _walk(declarations, ())
            if isinstance(item, ast.DeclarationConnection) and scope == tuple(target.scope)
            and _reference_text(item.target) == target.target
            for source in item.sources if _reference_text(source) == target.source
        ], target)
        if len(declaration.sources) == 1:
            return _Site(list(declaration.outer_attributes + source.outer_attributes), [],
                         'outer', _start(declaration))
        return _Site(list(source.outer_attributes), list(declaration.outer_attributes),
                     'source', _start(source))
    raise TypeError(f'Unsupported attribute target {target!r}')


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
        return _insert_inner(text, site, f'#![{metadata}]')
    attribute = f'#[{metadata}]'
    anchor = site.anchor
    before = text[_line_start(text, anchor):anchor]
    if site.kind == 'outer' and not before.strip():
        return text[:anchor] + attribute + '\n' + before + text[anchor:]
    return text[:anchor] + attribute + ' ' + text[anchor:]


def _insert_inner(text, site, attribute):
    open_brace, close_brace, anchor = site.open_brace, site.close_brace, site.anchor
    outer_indent = _indentation(text, site.declaration_start)
    if '\n' not in text[open_brace:close_brace]:
        indent = outer_indent + _indent_unit(text)
        lines = [line for line in (text[open_brace + 1:anchor].strip(), attribute,
                                   text[anchor:close_brace].strip()) if line]
        body = ''.join(f'\n{indent}{line}' for line in lines)
        return text[:open_brace + 1] + body + '\n' + outer_indent + text[close_brace:]

    line_end = _line_end(text, anchor)
    rest = text[anchor:line_end].strip()
    if rest and not rest.startswith('//'):
        separator = '' if text[anchor].isspace() else ' '
        return text[:anchor] + ' ' + attribute + separator + text[anchor:]

    insert_at = line_end + 1
    content = _skip_whitespace(text, insert_at)
    if content < close_brace:
        indent = _indentation(text, content)
    else:
        indent = outer_indent + _indent_unit(text)
    return text[:insert_at] + indent + attribute + '\n' + text[insert_at:]


def _skip_whitespace(text, position):
    while position < len(text) and text[position] in ' \t\f\r\n':
        position += 1
    return position


def _remove_span(text, start, end):
    line_start = _line_start(text, start)
    line_end = _line_end(text, end)
    if not text[end:line_end].strip():
        if not text[line_start:start].strip():
            return text[:line_start] + text[line_end + 1:]
        while start > line_start and text[start - 1] in ' \t':
            start -= 1
        return text[:start] + text[end:]
    while end < line_end and text[end] in ' \t':
        end += 1
    return text[:start] + text[end:]
