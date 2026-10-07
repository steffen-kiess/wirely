import datetime
from unittest.mock import patch

import wirely
import wirely.compiler
import wirely.native.std
from wirely.compiler import program as program_compiler
from wirely.runtime.engine import Engine


def check_main_program():
    source = engine.get_native_state('source')
    source.set_value(False)
    engine.get_native_state('option_source').set_value(None)
    engine.get_native_state('nested_source').set_value(None)
    engine.get_native_state('int_source').set_value(0)
    engine.get_native_state('duration_source').set_value(wirely.Duration(duration_ns=0))
    engine.get_native_state('time_source').set_value(datetime.time(0))
    engine.get_native_state('int8_source').set_value(0)
    engine.get_native_state('uint8_source').set_value(0)
    engine.get_native_state('pair_source').set_value(pair(False, 0))
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
    return program


def check_pure_stages():
    # Procedures can execute without native code.
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

    pure_engine = Engine(pure)
    pure_engine.step()
    pure_engine.assert_value_is('pure', True)
    pure_engine.assert_value_is('pure.InputStage', True)
    pure_engine.assert_value_is('pure.OutputStage', True)
    pure_engine.assert_is_idle()


def check_generic_copies():
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
        generic_engine = Engine(generic)
        generic_engine.step()
        assert generic_engine.get_value('copy') == generic_engine.get_value('constant')


def check_mixed_procedures():
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
    with patch.object(wirely.native.std.Constant, 'run',
                      staticmethod(lambda *, context: context.set_result('temp', context.values['Value']))):
        mixed_engine = Engine(compile_program(mixed_source))
        mixed_engine.step()
        mixed_engine.assert_value_is('mixed', True)
        mixed_engine.assert_is_idle()


def check_root_only():
    # The root exists without child nodes and does not require native lookup.
    root_only = compile_program('''temporary t: int8;
    temporary sum: Option[int8];
    procedure p1 produces t { t = 50; }
    procedure p2 depends on t produces sum { sum = int8.CheckedAdd(t, 100); }
    ''')
    root_engine = Engine(root_only)
    root_engine.step()
    root_engine.assert_value_is('t', 50)
    root_engine.assert_value_is('sum', None)
    root_engine.assert_is_idle()


def check_root_dependencies():
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
    chained_engine = Engine(chained)
    chained_source = chained_engine.get_native_state('source')
    chained_source.set_value(False)
    chained_engine.step()
    chained_engine.assert_value_is('first', False)
    chained_engine.assert_value_is('second', False)
    chained_engine.assert_value_is('copied', False)
    chained_engine.assert_is_idle()
    chained_source.set_value(True)
    chained_engine.step()
    chained_engine.assert_value_is('copied', True)
    chained_engine.assert_is_idle()


def check_root_state():
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
    state_engine = Engine(root_state)
    state_source = state_engine.get_native_state('source')
    state_source.set_value(True)
    state_engine.step()
    state_engine.assert_value_is('previous', False)
    state_engine.assert_value_is('current', True)
    state_engine.assert_value_is('old_copy', False)
    state_engine.assert_value_is('new_copy', True)
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


def check_option_presence():
    source = engine.get_native_state('option_source')
    nested_source = engine.get_native_state('nested_source')
    for value, nested in [(None, None), (wirely.Some(False), wirely.Some(None)),
                          (wirely.Some(True), wirely.Some(wirely.Some(False))),
                          (None, None)]:
        source.set_value(value)
        nested_source.set_value(nested)
        engine.step()
        for name, expected in [('presence', value), ('nested_presence', nested)]:
            engine.assert_value_is(f'{name}.Present', expected is not None)
            engine.assert_value_is(f'{name}.Absent', expected is None)
            engine.assert_value_is(f'{name}.Copy', expected)
            fallback = True if name == 'presence' else wirely.Some(True)
            unwrapped = fallback if expected is None else expected.value
            engine.assert_value_is(f'{name}.Unwrapped', unwrapped)
            engine.assert_value_is(f'{name}.Matched', unwrapped)
            engine.assert_value_is(f'{name}.Ignored', expected is not None)
        engine.assert_value_is('pending', True if value is None else value.value)
    engine.assert_is_idle()



def check_ordering():
    cases = {
        'int': [-2, -1, 0],
        'duration': [wirely.Duration(duration_ns=999_999_999), wirely.Duration(duration_ns=1_000_000_000),
                     wirely.Duration(duration_ns=1_000_000_001)],
        'time': [datetime.time(7, 59), datetime.time(8), datetime.time(8, 1)],
    }
    for name, (below, equal, above) in cases.items():
        source = engine.get_native_state(f'{name}_source')
        for value, expected in [(below, (True, True, False, False)), (equal, (False, True, False, True)),
                                (above, (False, False, True, True))]:
            source.set_value(value)
            engine.step()
            for output, result in zip(('Less', 'LessEqual', 'Greater', 'GreaterEqual'), expected):
                engine.assert_value_is(f'{name}_ordering.{output}', result)
    engine.assert_is_idle()



def check_arithmetic():
    some = wirely.Some
    cases = {  # input: Wrapped, Saturated, Checked, Quotient, Fallback
        'int8': [(127, -128, -127, None, some(0), 1), (-128, -127, 127, None, some(0), 1),
                 (-50, -49, 50, some(-100), some(-2), -100), (0, 1, 0, some(0), None, 0)],
        'uint8': [(255, 0, 0, None, some(0), 1), (3, 4, 0, some(6), some(33), 6),
                  (0, 1, 0, some(0), None, 0)],
    }
    for name, rows in cases.items():
        source = engine.get_native_state(f'{name}_source')
        for value, *expected in rows:
            source.set_value(value)
            engine.step()
            for output, result in zip(('Wrapped', 'Saturated', 'Checked', 'Quotient', 'Fallback'), expected):
                engine.assert_value_is(f'{name}_arithmetic.{output}', result)
    engine.assert_is_idle()



def pair(enabled, number):
    pair_type = wirely.ValueType(engine.get_variable('pair_source').var_type)
    return pair_type.make_struct({'enabled': enabled, 'number': number})


def check_struct_fields():
    source = engine.get_native_state('pair_source')
    for enabled, number, expected_next in [(True, 7, 8), (False, 255, 255), (True, 0, 1)]:
        source.set_value(pair(enabled, number))
        engine.step()
        engine.assert_value_is('pair_fields.Enabled', enabled)
        engine.assert_value_is('pair_fields.Next', expected_next)
        engine.assert_value_is('pair_fields.Built', pair(not enabled, number if enabled else 0))
        engine.assert_value_is('pair_fields.Wrapped', wirely.Some(pair(enabled, number)))
        engine.assert_value_is('pair_fields.Nested',
                               None if number == 0 else wirely.Some(wirely.Some(enabled)))
    engine.assert_is_idle()


check_main_program()
check_pure_stages()
check_generic_copies()
check_mixed_procedures()
check_root_only()
check_root_dependencies()
check_root_state()
check_option_presence()
check_ordering()
check_arithmetic()
check_struct_fields()
