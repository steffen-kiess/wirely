import contextlib
import io
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import wirely as blocks
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.cli.graphviz
import wirely.native.test_library
from wirely.native import native_block_type


engine.step()
engine.assert_value_is('empty', True)

registry = {}
calls = Counter()
list_inputs = []
observations = {}


@native_block_type(registry, has_native_state=False)
class Owner:
    @staticmethod
    def write(*, context):
        context.set_result('saved', context.inputs['input'])


@native_block_type(registry, has_native_state=False)
class Forward:
    @staticmethod
    def run(*, context):
        calls[context.name] += 1
        context.set_result('output', context.inputs['input'])


@native_block_type(registry, has_native_state=False)
class Feedback:
    @staticmethod
    def advance(*, context):
        context.set_result('saved', min(context.inputs['input'] + 1, 2))


@native_block_type(registry, has_native_state=False)
class Shared:
    @staticmethod
    def advance(*, context):
        context.set_result('saved', min(context.inputs['old'] + 1, 2))


@native_block_type(registry, has_native_state=False)
class Collect:
    @staticmethod
    def run(*, context):
        calls[context.name] += 1
        values = context.inputs['input']
        list_inputs.append(values)
        observations[context.name] = values
        context.set_result('output', True)


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
    default input: list value boolean;
    default output: value boolean;
    native procedure run
      depends on default input as "input"
      produces default output as "output";
  }
}
'''
std_source = Path('wirely/lib/std.wrl').read_text()


def compile_graph(source):
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        return wirely.compiler.compile_strs([
            ('wirely/lib/std.wrl', std_source),
            ('<state-connections>', source),
        ])


def make_engine(source, *, declarations=definitions):
    graph = compile_graph(declarations + source)
    assert graph is not None
    program = program_compiler.compile(graph)
    assert program is not None
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        return blocks.Engine(program)


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
assert observations['collector'] == (False, True, True)
owner_variable = probe.get_variable('owner')
assert probe.state[owner_variable] is True
assert probe.get_value('owner.relay') is False
assert probe.get_value('owner.reader.') is False
assert owner_variable not in probe.program.temporaries.variable_set
assert probe.get_variable('owner.relay') is owner_variable
assert probe.get_variable('owner.reader.') is owner_variable
assert '() = False' in wirely.cli.graphviz.get_graph(probe.program.graph, probe).source

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
assert len(feedback.program.temporaries.variable_set) == 2
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
assert len(shared.program.state_variables.variable_set) == 1
for value in (0, 1, 2):
    shared.step()
    shared.assert_value_is('first', value)
    shared.assert_value_is('second', value)
    shared.assert_value_is('observer', value)
shared.step()
shared.assert_is_idle()

minimal = make_engine('''
node source: Std.Constant { Type: boolean, Value: true }
node owner: TestLibrary.Owner { Type: boolean, InitialValue: false }
connect source -> owner;
''')
assert all('saved' not in var.name or var.name.endswith('.new')
           for var in minimal.program.temporaries.variable_set)

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


@native_block_type(registry, has_native_state=False)
class Current(Owner):
    @staticmethod
    def write(*, context):
        calls[context.name + '.write'] += 1
        Owner.write(context=context)

    @staticmethod
    def observe(*, context):
        observations[context.name] = (context.inputs['previous'], context.inputs['current'])
        context.set_result('output', True)


@native_block_type(registry, has_native_state=False)
class Mixed(Collect):
    pass


current_definitions = definitions + '''
namespace TestLibrary {
  node type Mixed {
    parameter Type: ValueType;
    default input: list value Type;
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
new_variable = current.get_variable('owner.New')
assert current.get_variable('owner.relay') is new_variable
assert sum(var.name.endswith('.saved.new') for var in current.program.temporaries.variable_set) == 1
scheduled = current.program.procs[blocks.RunStage.DEFAULT]
keys = [proc.key for proc in scheduled]
assert keys.index(('owner', 'write')) < keys.index(('owner', 'observe'))
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
observer_path = current.get_block_path('observer')
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
assert len(new_shared.program.state_variables.variable_set) == 1
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

# Graph labels preserve the phase even when both read the same state.
owner_graph = current.program.graph.libraries['TestLibrary'].block_type_implementations['Current']
rendered = wirely.cli.graphviz.get_graph(owner_graph).source
assert 'saved.old' in rendered
assert 'saved.new' in rendered

for connector, message in [
    ('value boolean', 'source has type'),
    ('event Type', 'source has kind'),
    ('resource Handle', 'Cannot connect resource and data'),
]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert compile_graph('resource type Handle;\n' + current_definitions.replace(
            'output New: value Type;', 'output New: ' + connector + ';',
        ) + current_source) is None
    assert message in output.getvalue(), output.getvalue()
    assert 'Internal compiler error' not in output.getvalue()

invalid_programs = [
    ('native procedure advance depends on saved.new as "input" produces saved;',
     'Circular procedure dependency'),
    ('''node reader: TestLibrary.Forward { Type: uint8 }
        connect saved.new -> reader;
        native procedure advance depends on reader as "input" produces saved;''',
     'Circular procedure dependency'),
    ('''node reader: TestLibrary.Forward { Type: uint8 }
        connect saved.new -> reader;
        native procedure advance stage output
          depends on saved.old as "input" produces saved;''',
     'later execution stage'),
]
for body, message in invalid_programs:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        invalid_graph = compile_graph(definitions.replace(
            '''node reader: Forward { Type: uint8 }
    connect saved.old -> reader;
    connect reader -> default output;
    native procedure advance
      depends on reader as "input"
      produces saved;''', body + '\nconnect saved.old -> default output;',
        ) + 'node feedback: TestLibrary.Feedback {}')
        assert invalid_graph is not None, output.getvalue()
        assert program_compiler.compile(invalid_graph) is None
    assert message in output.getvalue(), output.getvalue()
    assert 'Hint:' in output.getvalue()
    assert 'Internal compiler error' not in output.getvalue()
