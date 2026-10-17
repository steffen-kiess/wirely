"""Compile-time MQTT configuration functions."""

from wirely.types.value_types import StructValue
from wirely.runtime.structs import MQTTTemplateParts

from ..native_functions import NativeFunctionContext


def supports_type(*, context: NativeFunctionContext) -> bool:
    value_type, = context.arguments
    return value_type.supports_json


def parse_template(*, context: NativeFunctionContext) -> StructValue:
    template, = context.arguments
    if template is None:
        return context.return_type.struct_type.from_native(MQTTTemplateParts('', ''))
    try:
        prefix, suffix = parse_template_string(template.value)
    except ValueError as error:
        raise context.error(str(error), argument=0) from error
    return context.return_type.struct_type.from_native(MQTTTemplateParts(prefix, suffix))


def parse_json_str_simple(s: str) -> list[tuple[bool, str]]:
    in_str = False
    backslash_count = 0
    res = []
    cur_str = ''
    for c in s:
        if c == '\\':
            backslash_count += 1
            cur_str += c
        elif c == '"' and backslash_count % 2 == 0:
            if in_str:
                cur_str += c
            if cur_str != '':
                res.append((in_str, cur_str))
            in_str = not in_str
            cur_str = ''
            if in_str:
                cur_str += c
        else:
            backslash_count = 0
            cur_str += c
    if cur_str != '':
        res.append((in_str, cur_str))
    return res


def parse_template_string(st: str) -> tuple[str, str]:
    prefix = ''
    suffix = ''
    found_ref = False
    for in_str, cur_str in parse_json_str_simple(st):
        if in_str:
            if not found_ref:
                prefix += cur_str
            else:
                suffix += cur_str
            continue

        pos = 0
        while pos < len(cur_str):
            c = cur_str[pos]
            pos += 1

            if c == '%':
                if pos == len(cur_str):
                    raise ValueError('Found lone % at end of string')
                c = cur_str[pos]
                pos += 1

                if c == '%':
                    pass
                elif c == 's':
                    if found_ref:
                        raise ValueError('Found multiple %s in template string')
                    found_ref = True
                    continue
                else:
                    raise ValueError('Unexpected sequence in template string: ' + repr('%' + c))

            if not found_ref:
                prefix += c
            else:
                suffix += c

    if not found_ref:
        raise ValueError('Got template string without %s')

    return prefix, suffix
