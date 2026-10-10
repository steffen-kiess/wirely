from collections import Counter
from pathlib import Path
from unittest.mock import patch

from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_node_type
from wirely.runtime.engine import Engine


registry = {}
calls = Counter()
list_inputs = []
observations = {}


@native_node_type(registry, has_native_state=False)
class Owner:
    @staticmethod
    def write(*, context):
        context.set_output('saved', context.inputs['input'])


@native_node_type(registry, has_native_state=False)
class Forward:
    @staticmethod
    def run(*, context):
        calls[context.name] += 1
        context.set_output('output', context.inputs['input'])


@native_node_type(registry, has_native_state=False)
class Feedback:
    @staticmethod
    def advance(*, context):
        context.set_output('saved', min(context.inputs['input'] + 1, 2))


@native_node_type(registry, has_native_state=False)
class Shared:
    @staticmethod
    def advance(*, context):
        context.set_output('saved', min(context.inputs['old'] + 1, 2))


@native_node_type(registry, has_native_state=False)
class Collect:
    @staticmethod
    def run(*, context):
        calls[context.name] += 1
        values = context.inputs['input']
        list_inputs.append(values)
        observations[context.name] = values
        context.set_output('output', True)


definitions = '''
namespace TestLibrary {
  node type Forward {
    parameter Type: ValueType;
    default input: value Type;
    default output: value Type;
    native procedure run
      depends on default input as "input"
      produces default output as "output";
  }

  node type Relay {
    parameter Type: ValueType;
    default input: value Type;
    default output: value Type;
    connect default input -> default output;
  }

  node type Owner {
    parameter Type: ValueType;
    parameter InitialValue: Type;
    default input: value Type;
    default output: value Type;
    state saved: Type = InitialValue;
    connect saved.old -> default output;

    node relay: Relay { Type: Type }
    node reader: Forward { Type: Type }
    connect saved.old -> relay;
    connect relay -> reader;
    native procedure write stage output
      depends on default input as "input"
      produces saved;
  }

  node type Feedback {
    default output: value uint8;
    state saved: uint8 = 0;
    node reader: Forward { Type: uint8 }
    connect saved.old -> reader;
    connect reader -> default output;
    native procedure advance
      depends on reader as "input"
      produces saved;
  }

  singleton node type Shared {
    default output: value uint8;
    state saved: uint8 = 0;
    node relay: Relay { Type: uint8 }
    node reader: Forward { Type: uint8 }
    connect saved.old -> relay;
    connect relay -> reader;
    connect saved.old -> default output;
    native procedure advance
      depends on saved.old as "old"
      produces saved;
  }

  node type Collect {
    default input: multi value boolean;
    default output: value boolean;
    native procedure run
      depends on default input as "input"
      produces default output as "output";
  }
}
'''
std_source = Path('wirely/lib/std.wrl').read_text()


def compile_graph(source):
    with patch.dict(wirely.native.test_library.native_node_types, registry):
        return wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', std_source),
            ('<state-connections>', source),
        ])


def make_engine(source, *, declarations=definitions):
    graph = compile_graph(declarations + source)
    assert graph is not None
    program = program_compiler.compile(graph)
    assert program is not None
    with patch.dict(wirely.native.test_library.native_node_types, registry):
        return Engine(program)


