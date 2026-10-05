from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls


engine.step()
engine.assert_value_is('empty', True)
engine.assert_value_is('number', False)
for name, expected in [('empty', True), ('number', False)]:
    parameters = engine.program.instances[name].parameters
    assert parameters['Same'] is expected
    assert parameters['Reverse'] is expected


def identity(*, context):
    return context.arguments['value']


with patch.dict(native_function_impls, {('TypeIdentity',): identity}):
    graph = compile_strs([('<type comparisons>', '''
native function TypeIdentity(value: ValueType): ValueType;
assert TypeIdentity(int16) == int16;
assert int16 == TypeIdentity(int16);
assert TypeIdentity(int16) != TypeIdentity(unit);
constant Same: boolean = TypeIdentity(Later) == Option[List[int16]];
constant Later: ValueType = Option[List[int16]];
assert Same;
node type Compare {
  parameter Type: ValueType;
  parameter Same: boolean = TypeIdentity(Type) == unit;
  assert Same == (unit == TypeIdentity(Type));
  default output: value boolean;
  procedure run produces default output as output { output = Same; }
}
node empty: Compare { Type: unit }
node number: Compare { Type: int16 }
''')])
    assert graph is not None
    runtime = wirely.Engine(program_compiler.compile_nocatch(graph))
    runtime.step()
    runtime.assert_value_is('empty', True)
    runtime.assert_value_is('number', False)
