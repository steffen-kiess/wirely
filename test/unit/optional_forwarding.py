from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs, model, program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.compiler.parameters import ParameterValues
from wirely.compiler.value import OptionalBinding


instances = engine.program.instances
engine.step()
engine.assert_value_is('omitted.child.number', 9)
engine.assert_value_is('supplied.child.number', -5)
engine.assert_value_is('outer_omitted.child.child.number', 9)
engine.assert_value_is('outer_supplied.child.child.number', 4)
engine.assert_value_is('default_type.child.number', 9)
engine.assert_value_is('supplied_type.child.number', 9)
for name in ('omitted', 'supplied', 'nested_some'):
    engine.assert_value_is(f'{name}.forced.number', 12)
    assert instances[f'{name}.forced'].parameters['Flag'] is False
assert instances['omitted.child'].parameters['Text'] == 'default'
assert instances['supplied.child'].parameters['Text'] == 'hello'
assert instances['supplied.child'].parameters['Flag'] is True
assert 'Nested' not in instances['omitted.child'].parameters
assert instances['supplied.child'].parameters['Nested'] is None
assert instances['nested_some.child'].parameters['Nested'] == wirely.Some(False)
assert instances['supplied.child'].parameters['Items'] == ('one', 'two')
assert instances['supplied.child'].parameters['PairValue']['Value'] is True
assert instances['default_expression'].parameters['Fallback'] is None
assert instances['supplied_expression'].parameters['Fallback'] == wirely.Some(True)
assert instances['default_type.child'].parameters['Type'] == wirely.value_types.int16
assert instances['supplied_type.child'].parameters['Type'] == wirely.value_types.uint8

assert dict(instances['capture_omitted.snapshot'].parameters) == {
    'Number': None, 'Type': None, 'Nested': None, 'Items': None, 'PairValue': None,
}
captured = instances['capture_supplied.snapshot'].parameters
assert captured['Number'] == wirely.Some(7)
assert captured['Type'] == wirely.Some(wirely.ValueType(wirely.types.TypeList(wirely.types.boolean)))
assert captured['Nested'] == wirely.Some(None)
assert captured['Items'] == wirely.Some(('value',))
assert captured['PairValue'].value['Value'] is False
engine.assert_value_is('runtime_omitted', None)
engine.assert_value_is('runtime_supplied', wirely.Some(True))

# Presence checks and repeated reads share one optional-binding evaluation.


calls = []


def count(*, context):
    value = context.arguments['value']
    calls.append(value)
    return value


def is_some(*, context):
    return context.arguments['value'] is not None


source = '''namespace Example {
  native function Count(value: Option[boolean]): Option[boolean];
  native function IsSome(value: Option[boolean]): boolean;
}
node type Child {
  optional parameter Flag: boolean;
  constant First: Option[boolean] = parameter_option(Flag);
  constant Second: Option[boolean] = parameter_option(Flag);
  parameter Expected: boolean;
  assert Example.IsSome(parameter_option(Flag)) == Expected;
  node first: Std.Constant { Type: Option[boolean], Value: First }
  node second: Std.Constant { Type: Option[boolean], Value: Second }
}
node type Outer {
  optional parameter Flag: boolean;
  parameter Expected: boolean;
  node child: Child {
    Flag?: Example.Count(parameter_option(Flag)),
    Expected: Expected,
  }
}
node omitted: Outer { Expected: false }
node supplied: Outer { Flag: true, Expected: true }
'''
with patch.dict(native_function_impls, {
    ('Example', 'Count'): count, ('Example', 'IsSome'): is_some,
}):
    graph = compile_strs([('<memoized-forwarding>', source)], add_std=True)
    assert graph is not None
    assert calls == []
    compiled = program_compiler.compile_nocatch(graph)
    assert calls == [None, wirely.Some(True)]
    runtime = wirely.Engine(compiled)
    runtime.step()
    runtime.assert_value_is('supplied.child.first', wirely.Some(True))
    runtime.assert_value_is('supplied.child.second', wirely.Some(True))
    runtime.assert_value_is('omitted.child.first', None)
    assert calls == [None, wirely.Some(True)]

# A presence query through a binding must still detect dependency cycles.
scope = graph.block_type_implementations['Child']
parameter = scope.parsed_parameters['Flag']
expression = scope.parsed_constants['First'].default_value
values = ParameterValues({parameter: OptionalBinding(scope, expression, parameter)})
with TestCase().assertRaisesRegex(model.ProgramError, 'Cyclic parameter resolution: Flag -> Flag'):
    parameter in values

assert instances['dependent_before_type'].parameters['Value'] == -3
assert instances['dependent_before_type'].parameters['Type'] == wirely.value_types.int8
assert instances['dependent_default_type'].parameters['Value'] is True

assert instances['generic_omitted.snapshot'].parameters['Value'] is None
assert instances['generic_supplied.snapshot'].parameters['Value'] == wirely.Some((True, False))
assert instances['raw'].parameters['type'] is True
