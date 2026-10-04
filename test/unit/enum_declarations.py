import contextlib
import io
from pathlib import Path
from unittest import TestCase

import wirely
import wirely.compiler
from wirely.compiler import program as program_compiler


graph = engine.program.graph
signed = graph.enum_types['Signed']
mode = graph.libraries['Example'].enum_types['Mode']
other = graph.libraries['Other'].enum_types['Mode']
assert str(mode) == 'Example.Mode'
assert list(signed.python_type.__members__) == ['Min', 'Zero', 'Hex', 'Octal', 'enum']
assert list(graph.enum_types['Empty'].python_type) == []
assert signed.parse('Hex').value == 42
assert signed.parse('Octal').value == 63
assert getattr(signed.python_type, 'enum').value == 127
assert mode is not other
assert mode.python_type.On is not other.python_type.On
engine.step()
for name in ['root', 'forwarded', 'nested']:
    engine.assert_value_is(name, mode.parse('On'))
engine.assert_value_is('signed', signed.parse('Min'))
engine.assert_value_is('escaped', graph.enum_types['enum'].parse('node'))
engine.assert_value_is('other', other.parse('On'))
assert engine.program.instances['wrapped_modes'].parameters['Value'] == (
    wirely.Some(mode.parse('On')), None,
)
assert mode.json_encode(mode.parse('On'), None) == 'On'
assert mode.json_decode('On', None) is mode.parse('On')
assert signed.serialize(signed.parse('Min')) == b'\x80'
assert signed.deserialize(b'\x80') is signed.parse('Min')


def compile_source(source, *, extra=()):
    compiled = wirely.compiler.compile_strs([
        ('wirely/lib/std.wrl', Path('wirely/lib/std.wrl').read_text()),
        ('<enum-declarations>', source),
        *extra,
    ])
    if compiled is not None and program_compiler.compile(compiled) is None:
        return None
    return compiled


# Exercise every width, including integers larger than signed int64.
for prefix in ['int', 'uint']:
    for bits in [8, 16, 32, 64]:
        underlying = getattr(wirely.types, f'{prefix}{bits}')
        source = f'''
node value: Std.Constant {{ Type: Boundary, Value: Boundary.Maximum }}
enum type Boundary: {underlying} {{
  Minimum = {underlying.min_value},
  Maximum = {underlying.max_value},
}}
'''
        compiled = compile_source(source)
        assert compiled is not None
        enum_type = compiled.enum_types['Boundary']
        assert enum_type.underlying_type is underlying
        for name, number in [('Minimum', underlying.min_value),
                             ('Maximum', underlying.max_value)]:
            member = enum_type.parse(name)
            assert member.value == number
            expected = number.to_bytes(bits // 8, 'little', signed=prefix == 'int')
            assert enum_type.serialize(member) == expected
            assert enum_type.deserialize(expected) is member
        runtime = wirely.Engine(program_compiler.compile(compiled))
        runtime.step()
        runtime.assert_value_is('value', enum_type.parse('Maximum'))

# Definitions in later files resolve, and compilations own their enum classes.
source = 'node value: Std.Constant { Type: Fresh, Value: Fresh.One }'
extra = (('<later-file>', 'enum type Fresh: int8 { One = 1, }'),)
first = compile_source(source, extra=extra)
second = compile_source(source, extra=extra)
assert first is not None and second is not None
assert first.enum_types['Fresh'] is not second.enum_types['Fresh']
assert first.enum_types['Fresh'].python_type is not second.enum_types['Fresh'].python_type
with TestCase().assertRaises(ValueError):
    wirely.ValueType(first.enum_types['Fresh']).validate(
        second.enum_types['Fresh'].parse('One'),
    )
assert 'Fresh' not in wirely.types.type_by_name
with contextlib.redirect_stdout(io.StringIO()) as output:
    assert compile_source(source) is None
assert 'Cannot find type Fresh' in output.getvalue()
