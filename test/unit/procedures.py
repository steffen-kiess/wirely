import contextlib
import datetime
import io
from dataclasses import replace
from unittest.mock import patch

import wirely
import wirely.compiler
from wirely.compiler import program as program_compiler
from wirely.runtime.procedures import Expression


source = engine.get_native_state('source')
source.set_value(False)
engine.step()
engine.assert_value_is('selected', False)
engine.assert_value_is('fallback', True)
engine.assert_value_is('forwarded', False)
engine.assert_value_is('delayed', False)
engine.assert_value_is('delayed.Current', False)
engine.assert_value_is('constants', True)
engine.assert_value_is('constants.TimeValue', datetime.time(8))
engine.assert_value_is('constants.ModeEqual', True)
engine.assert_value_is('constants.UnitEqual', True)
engine.assert_value_is('shared', True)
assert engine.get_variable('shared') is engine.get_variable('shared_again')
engine.assert_is_idle()

source.set_value(True)
engine.step()
engine.assert_value_is('selected', True)
engine.assert_value_is('forwarded', True)
engine.assert_value_is('delayed', False)
engine.assert_value_is('delayed.Current', True)
engine.step()
engine.assert_value_is('delayed', True)
engine.assert_is_idle()

source.set_value(False)
engine.step()
engine.assert_value_is('delayed', True)
engine.assert_value_is('delayed.Current', False)
engine.step()
engine.assert_value_is('delayed', False)
engine.assert_is_idle()


def compile_program(source, *, add_std=False):
    graph = wirely.compiler.compile_strs([('<procedures>', source)], add_std=add_std)
    assert graph is not None
    program = program_compiler.compile(graph)
    assert program is not None
    return replace(program, graph=None)


# No compiler objects or native registration are needed for execution.
pure = compile_program('''node type Pure {
  output InputStage: value boolean;
  default output: value boolean;
  output OutputStage: value boolean;
  temporary early: boolean;
  temporary calculated: boolean;
  procedure read stage input produces InputStage, early {
    InputStage = true;
    early = true;
  }
  procedure compute depends on early
    produces default output as output, calculated
  {
    output = early;
    calculated = early;
  }
  procedure write stage output depends on calculated produces OutputStage {
    OutputStage = calculated;
  }
}
node pure: Pure {}
''')

with patch('wirely.native.get_native_block_type_by_name', side_effect=AssertionError('native lookup')):
    pure_engine = wirely.Engine(pure)
    assert pure_engine.step()[0] == 3
    pure_engine.assert_value_is('pure', True)
    pure_engine.assert_value_is('pure.InputStage', True)
    pure_engine.assert_value_is('pure.OutputStage', True)
    assert pure_engine.step()[0] == 0

# Branches and Boolean operators evaluate only what is needed.
missing = Expression('input', payload='missing')
true = Expression('constant', payload=True)
false = Expression('constant', payload=False)
assert Expression('and', (false, missing)).evaluate({}, {}) is False
assert Expression('or', (true, missing)).evaluate({}, {}) is True
assert Expression('if', (true, true, missing)).evaluate({}, {}) is True
assert Expression('if', (false, missing, false)).evaluate({}, {}) is False

# Generic copies preserve options and structs without exposing their contents.
for value_type, initial in [('Option[boolean]', 'Some(true)'),
                            ('Pair', 'Pair { flag: true }')]:
    generic = compile_program('''struct type Pair { flag: boolean }
node type Constant {
  parameter Type: ValueType;
  parameter Value: Type;
  default output: value Type;
  procedure run produces default output as output { output = Value; }
}
node type Copy {
  parameter Type: ValueType;
  default input: value Type;
  default output: value Type;
  procedure run depends on default input as input
    produces default output as output
  {
    let value = input;
    output = value;
  }
}
''' + f'''node constant: Constant {{ Type: {value_type}, Value: {initial} }}
node copy: Copy {{ Type: {value_type} }}
connect constant -> copy;
''')
    generic_engine = wirely.Engine(generic)
    generic_engine.step()
    assert generic_engine.get_value('copy') == generic_engine.get_value('constant')

