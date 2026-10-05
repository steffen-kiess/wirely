from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs, program as program_compiler
from wirely.compiler.expressions import TypedExpression
from wirely.compiler.native_functions import native_function_impls
from wirely.runtime.procedures import Expression


source = engine.get_native_state('source')
nested_source = engine.get_native_state('nested_source')
for step, value in enumerate((None, wirely.Some(False), wirely.Some(True), None)):
    source.set_value(value)
    nested_source.set_value(None if value is None else wirely.Some(None))
    engine.step()
    for name in ('present', 'absent', 'nested'):
        present = value is not None
        for output in ('Present', 'BlockPresent', 'IfPresent'):
            engine.assert_value_is(f'{name}.{output}', present)
        for output in ('Absent', 'BlockAbsent', 'IfAbsent'):
            engine.assert_value_is(f'{name}.{output}', not present)
        copied = wirely.Some(None) if name == 'nested' and present else value
        engine.assert_value_is(f'{name}.Copy', copied)
        engine.assert_value_is(f'{name}.LocalAbsent', True)
        parameter_present = name != 'absent'
        for output in ('NestedPresent', 'ParameterPresent', 'ConstantPresent', 'LocalPresent'):
            engine.assert_value_is(f'{name}.{output}', parameter_present)
        for output in ('NestedAbsent', 'ParameterAbsent', 'ConstantAbsent'):
            engine.assert_value_is(f'{name}.{output}', not parameter_present)
        engine.assert_value_is(f'{name}.OptionalPresent', name == 'present')
        engine.assert_value_is(f'{name}.OptionalAbsent', name != 'present')
    engine.assert_value_is('generic', True)
    engine.assert_value_is('qualified', step == 0)
engine.assert_is_idle()

# Property receivers retain inferred static dependencies.
for instance in ('present', 'absent', 'nested'):
    block = engine.program.graph.blocks[instance]
    usage = next(procedure for procedure in block.block_procedures
                 if procedure.name == 'run').static_usage
    assert {declaration.name.id_str for declaration in usage} == {
        'Value', 'Nested', 'IsSome', 'IsNone', 'CtOptionalPresent',
        'CtOptionalAbsent', 'Type',
    }

# Declared functions may share property names and return typed options.
calls = []


def identity(*, context):
    value = context.arguments['value']
    calls.append(value)
    return value


with patch.dict(native_function_impls, {('Fns', 'IsSome'): identity, ('Fns', 'IsNone'): identity}):
    graph = compile_strs([('<presence-functions>', '''namespace Fns {
  native function IsSome(value: Option[Option[boolean]]): Option[Option[boolean]];
  native function IsNone(value: Option[Option[boolean]]): Option[Option[boolean]];
}
constant Present: boolean = Fns.IsSome(Some(None)).IsSome;
constant Absent: boolean = Fns.IsNone(None).IsNone;
constant Condition: boolean = if Fns.IsSome(Some(Some(false))).IsSome {
  true
} else {
  false
};
assert Present && Absent && Condition;
''')])
    assert graph is not None
    program = program_compiler.compile_nocatch(graph)
    assert calls == [wirely.Some(None), None, wirely.Some(wirely.Some(False))]

# Both evaluators read the receiver once and check only outer presence.


class Receiver:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def evaluate(self, inputs, locals):
        self.calls += 1
        return self.value


for value in (None, wirely.Some(False), wirely.Some(None)):
    for operation, expected in [('is_some', value is not None), ('is_none', value is None)]:
        for expression_type in (Expression, TypedExpression):
            receiver = Receiver(value)
            if expression_type is TypedExpression:
                expression = expression_type(wirely.types.boolean, operation, (receiver,))
            else:
                expression = expression_type(operation, (receiver,))
            assert expression.evaluate({}, {}) is expected
            assert receiver.calls == 1
