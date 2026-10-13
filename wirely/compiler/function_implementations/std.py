"""Compile-time standard library functions."""

import wirely
from wirely.runtime.structs import StdFormatParts


def parse_format(*, context):
    value_type = context.arguments['type']
    try:
        prefix, suffix, has_value = parse_format_string(context.arguments['format'])
    except ValueError as error:
        raise context.error(str(error), argument='format') from error
    if has_value and value_type == wirely.value_types.unit:
        raise context.error('Format with {} for unit type', argument='format')
    if not has_value and value_type != wirely.value_types.unit:
        raise context.error(f'Format without {{}} for type {value_type}', argument='format')
    return context.return_type.struct_type.from_native(StdFormatParts(prefix, suffix, has_value))


def parse_format_string(text):
    """Split a format string with at most one {} into prefix and suffix."""
    parts = ['']
    pos = 0
    while pos < len(text):
        c = text[pos]
        pos += 1
        if c == '{':
            if text.startswith('{', pos):
                pos += 1
            elif text.startswith('}', pos):
                pos += 1
                if len(parts) == 2:
                    raise ValueError('Found multiple {} in format string')
                parts.append('')
                continue
            else:
                raise ValueError('Found { without } in format string; only {} is supported')
        elif c == '}':
            if not text.startswith('}', pos):
                raise ValueError('Found } without { in format string')
            pos += 1
        parts[-1] += c
    if len(parts) == 1:
        return parts[0], '', False
    return parts[0], parts[1], True