# A native procedure and a .wrl procedure can share a node.
mixed_source = '''namespace Std {
  node type Constant {
    parameter Type: ValueType;
    parameter Value: Type;
    default output: value Type;
    temporary temp: Type;
    native procedure produce as "run" produces temp as temp uses Value;
    procedure run depends on temp produces default output as output {
      output = temp;
    }
  }
}
node mixed: Std.Constant { Type: boolean, Value: true }
'''
import wirely.native.std
with patch.object(wirely.native.std.Constant, 'run',
                  staticmethod(lambda *, context: context.set_result('temp', context.values['Value']))):
    mixed_engine = wirely.Engine(compile_program(mixed_source))
    mixed_engine.step()
    mixed_engine.assert_value_is('mixed', True)
    mixed_engine.assert_is_idle()
    mixed_engine.trigger('mixed', 'run')
    assert mixed_engine.step()[0] == 1


# Top-level temporaries and procedures execute in an implicit root instance.
top_level = compile_program('''temporary t: int8;

procedure p1 produces t {
  t = 50;
}

node printer: Std.DebugValuePrinter {Type: int8}
connect t -> printer;
''', add_std=True)
top_engine = wirely.Engine(top_level)
output = io.StringIO()
with contextlib.redirect_stdout(output):
    assert top_engine.step()[0] == 2
assert output.getvalue() == '[printer]: Got value 50\n'
top_engine.assert_value_is('t', 50)
assert top_engine.get_variable('t').name == 't'
assert top_engine.get_block_path('printer') == 'printer'
assert top_engine.step()[0] == 0

# The root exists without child nodes and does not require native lookup.
root_only = compile_program('''temporary t: int8;
procedure p1 produces t { t = 50; }
''')
with patch('wirely.native.get_native_block_type_by_name', side_effect=AssertionError('native lookup')):
    root_engine = wirely.Engine(root_only)
    assert root_engine.step()[0] == 1
    root_engine.assert_value_is('t', 50)
    assert root_engine.step()[0] == 0

# Root dependencies can order child producers, root procedures, and consumers.
chained = compile_program('''temporary first: boolean;
temporary second: boolean;
procedure one depends on source as input produces first { first = input; }
procedure two depends on first, shared as enabled produces second {
  second = first && enabled;
}
node type Copy {
  default input: value boolean;
  default output: value boolean;
  procedure run depends on default input as input
    produces default output as output
  {
    output = input;
  }
}
singleton node type Shared {
  default output: value boolean;
  procedure run produces default output as output { output = true; }
}
node source: Std.DebugValueSource { Type: boolean }
node copied: Copy {}
node shared: Shared {}
node shared_again: Shared {}
connect second -> copied;
''', add_std=True)
chained_engine = wirely.Engine(chained)
chained_source = chained_engine.get_native_state('source')
chained_source.set_value(False)
assert chained_engine.step()[0] == 5
chained_engine.assert_value_is('first', False)
chained_engine.assert_value_is('second', False)
chained_engine.assert_value_is('copied', False)
assert chained_engine.get_variable('shared') is chained_engine.get_variable('shared_again')
assert chained_engine.step()[0] == 0
chained_source.set_value(True)
assert chained_engine.step()[0] == 4
chained_engine.assert_value_is('copied', True)
chained_engine.assert_is_idle()

# Root state keeps old snapshots and exposes new values to same-step readers.
root_state = compile_program('''state saved: boolean = false;
temporary previous: boolean;
temporary current: boolean;
procedure write depends on source as input produces saved { saved = input; }
procedure read_old depends on saved.old as old produces previous {
  previous = old;
}
procedure read_new depends on saved.new as new produces current {
  current = new;
}
node type Copy {
  default input: value boolean;
  default output: value boolean;
  procedure run depends on default input as input
    produces default output as output
  {
    output = input;
  }
}
node source: Std.DebugValueSource { Type: boolean }
node old_copy: Copy {}
node new_copy: Copy {}
connect saved.old -> old_copy;
connect saved.new -> new_copy;
''', add_std=True)
state_engine = wirely.Engine(root_state)
state_source = state_engine.get_native_state('source')
state_source.set_value(True)
state_engine.step()
state_engine.assert_value_is('previous', False)
state_engine.assert_value_is('current', True)
state_engine.assert_value_is('old_copy', False)
state_engine.assert_value_is('new_copy', True)
assert state_engine.get_variable('saved').name == 'saved'
state_engine.step()
state_engine.assert_value_is('previous', True)
state_engine.assert_value_is('old_copy', True)
state_engine.assert_is_idle()
state_source.set_value(False)
state_engine.step()
state_engine.assert_value_is('previous', True)
state_engine.assert_value_is('current', False)
state_engine.assert_value_is('old_copy', True)
state_engine.assert_value_is('new_copy', False)
state_engine.step()
state_engine.assert_value_is('previous', False)
state_engine.assert_value_is('old_copy', False)
state_engine.assert_is_idle()
