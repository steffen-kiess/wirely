import contextlib
import io
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import wirely as blocks
import wirely.compiler
import wirely.graphviz
import wirely.native.test_library
from wirely.native import native_block_type


engine.step()
engine.assert_value_is('empty', True)

registry = {}
calls = Counter()
list_inputs = []


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
        context.set_result('output', list(values))


definitions = '''
namespace TestLibrary {
  node type Forward {
    parameter Type : ValueType;
    implicit input : value Type;
    implicit output : value Type;
    native procedure run
      depends on implicit input as "input"
      produces implicit output as "output";
  }

  node type Relay {
    parameter Type : ValueType;
    implicit input : value Type;
    implicit output : value Type;
    connect implicit input -> implicit output;
  }

  node type Owner {
    parameter Type : ValueType;
    parameter InitialValue : Type;
    implicit input : value Type;
    implicit output : value Type;
    state saved : Type = InitialValue;
    connect saved.old -> implicit output;

    node relay : Relay(Type = Type);
    node reader : Forward(Type = Type);
    connect saved.old -> relay;
    connect relay -> reader;
    native procedure write stage output
      depends on implicit input as "input"
      produces saved;
  }

  node type Feedback {
    implicit output : value uint8;
    state saved : uint8 = 0;
    node reader : Forward(Type = uint8);
    connect saved.old -> reader;
    connect reader -> implicit output;
    native procedure advance
      depends on reader as "input"
      produces saved;
  }

  singleton node type Shared {
    implicit output : value uint8;
    state saved : uint8 = 0;
    node relay : Relay(Type = uint8);
    node reader : Forward(Type = uint8);
    connect saved.old -> relay;
    connect relay -> reader;
    connect saved.old -> implicit output;
    native procedure advance
      depends on saved.old as "old"
      produces saved;
  }

  node type Collect {
    implicit input : list value boolean;
    implicit output : value List[boolean];
    native procedure run
      depends on implicit input as "input"
      produces implicit output as "output";
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
    program = blocks.Program.compile(graph)
    assert program is not None
    return blocks.Engine(program)


probe = make_engine('''
node source : Std.DebugValueSource(Type = boolean);
node other : Std.Constant(Type = boolean, Value = false);
node owner : TestLibrary.Owner(Type = boolean, InitialValue = false);
node second : TestLibrary.Owner(Type = boolean, InitialValue = true);
node collector : TestLibrary.Collect;
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
probe.assert_value_is('collector', (False, True, True))
owner_path = probe.get_block_path('owner')
owner_state = owner_path[-1].block_statevars[0]
assert probe.state[probe.program.state_map[owner_path, owner_state]] is True
assert probe.get_value('owner.relay') is False
assert probe.get_value('owner.reader.') is False

owner_variable = probe.program.variable_map[probe.get_variable('owner')]
assert owner_variable is probe.program.state_map[owner_path, owner_state]
assert owner_variable not in probe.program.temporaries.variable_set
assert probe.program.variable_map[probe.get_variable('owner.relay')] is owner_variable
assert probe.program.variable_map[probe.get_variable('owner.reader.')] is owner_variable
assert '() = False' in wirely.graphviz.get_graph(probe.program.graph, probe).source

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

feedback = make_engine('node feedback : TestLibrary.Feedback;')
assert len(feedback.program.temporaries.variable_set) == 3
for value in (0, 1, 2):
    feedback.step()
    feedback.assert_value_is('feedback', value)
feedback.step()
feedback.assert_is_idle()

shared = make_engine('''
node first : TestLibrary.Shared;
node second : TestLibrary.Shared;
node observer : TestLibrary.Forward(Type = uint8);
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
node source : Std.Constant(Type = boolean, Value = true);
node owner : TestLibrary.Owner(Type = boolean, InitialValue = false);
connect source -> owner;
''')
assert all('saved' not in var.name or var.name.endswith(('.new', '.changed'))
           for var in minimal.program.temporaries.variable_set)

# A later-stage reader still sees the snapshot from before the state writer.
later = make_engine('''
node source : Std.Constant(Type = boolean, Value = true);
node owner : TestLibrary.Owner(Type = boolean, InitialValue = false);
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
  implicit input : value uint8;
  implicit output : value uint8;
  node owner : TestLibrary.Owner(Type = uint8, InitialValue = 7);
  connect implicit input -> owner;
  connect owner -> implicit output;
}
node source : Std.Constant(Type = uint8, Value = 8);
node other : Std.Constant(Type = uint8, Value = 9);
node first : Envelope;
node second : Envelope;
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

invalid_connections = [
    ('saved -> implicit output;', 'old value with .old'),
    ('saved.new -> implicit output;', 'old value with .old'),
    ('saved.old -> sink;', 'source has type'),
    ('saved.old -> event_sink;', 'source has kind'),
    ('temporary_value.old -> sink;', 'Unable to find source block'),
    ('missing.old -> sink;', 'Unable to find source block'),
    ('saved.old -> resource_sink;', 'Cannot connect resource and data'),
    ('source -> saved.old;', 'Unable to find target block'),
    ('child.saved.old -> sink;', 'unexpected number of identifiers'),
]
for connection, message in invalid_connections:
    source = '''
resource type Handle;
node type Sink { implicit input : value uint8; }
node type EventSink { implicit input : event boolean; }
node type ResourceSink { implicit input : resource Handle; }
node type Invalid {
  state saved : boolean = false;
  temporary temporary_value : boolean;
  implicit output : value boolean;
  node sink : Sink;
  node event_sink : EventSink;
  node resource_sink : ResourceSink;
  node source : Std.Constant(Type = boolean, Value = true);
  CONNECTION
}
'''.replace('CONNECTION', 'connect ' + connection)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert compile_graph(source) is None
    assert message in output.getvalue(), output.getvalue()
    assert 'Internal compiler error' not in output.getvalue()
