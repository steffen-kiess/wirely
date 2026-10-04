from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
import subprocess
import sys
from textwrap import dedent
from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import model as blocks
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.std
import wirely.native.test_library
from wirely.native import native_block_type
from wirely.compiler.native_configuration import register_configuration


checks = TestCase()


# Import execution and every native namespace without any compiler machinery.
subprocess.run([sys.executable, '-c', dedent('''
    import importlib
    import pkgutil
    import sys

    from wirely import Program, Engine
    import wirely.native

    for module in pkgutil.iter_modules(wirely.native.__path__, 'wirely.native.'):
        importlib.import_module(module.name)

    compiler_modules = {
        'wirely.compiler.ast', 'wirely.compiler.parser', 'wirely.compiler.block', 'wirely.compiler.block_type',
        'wirely.compiler.node', 'wirely.compiler.parameters', 'wirely.compiler.value',
        'wirely.compiler.program', 'wirely.compiler.native_configuration',
    }
    unexpected = {
        name for name in sys.modules
        if name in compiler_modules or name == 'lark' or name.startswith((
            'lark.', 'wirely.compiler', 'wirely.compiler.native_preparation',
        ))
    }
    assert not unexpected, unexpected
''')], check=True)


def check_runtime_data(value, seen=None):
    if seen is None:
        seen = set()
    if id(value) in seen:
        return
    seen.add(id(value))
    assert not isinstance(value, (blocks.Node, blocks.NodeInstance,
                                  blocks.BlockType, blocks.Block.Member,
                                  blocks.Block.Procedure, blocks.ast.SourceElement))
    if isinstance(value, Mapping):
        for key, item in value.items():
            check_runtime_data(key, seen)
            check_runtime_data(item, seen)
    elif isinstance(value, (tuple, list, set)):
        for item in value:
            check_runtime_data(item, seen)
    elif is_dataclass(value):
        for field in fields(value):
            check_runtime_data(getattr(value, field.name), seen)
    elif isinstance(value, (blocks.Program, blocks.Variable, blocks.VariableBlock)):
        check_runtime_data(vars(value), seen)
    else:
        assert not callable(value)


program = replace(engine.program, graph=None)
engine.program = program
check_runtime_data(program)
for schedule in program.procs.values():
    for proc in schedule:
        type_name, method_name = proc.reference
        assert isinstance(type_name, tuple)
        assert all(isinstance(part, str) for part in type_name)
        assert isinstance(method_name, str)
        assert isinstance(proc.node_id, str)

# A state change reruns its reader while an unchanged input stays skipped.
delay = wirely.native.std.DelayOneStep
with patch.object(delay, 'copy_from_input', wraps=delay.copy_from_input) as writer, \
        patch.object(delay, 'copy_to_output', wraps=delay.copy_to_output) as reader:
    isolated = blocks.Engine(program)
    isolated.get_native_state('source').set_value(True)
    isolated.step()
    isolated.assert_value_is('envelope', False)
    assert writer.call_count == reader.call_count == 1
    isolated.step()
    isolated.assert_value_is('envelope', True)
    assert writer.call_count == 1
    assert reader.call_count == 2
    isolated.assert_is_idle()
    assert writer.call_count == 1
    assert reader.call_count == 2

# Engines share the program descriptions and have independent live state.
second = blocks.Engine(program)
first_source = engine.get_native_state('source')
second_source = second.get_native_state('source')
assert first_source is not second_source
first_source.set_value(True)
second_source.set_value(False)
engine.step()
second.step()
engine.assert_value_is('envelope', False)
second.assert_value_is('envelope', False)
engine.step()
engine.assert_value_is('envelope', True)
second.assert_is_idle()
second.assert_value_is('envelope', False)
assert engine.cache is not second.cache
assert engine.state is not second.state
assert engine.triggered_procs is not second.triggered_procs
assert engine.wakeups is not second.wakeups
assert engine.get_block_path('shared') == engine.get_block_path('shared_again')
assert engine.get_block_path_singleton('Shared') == engine.get_block_path('shared')
assert engine.get_variable('shared') is engine.get_variable('shared_again')
assert engine.get_variable('envelope.') is engine.get_variable('envelope.delay.')
engine.assert_value_is('shared_again.constant', True)
with checks.assertRaisesRegex(ValueError, 'Unknown procedure'):
    engine.trigger(engine.get_block_path('source'), 'missing')


