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
node value : Std.Constant(Type = Boundary, Value = Boundary.Maximum);
enum type Boundary : {underlying} {{
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
source = 'node value : Std.Constant(Type = Fresh, Value = Fresh.One);'
extra = (('<later-file>', 'enum type Fresh : int8 { One = 1, }'),)
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


invalid = [
    ('enum type Bad : boolean { One = 1 }', 'Enum underlying type must be a supported integer type'),
    ('enum type Bad : Option[int8] { One = 1 }', 'Enum underlying type must be a supported integer type'),
    ('enum type Bad : Missing { One = 1 }', 'Enum underlying type must be a supported integer type'),
    ('enum type Bad : int8 { One = true }', 'Invalid int value'),
    ('enum type Bad : int8 { One = "1" }', 'Invalid int value'),
    ('enum type Bad : int8 { One = 1.0 }', 'Invalid int value'),
    ('enum type Bad : int8 { One = 128 }', 'Int value out of range'),
    ('enum type Bad : int8 { One = -129 }', 'Int value out of range'),
    ('enum type Bad : uint8 { One = -1 }', 'Int value out of range'),
    ('enum type Bad : uint64 { One = 18446744073709551616 }', 'Int value out of range'),
    ('enum type Bad : int8 { One = 1, One = 2 }', 'Duplicate enum member One'),
    ('enum type Bad : int8 { One = 1, Two = 1 }', 'Duplicate enum value 1'),
    ('enum type Bad : int8 {} enum type Bad : int8 {}', 'Enum type Bad conflicts with enum type'),
    ('namespace A { enum type Bad : int8 {} } namespace A { enum type Bad : int8 {} }',
     'Enum type Bad conflicts with enum type'),
    ('enum type int8 : int8 {}', 'Enum type int8 conflicts with built-in type'),
    ('enum type List : int8 {}', 'Enum type List conflicts with built-in type'),
    ('node type Bad {} enum type Bad : int8 {}', 'Enum type Bad conflicts with node type'),
    ('enum type Bad : int8 {} node type Bad {}', 'Node type Bad conflicts with enum type'),
    ('resource type Bad; enum type Bad : int8 {}', 'Enum type Bad conflicts with resource type'),
    ('enum type Bad : int8 {} resource type Bad;', 'Resource type Bad conflicts with enum type'),
    ('namespace Bad {} enum type Bad : int8 {}', 'Enum type Bad conflicts with namespace'),
    ('enum type Bad : int8 {} namespace Bad {}', 'Namespace Bad conflicts with enum type'),
    ('node type Bad { enum type Inner : int8 {} }', 'Enum type declaration not allowed inside node type'),
    ('enum type Bad : int8 { Missing }', 'Error in <enum-declarations>:'),
    ('enum type Bad : int8 { One = 1; }', 'Error in <enum-declarations>:'),
    ('enum type Bad : int8 { One = 1 + 2 }', 'Error in <enum-declarations>:'),
    ('enum type Bad : int8 { One = 1 } node n : Std.Constant(Type = Bad, Value = Bad.Two);',
     "Unknown member 'Two' of Bad"),
    ('''namespace A { enum type Mode : int8 { One = 1 } }
namespace B { enum type Mode : int8 { One = 1 } }
node n : Std.Constant(Type = A.Mode, Value = B.Mode.One);''', 'Expected enum type A.Mode, got B.Mode'),
    ('''namespace A { enum type Mode : int8 { One = 1 } }
namespace B { enum type Mode : int8 { One = 1 }
node type Bad { parameter Value : A.Mode = Mode.One;
node value : Std.Constant(Type = A.Mode, Value = Value); } }
node n : B.Bad;''', 'Expected enum type A.Mode, got Mode'),
    ('''namespace A { enum type Mode : int8 { One = 1 } }
namespace B { enum type Mode : int8 { One = 1 } }
node type LiteralParameter { parameter Type : ValueType; parameter Value : Type; }
node n : LiteralParameter(Type = List[Option[A.Mode]], Value = [Some(B.Mode.One)]);''',
     'Expected enum type A.Mode, got B.Mode'),
]
for source, expected in invalid:
    with contextlib.redirect_stdout(io.StringIO()) as output:
        assert compile_source(source) is None, source
    diagnostic = output.getvalue()
    assert expected in diagnostic, (source, diagnostic)
    assert 'Internal compiler error' not in diagnostic, diagnostic

# Duplicate diagnostics identify both member locations.
with contextlib.redirect_stdout(io.StringIO()) as output:
    assert compile_source('''enum type Duplicate : int8 {
  First = 1,
  Second = 1,
}''') is None
assert 'Error in <enum-declarations>:3:' in output.getvalue()
assert 'Hint: <enum-declarations>:2: Previous member was here' in output.getvalue()