def check_old_snapshots():
    probe = make_engine('''
    node source: Std.DebugValueSource { Type: boolean }
    node other: Std.Constant { Type: boolean, Value: false }
    node owner: TestLibrary.Owner { Type: boolean, InitialValue: false }
    node second: TestLibrary.Owner { Type: boolean, InitialValue: true }
    node collector: TestLibrary.Collect {}
    connect source -> owner;
    connect other -> second;
    connect owner, source, second -> collector;
    ''')
    probe.get_native_state('source').set_value(True)
    probe.step()
    probe.assert_value_is('owner', False)
    probe.assert_value_is('owner.reader', False)
    probe.assert_value_is('second', True)
    assert list_inputs[-1] == (False, True, True)

    probe.step()
    probe.assert_value_is('owner.reader', True)
    probe.assert_value_is('second.reader', False)
    assert list_inputs[-1] == (True, True, False)
    assert calls['owner.reader'] == 2
    assert calls['second.reader'] == 2
    assert calls['collector'] == 2
    probe.step()
    probe.assert_is_idle()
    assert calls['owner.reader'] == 2
    assert calls['collector'] == 2

    probe.get_native_state('source').set_value(False)
    probe.step()
    assert list_inputs[-1] == (True, False, False)
    probe.step()
    assert list_inputs[-1] == (False, False, False)
    probe.step()
    probe.assert_is_idle()

    feedback = make_engine('node feedback: TestLibrary.Feedback {}')
    for value in (0, 1, 2):
        feedback.step()
        feedback.assert_value_is('feedback', value)
    feedback.step()
    feedback.assert_is_idle()

    shared = make_engine('''
    node first: TestLibrary.Shared {}
    node second: TestLibrary.Shared {}
    node observer: TestLibrary.Forward { Type: uint8 }
    connect second -> observer;
    ''')
    for value in (0, 1, 2):
        shared.step()
        shared.assert_value_is('first', value)
        shared.assert_value_is('second', value)
        shared.assert_value_is('observer', value)
    shared.step()
    shared.assert_is_idle()

    # A later-stage reader still sees the snapshot from before the state writer.
    later = make_engine('''
    node source: Std.Constant { Type: boolean, Value: true }
    node owner: TestLibrary.Owner { Type: boolean, InitialValue: false }
    connect source -> owner;
    ''', declarations=definitions.replace(
        'native procedure write stage output', 'native procedure write',
    ).replace('native procedure run\n', 'native procedure run stage output\n'))
    later.step()
    later.assert_value_is('owner.reader', False)
    later.step()
    later.assert_value_is('owner.reader', True)

    nested = make_engine('''
    node type Envelope {
      default input: value uint8;
      default output: value uint8;
      node owner: TestLibrary.Owner { Type: uint8, InitialValue: 7 }
      connect default input -> owner;
      connect owner -> default output;
    }
    node source: Std.Constant { Type: uint8, Value: 8 }
    node other: Std.Constant { Type: uint8, Value: 9 }
    node first: Envelope {}
    node second: Envelope {}
    connect source -> first;
    connect other -> second;
    ''')
    nested.step()
    nested.assert_value_is('first', 7)
    nested.assert_value_is('second', 7)
    nested.step()
    nested.assert_value_is('first', 8)
    nested.assert_value_is('second', 9)
    nested.assert_value_is('first.owner.reader', 8)
    nested.assert_value_is('second.owner.reader', 9)


@native_node_type(registry, has_native_state=False)
class Current(Owner):
    @staticmethod
    def write(*, context):
        calls[context.name + '.write'] += 1
        Owner.write(context=context)

    @staticmethod
    def observe(*, context):
        observations[context.name] = (context.inputs['previous'], context.inputs['current'])
        context.set_output('output', True)


@native_node_type(registry, has_native_state=False)
class Mixed(Collect):
    pass


current_definitions = definitions + '''
namespace TestLibrary {
  node type Mixed {
    parameter Type: ValueType;
    default input: multi value Type;
    default output: value boolean;
    native procedure run
      depends on default input as "input"
      produces default output as "output";
  }

  node type Current {
    parameter Type: ValueType;
    parameter InitialValue: Type;
    default input: value Type;
    default output: value boolean;
    output Old: value Type;
    output New: value Type;
    state saved: Type = InitialValue;
    connect saved.old -> Old;
    connect saved.new -> New;

    node old_reader: Forward { Type: Type }
    node relay: Relay { Type: Type }
    node new_reader: Forward { Type: Type }
    node mixed: Mixed { Type: Type }
    connect saved.old -> old_reader;
    connect saved.new -> relay;
    connect relay -> new_reader;
    connect saved.old, default input, saved.new -> mixed;

    // Declare the reader first to exercise dependency ordering.
    native procedure observe
      depends on saved.old as "previous", saved.new as "current"
      produces default output as "output";
    native procedure write
      depends on default input as "input"
      produces saved;
  }
}
'''