registry = {}


@dataclass(frozen=True)
class Prepared:
    label: str


def prepare_prepared(*, context):
    return Prepared(context.parameters['Label'])


register_configuration(Prepared, prepare_prepared)


@native_block_type(registry, has_native_state=True)
class Provider(blocks.NativeState):
    pass


@native_block_type(registry, has_native_state=True, data_type=Prepared)
class Consumer(blocks.NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        assert context.inputs['service'] is context.inputs['services'][0]
        assert context.inputs['services'][0] is context.inputs['services'][1]
        self.calls = 0
        self.requested_value = False
        self.schedule_wakeup = True
        self.retrigger = False
        self.trigger('refresh')

    def refresh(self, *, context):
        assert context.configuration.label == 'prepared'
        assert context.inputs['service'] is self.inputs['service']
        assert context.inputs['services'] == self.inputs['services']
        self.calls += 1
        assert isinstance(context.inputs['value'], bool)
        context.set_result('saved', self.requested_value)
        if self.schedule_wakeup:
            self.schedule_wakeup = False
            now = context.start_time_for_stage(blocks.RunStage.DEFAULT).monotonic
            context.set_wakeup_time(now + blocks.Duration(duration_ns=3600_000_000_000))
        if self.retrigger:
            self.retrigger = False
            self.trigger('refresh')

    def read(self, *, context):
        context.set_result('output', context.inputs['value'])


with patch.dict(wirely.native.test_library.native_block_types, registry):
    graph = wirely.compiler.compile_strs([('<runtime-resources>', '''
namespace TestLibrary {
  resource type Service;
  singleton native node type Provider {
    implicit output: resource Service;
  }
  native node type Consumer {
    parameter Label: String;
    input Service: resource Service;
    input Services: list resource Service;
    state saved: boolean = false;
    output Old: value boolean;
    output New: value boolean;
    connect saved.old -> Old;
    connect saved.new -> New;
    implicit output: value boolean;
    native initializer depends on Service as "service", Services as "services";
    native procedure Poll as "refresh"
      depends on Service as "service", Services as "services",
        saved.old as "value"
      produces saved;
    native procedure read
      depends on saved.new as "value"
      produces implicit output as "output";
  }
}
node consumer: TestLibrary.Consumer { Label: "prepared" }
node provider: TestLibrary.Provider {}
node provider_alias: TestLibrary.Provider {}
connect provider -> consumer.Service;
connect provider, provider_alias -> consumer.Services;
''')])
    assert graph is not None
    resource_program = replace(program_compiler.compile_nocatch(graph), graph=None)
    check_runtime_data(resource_program)
    first = blocks.Engine(resource_program)
    other = blocks.Engine(resource_program)

consumer = first.get_native_state('consumer')
other_consumer = other.get_native_state('consumer')
assert consumer.context.configuration is resource_program.instances['consumer'].configuration
assert consumer is not other_consumer
assert consumer.inputs['service'] is not other_consumer.inputs['service']
assert consumer.inputs['service'] is first.get_native_state_singleton('TestLibrary.Provider')
assert not consumer.calls

first.step()
first.assert_value_is('consumer', False)
first.assert_value_is('consumer.Old', False)
first.assert_value_is('consumer.New', False)
assert consumer.calls == 1
assert first.next_wakeup is not None
first.assert_is_idle()
first.trigger('consumer', 'read')
first.step()
assert consumer.calls == 1
first.assert_value_is('consumer.New', False)

first.advance_fake_monotonic_time(blocks.Duration(duration_ns=3601_000_000_000))
first.step()
assert consumer.calls == 2
assert first.next_wakeup is None
assert not first.wakeups
first.assert_is_idle()
assert other_consumer.calls == 0
assert other.cache is None

consumer.requested_value = True
consumer.trigger('refresh')
first.step()
first.assert_value_is('consumer.Old', False)
first.assert_value_is('consumer.New', True)
first.assert_value_is('consumer', True)
first.step()
first.assert_value_is('consumer.Old', True)
first.assert_is_idle()
consumer.retrigger = True
consumer.trigger('refresh')
first.step()
assert ('consumer', 'refresh') in first.triggered_procs
first.step()
first.assert_is_idle()
other.step()
other.assert_value_is('consumer.New', False)
with checks.assertRaisesRegex(ValueError, 'Unknown procedure name'):
    consumer.trigger('Poll')

# Slots share one compact layout while instances keep independent byte values.
flag = wirely.Variable('flag', wirely.types.boolean)
small = wirely.Variable('small', wirely.types.uint16)
unit_a = wirely.Variable('unit_a', wirely.types.unit)
unit_b = wirely.Variable('unit_b', wirely.types.unit)
nested_type = wirely.types.TypeOption(wirely.types.TypeOption(wirely.types.uint64))
nested = wirely.Variable('nested', nested_type)
clock = wirely.Variable('clock', wirely.types.MonotonicTime)
number = wirely.Variable('number', wirely.types.int64)
block = wirely.VariableBlock([flag, small, unit_a, nested, clock, number, unit_b], 'bytes')
assert list(block.variables) == ['flag', 'small', 'unit_a', 'nested', 'clock', 'number', 'unit_b']
assert list(block.offsets.items()) == [
    (nested, 0), (clock, 16), (number, 32), (small, 40),
    (flag, 42), (unit_a, 43), (unit_b, 43),
]
assert (block.size, block.alignment) == (48, 8)
values = wirely.VariableBlockInstance(block)
independent = wirely.VariableBlockInstance(block)
assert isinstance(values.data, bytearray)
assert values.data == bytes(48)
with checks.assertRaisesRegex(Exception, 'not yet set'):
    values[flag]
with checks.assertRaisesRegex(Exception, 'not yet set'):
    values[unit_a]
expected = {
    flag: True, small: 0x1234, unit_a: wirely.Unit(), unit_b: wirely.Unit(),
    nested: wirely.Some(None), clock: wirely.MonotonicTime(7, 123), number: -2,
}
for variable, value in expected.items():
    values.add(variable, value)
    assert values[variable] == value
assert values.data == (
    bytes(9) + b'\x01' + bytes(6)
    + bytes.fromhex('07 00 00 00 00 00 00 00 7b 00 00 00 00 00 00 00')
    + bytes.fromhex('fe ff ff ff ff ff ff ff 34 12 01') + bytes(5)
)
assert independent.data == bytes(48)
with checks.assertRaisesRegex(Exception, 'not yet set'):
    independent[flag]
with checks.assertRaisesRegex(Exception, 'already set'):
    values.add(unit_a, wirely.Unit())
with checks.assertRaisesRegex(Exception, 'already set'):
    values.add(flag, False)
foreign = wirely.Variable('flag', wirely.types.boolean)
with checks.assertRaisesRegex(Exception, 'not in VariableBlock'):
    values.add(foreign, True)
with checks.assertRaisesRegex(Exception, 'not in VariableBlock'):
    values[foreign]

# Failed writes preserve the buffer and leave the variable available for retry.
with checks.assertRaises(ValueError):
    independent.add(small, 65536)
assert independent.data == bytes(48)
with checks.assertRaisesRegex(Exception, 'not yet set'):
    independent[small]
independent.add(small, 65535)
assert independent[small] == 65535

# A slot write clears its padding; reads validate slot and final block padding.
independent.data[10] = 1
independent.add(nested, None)
assert independent[nested] is None
for offset, variable in [(10, nested), (47, flag)]:
    values.data[offset] = 1
    with checks.assertRaisesRegex(ValueError, 'Non-zero padding'):
        values[variable]
    values.data[offset] = 0
values.data[42] = 2
with checks.assertRaises(ValueError):
    values[flag]
values.data[42] = 1

empty_block = wirely.VariableBlock([], 'empty')
assert (empty_block.size, empty_block.alignment) == (0, 1)
assert wirely.VariableBlockInstance(empty_block).data == b''
units = wirely.VariableBlock([unit_a, unit_b], 'units')
assert units.size == 0
unit_values = wirely.VariableBlockInstance(units)
unit_values.add(unit_a, wirely.Unit())
with checks.assertRaisesRegex(Exception, 'not yet set'):
    unit_values[unit_b]
unit_values.add(unit_b, wirely.Unit())
assert unit_values[unit_a] == unit_values[unit_b] == wirely.Unit()
assert unit_values.data == b''
