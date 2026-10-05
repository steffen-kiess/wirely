import contextlib
import datetime
import io
import sys
from dataclasses import FrozenInstanceError
from types import ModuleType
from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs, model as blocks
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.compiler.attributes import ATTRIBUTE_REGISTRY, AttributeDefinition

engine.step()
assert engine.get_value('Wrapped') == datetime.time(0, 30)


def graph(source):
    result = compile_strs([('<functions>', source)])
    assert result is not None
    return result


def program(source):
    return program_compiler.compile_nocatch(graph(source))


calls = []


def identity(*, context):
    calls.append(context.function_name)
    assert tuple(context.arguments) == tuple(context.arguments_ast)
    for mapping in (context.arguments, context.arguments_ast):
        try:
            mapping['value'] = None
        except TypeError:
            pass
        else:
            raise AssertionError('Mutable context mapping')
    try:
        context.call = None
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError('Mutable context')
    assert context.error('call').source_element is context.call
    assert context.error('argument', argument='value').source_element is context.call.parameters[0]
    try:
        context.error('unknown', argument='unknown')
    except ValueError:
        pass
    else:
        raise AssertionError('Unknown argument accepted')
    return context.arguments['value']


def choose_type(*, context):
    return wirely.value_types.uint16


with patch.dict(native_function_impls, {
    ('A', 'Echo'): identity, ('Type',): choose_type,
    ('ListEcho',): identity, ('UnitEcho',): identity, ('TypeEcho',): identity,
    ('StructEcho',): identity, ('EnumEcho',): identity,
}):
    source = '''state Result: uint16 = A.Echo(513);
namespace A {
  node type Probe {
    parameter Value: uint16 = Echo(8);
    state Copy: uint16 = Echo(Value);
  }
}
namespace A {
  native function Echo(value: uint16,): uint16;
}
node probe: A.Probe {}
native function Type(): ValueType;
constant Kind: ValueType = Type();
state Typed: Kind = 7;
node type Generic {
  parameter Kind: ValueType = Type();
  parameter Initial: Kind = 9;
  state Value: Kind = Initial;
}
node default_type: Generic {}
node explicit_type: Generic { Kind: Type(), Initial: 11 }
native function ListEcho(value: List[Option[uint8]]): List[Option[uint8]];
constant Items: List[Option[uint8]] = ListEcho([Some(3), None]);
native function UnitEcho(value: unit): unit;
state Empty: unit = UnitEcho(());
native function TypeEcho(value: ValueType): ValueType;
constant TypeCopy: ValueType = TypeEcho(uint8);
struct type Pair { value: uint8, }
native function StructEcho(value: Pair): Pair;
state PairCopy: Pair = StructEcho(Pair { value: 4 });
enum type Mode: uint8 { On = 1, }
native function EnumEcho(value: Mode): Mode;
state ModeCopy: Mode = EnumEcho(Mode.On);
'''
    compiled = graph(source)
    assert calls == [], calls
    result = wirely.Engine(program_compiler.compile_nocatch(compiled))
    result.step()
    assert result.get_value('Result') == 513
    assert result.get_value('probe.Copy') == 8
    assert result.get_value('Typed') == 7
    assert result.get_value('default_type.Value') == 9
    assert result.get_value('explicit_type.Value') == 11
    assert ('ListEcho',) in calls
    assert result.get_value('Empty') == wirely.Unit()
    assert ('TypeEcho',) in calls
    assert result.get_value('PairCopy')['value'] == 4


module_name = 'wirely_native_function_test'
module = ModuleType(module_name)
module.run = lambda *, context: True
source = 'native function Deferred(): boolean;'
with patch.dict(native_function_impls, {('Deferred',): (module_name, 'run')}):
    with patch('wirely.compiler.native_functions.importlib.import_module', return_value=module) as importer:
        compiled = graph(source + '''
constant Skip: boolean = true || Deferred();
constant SkipAnd: boolean = false && Deferred();
constant SkipIf: boolean = if true { true } else { Deferred() };
node type Unused { constant Value: boolean = Deferred(); }
''')
        program_compiler.compile_nocatch(compiled)
        importer.assert_not_called()
        compiled = graph(source + 'constant First: boolean = Deferred(); assert Deferred();')
        importer.assert_not_called()
        program_compiler.compile_nocatch(compiled)
        assert importer.call_count == 1
        module.run = lambda *, context: False
        second = wirely.Engine(program(source + 'state Value: boolean = Deferred();'))
        second.step()
        assert second.get_value('Value') is False
        assert importer.call_count == 2
    # An unselected call does not need the implementation's dependencies.
    program(source + 'assert true || Deferred();')
    try:
        program(source + 'assert Deferred();')
    except blocks.ProgramError as error:
        assert 'Cannot load function' in str(error)
        assert module_name in str(error)
    else:
        raise AssertionError('Missing dependency accepted')


async def asynchronous(*, context):
    return True


def generator(*, context):
    yield True


def expected_error(*, context):
    raise context.error('expected', argument='value')


def os_error(*, context):
    raise OSError('unavailable file')