def check_current_snapshots():
    current_source = '''
    node source: Std.DebugValueSource { Type: uint8 }
    node owner: TestLibrary.Current { Type: uint8, InitialValue: 0 }
    node observer: TestLibrary.Forward { Type: uint8 }
    connect source -> owner;
    connect owner.New -> observer;
    '''
    current = make_engine(current_source, declarations=current_definitions)
    current.get_native_state('source').set_value(5)
    current.step()
    assert observations['owner'] == (0, 5)
    current.assert_value_is('owner.Old', 0)
    current.assert_value_is('owner.New', 5)
    current.assert_value_is('owner.old_reader', 0)
    current.assert_value_is('owner.new_reader', 5)
    assert observations['owner.mixed'] == (0, 5, 5)
    current.assert_value_is('observer', 5)
    new_reader_calls = calls['owner.new_reader']
    observer_calls = calls['observer']

    current.step()
    assert observations['owner'] == (5, 5)
    assert observations['owner.mixed'] == (5, 5, 5)
    assert calls['owner.new_reader'] == new_reader_calls
    assert calls['observer'] == observer_calls
    current.step()
    current.assert_is_idle()
    write_calls = calls['owner.write']

    # A skipped writer still supplies .new when its reader is triggered.
    observer_path = current.get_node_path('observer')
    current.trigger(observer_path, 'run')
    current.step()
    assert calls['owner.write'] == write_calls
    assert calls['observer'] == observer_calls + 1
    current.assert_value_is('observer', 5)
    current.assert_value_is('owner.New', 5)

    current.get_native_state('source').set_value(9)
    current.step()
    assert observations['owner'] == (5, 9)
    assert observations['owner.mixed'] == (5, 9, 9)
    current.assert_value_is('owner.new_reader', 9)
    current.assert_value_is('observer', 9)
    assert calls['owner.new_reader'] == new_reader_calls + 1
    assert calls['observer'] == observer_calls + 2

    # Both same-stage and later-stage readers observe this step's produced value.
    later_current = make_engine(current_source, declarations=current_definitions.replace(
        'native procedure observe\n', 'native procedure observe stage output\n',
    ))
    later_current.get_native_state('source').set_value(11)
    later_current.step()
    assert observations['owner'] == (0, 11)

    new_shared_definitions = definitions.replace(
        'connect saved.old -> relay;', 'connect saved.new -> relay;',
    ).replace('connect saved.old -> default output;', 'connect saved.new -> default output;')
    new_shared = make_engine('''
    node first: TestLibrary.Shared {}
    node second: TestLibrary.Shared {}
    node observer: TestLibrary.Forward { Type: uint8 }
    connect second -> observer;
    ''', declarations=new_shared_definitions)
    for value in (1, 2, 2):
        new_shared.step()
        new_shared.assert_value_is('first', value)
        new_shared.assert_value_is('second', value)
        new_shared.assert_value_is('first.reader', value)
        new_shared.assert_value_is('observer', value)
    new_shared.assert_is_idle()

    new_nested = make_engine('''
    node type Envelope {
      default input: value uint8;
      default output: value uint8;
      node owner: TestLibrary.Current { Type: uint8, InitialValue: 0 }
      connect default input -> owner;
      connect owner.New -> default output;
    }
    node source: Std.Constant { Type: uint8, Value: 8 }
    node other: Std.Constant { Type: uint8, Value: 9 }
    node first: Envelope {}
    node second: Envelope {}
    connect source -> first;
    connect other -> second;
    ''', declarations=current_definitions)
    new_nested.step()
    new_nested.assert_value_is('first', 8)
    new_nested.assert_value_is('second', 9)
    assert observations['first.owner'] == (0, 8)
    assert observations['second.owner'] == (0, 9)


copied_definitions = '''
node type Copies {
  default input: value uint8;
  output Fanned: value uint8;
  output Delayed: value uint8;
  output Current: value uint8;
  output Twice: value uint8;
  output Mirrored: value uint8;
  output Published: value uint8;

  state first: uint8 = 0;
  state second: uint8 = 9;
  state latched: uint8 = 0;
  state twice: uint8 = 0;
  state mirror: uint8 = 5;
  state sent: uint8 = 0;
  state published: uint8 = 0;

  procedure forward
    depends on default input as input
    produces result: value uint8
  {
    result = input;
  }
  connect forward.result -> first;
  connect forward.result -> second;
  connect default input -> latched;
  connect latched.old -> twice;
  connect first.new -> mirror;

  procedure publish stage output
    depends on default input as input
    produces value: value uint8
  {
    value = input;
  }
  connect publish.value -> sent;
  connect publish.value -> published;
  procedure observe stage output
    depends on published.new as value
    produces Published as output
  {
    output = value;
  }

  connect second.old -> Fanned;
  connect latched.old -> Delayed;
  connect latched.new -> Current;
  connect twice.old -> Twice;
  connect mirror.old -> Mirrored;
}
'''


def check_copied_states():
    copied = make_engine('''
    node source: Std.DebugValueSource { Type: uint8 }
    node copies: Copies {}
    connect source -> copies;
    ''', declarations=copied_definitions)

    def check(fanned, delayed, current, twice, mirrored, published):
        for name, value in [('Fanned', fanned), ('Delayed', delayed), ('Current', current),
                            ('Twice', twice), ('Mirrored', mirrored), ('Published', published)]:
            copied.assert_value_is('copies.' + name, value)

    copied.get_native_state('source').set_value(3)
    copied.step()
    check(9, 0, 3, 0, 5, 3)
    copied.step()
    check(3, 3, 3, 0, 3, 3)
    copied.step()
    check(3, 3, 3, 3, 3, 3)
    copied.assert_is_idle()

    copied.get_native_state('source').set_value(7)
    copied.step()
    check(3, 3, 7, 3, 3, 7)
    copied.step()
    check(7, 7, 7, 3, 7, 7)
    copied.step()
    check(7, 7, 7, 7, 7, 7)
    copied.assert_is_idle()


check_old_snapshots()
check_current_snapshots()
check_copied_states()