for implementation, message in [
    (lambda wrong: True, 'Cannot load function'),
    (asynchronous, 'synchronous'), (generator, 'synchronous'),
    (lambda *, context: 123, 'expected result of type boolean'),
    (expected_error, 'expected'), (os_error, 'unavailable file'),
]:
    with patch.dict(native_function_impls, {('Fail',): implementation}):
        try:
            program('native function Fail(value: boolean): boolean; assert Fail(true);')
        except blocks.ProgramError as error:
            assert message in str(error), str(error)
            assert not isinstance(error, blocks.InternalCompilerError)
            if implementation is expected_error:
                assert isinstance(error.source_element, blocks.Reference)
                assert str(error.source_element) == 'true'
        else:
            raise AssertionError('Expected compilation error')


for entry, message in [((module_name, 'absent'), 'Cannot load function'),
                       ((module_name, 'run'), 'not callable')]:
    module.run = 1
    with patch.dict(native_function_impls, {('Fail',): entry}), patch.dict(sys.modules, {module_name: module}):
        try:
            program('native function Fail(): boolean; assert Fail();')
        except blocks.ProgramError as error:
            assert message in str(error)
        else:
            raise AssertionError('Invalid module attribute accepted')


with patch.dict(native_function_impls, {('Undeclared',): lambda *, context: True,
                                      ('Malformed',): ('relative.module', 'nested.attribute')}):
    for source, message in [
        ('assert Undeclared();', 'Cannot find function'),
        ('native function Malformed(): boolean;', 'Invalid implementation entry'),
    ]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert compile_strs([('<functions>', source)]) is None
        assert message in output.getvalue()
# Declarations from the earlier compilation must not leak.
output = io.StringIO()
with contextlib.redirect_stdout(output):
    assert compile_strs([('<functions>', 'assert Deferred();')]) is None
assert 'Cannot find function' in output.getvalue()


with patch.dict(native_function_impls, {('Dependent',): choose_type}):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert compile_strs([('<functions>',
                             'parameter Kind: ValueType; native function Dependent(): Kind;')]) is None
    assert 'fixed value types' in output.getvalue()


# Nested native conversions and left-to-right argument evaluation.
order = []


def tick(*, context):
    order.append(context.arguments['value'])
    return context.arguments['value']


def inspect_values(*, context):
    assert context.arguments['first'] == 1
    assert context.arguments['second'] == 2
    assert context.arguments['types'] == (wirely.Some(wirely.value_types.uint8), None)
    assert context.arguments['pair']['kind'] == wirely.value_types.uint16
    return wirely.Unit()


with patch.dict(native_function_impls, {('Tick',): tick, ('Inspect',): inspect_values}):
    compiled = graph('''struct type TypedPair { kind: ValueType, }
native function Tick(value: uint8): uint8;
native function Inspect(first: uint8, second: uint8,
  types: List[Option[ValueType]], pair: TypedPair): unit;
constant Result: unit = Inspect(Tick(1), Tick(2),
  [Some(uint8), None], TypedPair { kind: uint16 });
''')
    assert order == []
    program_compiler.compile_nocatch(compiled)
    assert order == [1, 2]


# Function metadata participates in ordinary attribute validation.
with patch.dict(ATTRIBUTE_REGISTRY, {
    'integration': AttributeDefinition(frozenset({'function'}), lambda attribute: True),
}), patch.dict(native_function_impls, {('Tagged',): choose_type}):
    compiled = graph('#[integration] native function Tagged(): ValueType;')
    assert compiled.native_functions['Tagged'].attributes[0].value is True


with patch.dict(native_function_impls, {('Qualified', 'Some'): identity}):
    program("""namespace Qualified {
  native function Some(value: unit): unit;
}
constant Value: unit = Qualified.Some(());
""")


# Functions can construct declared structs from native field mappings.
with patch.dict(native_function_impls, {
    ('MakePair',): lambda *, context: {'kind': wirely.value_types.uint16, 'number': 7},
}):
    source = '''struct type Pair { kind: ValueType, number: uint8, }
native function MakePair(): Pair;
namespace TestLibrary {
  native node type FunctionPairProbe {
    parameter PairValue: Pair = MakePair();
  }
}
node probe: TestLibrary.FunctionPairProbe {}
'''
    # Inspect parameters without needing a runtime implementation.
    from wirely.native import native_block_type
    import wirely.native.test_library

    registry = {}

    @native_block_type(registry, has_native_state=True)
    class FunctionPairProbe(wirely.NativeState):
        pass

    with patch.dict(wirely.native.test_library.native_block_types, registry):
        result = program(source)
    assert result.instances['probe'].parameters['PairValue']['kind'] == wirely.value_types.uint16
    assert result.instances['probe'].parameters['PairValue']['number'] == 7
    for fields in ({'kind': wirely.value_types.uint16},
                   {'kind': wirely.value_types.uint16, 'number': 'invalid'},
                   {'kind': wirely.value_types.uint16, 'number': 7, 'extra': 1}):
        with patch.dict(native_function_impls, {('MakePair',): lambda *, context: fields}), \
                patch.dict(wirely.native.test_library.native_block_types, registry):
            try:
                program(source)
            except blocks.ProgramError as error:
                assert 'expected result of type Pair' in str(error)
            else:
                raise AssertionError('Invalid struct mapping accepted')
