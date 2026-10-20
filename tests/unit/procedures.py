import wirely
import wirely.compiler
from wirely.compiler import program as program_compiler
from wirely.functions import FunctionImplementation
from wirely.runtime.engine import Engine
from tests.infra.support import internal, test_extension, test_contracts, test_engine


def counted_function(function_name):
    """Return recorded calls and an extension wrapping a function."""
    from wirely.runtime.extensions import resolve_extensions
    from tests.infra.native_extension.generated import extension as fixture
    binding = resolve_extensions([fixture]).functions[function_name].load()
    calls = []

    def counted(*arguments):
        calls.append(arguments)
        return binding.implementation(*arguments)

    return calls, test_extension(functions={function_name: FunctionImplementation(counted, binding.contract)})


def check_main_program():
    source = engine.get_native_state('source')
    source.set_value(False)
    engine.get_native_state('constant_source').set_value(False)
    engine.get_native_state('option_source').set_value(None)
    engine.get_native_state('nested_source').set_value(None)
    engine.get_native_state('int_source').set_value(0)
    engine.get_native_state('duration_source').set_value(internal(wirely.Duration(ticks=0)))
    engine.get_native_state('time_source').set_value(internal(wirely.TimeOfDay.from_parts(0, 0)))
    engine.get_native_state('int8_source').set_value(0)
    engine.get_native_state('uint8_source').set_value(0)
    engine.get_native_state('pair_source').set_value(pair(False, 0))
    engine.get_native_state('span_source').set_value(internal(wirely.Duration(ticks=0)))
    engine.get_native_state('factor_source').set_value(1)
    for name in ('monotonic_source', 'monotonic_other'):
        engine.get_native_state(name).set_value(internal(wirely.MonotonicTime(generation=0, ticks=0)))
    engine.get_native_state('monotonic_span').set_value(internal(wirely.Duration(ticks=0)))
    engine.step()
    engine.assert_value_is('selected', False)
    engine.assert_value_is('fallback', True)
    engine.assert_value_is('forwarded', False)
    engine.assert_value_is('delayed', False)
    engine.assert_value_is('delayed.Current', False)
    engine.assert_value_is('constants', True)
    engine.assert_value_is('constants.TimeValue', internal(wirely.TimeOfDay.from_parts(8, 0)))
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
    from tests.infra.native_extension.generated import extension as fixture
    graph = wirely.compiler.compile_strs([('<procedures>', source)], add_std=add_std, extensions=[fixture])
    assert graph is not None
    program = program_compiler.compile(graph)
    assert program is not None
    return program


def check_constant_blocks():
    for name, expected in [('optional_absent', None), ('optional_present', wirely.Some(True))]:
        engine.assert_value_is(name, expected)
        # Inside the runtime, a struct is a tuple of its field values.
        engine.assert_value_is(name + '.Wrapped', (expected,))
    source = engine.get_native_state('constant_source')
    for enabled in (False, True, False):
        source.set_value(enabled)
        engine.step()
        number = 3 if enabled else 9
        for port, value in {
                '': enabled, '.Number': number, '.Saved': number,
                '.ReadBack': number, '.Shifted': number << 2,
                '.Matched': number + 2, '.Selected': 3}.items():
            engine.assert_value_is('constant_blocks' + port, value)
        engine.assert_is_idle()


def check_pure_stages():
    # Procedures can execute without native code.
    pure = compile_program('''node type Pure {
      output InputStage: value boolean;
      default output: value boolean;
      output OutputStage: value boolean;
      binding early: value boolean;
      binding calculated: value boolean;
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

    pure_engine = test_engine(pure)
    pure_engine.step()
    pure_engine.assert_value_is('pure', True)
    pure_engine.assert_value_is('pure.InputStage', True)
    pure_engine.assert_value_is('pure.OutputStage', True)
    pure_engine.assert_is_idle()


def check_output_storage():
    # Output storage does not share slots with declared temporaries.
    program = compile_program('''node type Split {
      output Value: value boolean;
      #[Allow(Unused)] binding Value_val: value boolean;
      procedure run produces Value as value, Value_val {
        value = true;
        Value_val = false;
      }
    }
    node split: Split {}
    ''')
    assert 'split:run.value' in program.temporaries.variables
    engine = test_engine(program)
    engine.step()
    engine.assert_value_is('split.Value', True)
    engine.assert_value_is('split.Value_val', False)


def check_temporary_connections():
    # Temporaries feed outputs and child inputs without separate storage.
    program = compile_program('''node type Copy {
      default input: value boolean;
      default output: value boolean;
      procedure run depends on default input as input produces default output as output {
        output = input;
      }
    }
    node type Normalize {
      output Level: value boolean;
      output Inverted: value boolean;
      binding level: value boolean;
      procedure normalize produces level { level = true; }
      procedure invert depends on level produces Inverted as inverted {
        inverted = if level { false } else { true };
      }
      node child: Copy {}
      connect level -> child;
      connect level -> Level;
    }
    node n: Normalize {}
    node reader: Copy {}
    connect n.Level -> reader;
    ''')
    engine = test_engine(program)
    engine.step()
    assert engine.get_variable('n.Level') is engine.get_variable('n.level')
    for name, value in [('n.Level', True), ('n.Inverted', False), ('n.child', True), ('reader', True)]:
        engine.assert_value_is(name, value)
    engine.assert_is_idle()


def check_procedure_ports():
    # Connections wire procedure ports to each other, variables, and children.
    program = compile_program('''node type Copy {
      default input: value boolean;
      default output: value boolean;
      procedure run depends on default input as input produces default output as output {
        output = input;
      }
    }
    node type Ports {
      default input: value boolean;
      output Inverted: value boolean;
      output Twice: value boolean;
      output Latest: value boolean;
      output Previous: value boolean;
      output Copied: value boolean;
      output Fed: value boolean;
      state last: boolean = false;
      binding named: value boolean;
      node copy: Copy {}
      node fed: Copy {}

      procedure invert depends on value: value boolean produces inverted: value boolean {
        inverted = !value;
      }
      procedure again depends on invert.inverted as value produces result: value boolean {
        result = !value;
      }
      procedure remember depends on value: value boolean produces next: value boolean {
        next = value;
      }
      procedure feed depends on default input as value produces fed as copied {
        copied = value;
      }

      connect default input -> invert.value;
      connect invert.inverted -> Inverted;
      connect invert.inverted -> copy;
      connect copy -> Copied;
      connect again.result -> named;
      connect named -> Twice;
      connect invert.inverted -> remember.value;
      connect remember.next -> last;
      connect remember.next -> Latest;
      connect last.old -> Previous;
      connect fed -> Fed;
    }
    node source: Std.DebugValueSource { Type: boolean }
    node p: Ports {}
    connect source -> p;
    ''', add_std=True)
    assert 'p:invert.inverted' in program.temporaries.variables
    engine = test_engine(program)
    assert engine.get_variable('p.named') is engine.get_variable('p.Twice')
    source = engine.get_native_state('source')
    for value, previous in [(True, False), (False, False), (True, True)]:
        source.set_value(value)
        engine.step()
        for name, expected in [('p.Inverted', not value), ('p.Copied', not value), ('p.Twice', value),
                               ('p.Latest', not value), ('p.Previous', previous), ('p.Fed', value)]:
            engine.assert_value_is(name, expected)


def check_generic_copies():
    # Generic copies preserve options and structs without exposing their contents.
    for value_type, initial in [('Option[boolean]', 'Some(true)'),
                                ('Pair', 'Pair { flag: true }')]:
        generic = compile_program('''struct type Pair { flag: boolean }
    node type Constant {
      parameter Type: type;
      parameter Value: Type;
      default output: value Type;
      procedure run produces default output as output { output = Value; }
    }
    node type Copy {
      parameter Type: type;
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
        generic_engine = test_engine(generic)
        generic_engine.step()
        assert generic_engine.get_value('copy') == generic_engine.get_value('constant')


def check_mixed_procedures():
    # A native procedure and a .wrl procedure can share a node.
    mixed_source = '''namespace Std {
      node type Constant {
        parameter Type: type;
        parameter Value: Type;
        default output: value Type;
        binding temp: value Type;
        native procedure produce as "TestLibrary.TestNode:test_procedure"
          produces temp as temp uses Value, Type;
        procedure run depends on temp produces default output as output {
          output = temp;
        }
      }
    }
    node mixed: Std.Constant { Type: boolean, Value: true }
    '''
    mixed_program = compile_program(mixed_source)
    with test_contracts(mixed_program.graph) as contracts:
        class Produce(contracts.TestNodeTestProcedure):
            def prepare(self, *, services, value, type):
                self.value = value

            def run(self, *, services):
                return contracts.TestNodeTestProcedureResult(temp=self.value)

        bindings = test_extension(procedures={'TestLibrary.TestNode:test_procedure': Produce}, contracts=contracts)
        mixed_engine = Engine(mixed_program, override_extensions=[bindings])
    mixed_engine.step()
    mixed_engine.assert_value_is('mixed', True)
    mixed_engine.assert_is_idle()


def check_root_only():
    # The root exists without child nodes and does not require native lookup.
    root_only = compile_program('''binding t: value int8;
    #[Allow(Unused)] binding sum: value Option[int8];
    procedure p1 produces t { t = 50; }
    procedure p2 depends on t produces sum { sum = int8.CheckedAdd(t, 100); }
    ''')
    root_engine = test_engine(root_only)
    root_engine.step()
    root_engine.assert_value_is('t', 50)
    root_engine.assert_value_is('sum', None)
    root_engine.assert_is_idle()


def check_root_dependencies():
    # Root dependencies can order child producers, root procedures, and consumers.
    chained = compile_program('''binding first: value boolean;
    binding second: value boolean;
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
    chained_engine = test_engine(chained)
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
    #[Allow(Unused)] binding previous: value boolean;
    #[Allow(Unused)] binding current: value boolean;
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
    state_engine = test_engine(root_state)
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
        'duration': [wirely.Duration(ticks=2**64 - 1), wirely.Duration(ticks=2**64),
                     wirely.Duration(ticks=2**64 + 1)],
        'time': [wirely.TimeOfDay.from_parts(7, 59), wirely.TimeOfDay.from_parts(8, 0), wirely.TimeOfDay.from_parts(8, 1)],
    }
    for name, (below, equal, above) in cases.items():
        source = engine.get_native_state(f'{name}_source')
        for value, expected in [(below, (True, True, False, False)), (equal, (False, True, False, True)),
                                (above, (False, False, True, True))]:
            source.set_value(internal(value))
            engine.step()
            for output, result in zip(('Less', 'LessEqual', 'Greater', 'GreaterEqual'), expected):
                engine.assert_value_is(f'{name}_ordering.{output}', result)
    engine.assert_is_idle()



def check_builtin_ordering():
    # Operators link their helpers and evaluate operands before reversing them.
    maximum = 'P213503982334601DT7H15.99999999999999999995S'
    for type_name, low, high in [('Std.Duration', 'PT0S', maximum), ('Schedule.TimeOfDay', '00:00', '24:00')]:
        for left, right, expected in [(low, high, (True, True, False, False)),
                                      (high, high, (False, True, False, True)),
                                      (high, low, (False, False, True, True))]:
            program = compile_program('''node type Ordering {
              parameter Type: type;
              parameter Left: Type;
              parameter Right: Type;
              state left: Type = Left;
              state right: Type = Right;
              output Less: value boolean;
              output LessEqual: value boolean;
              output Greater: value boolean;
              output GreaterEqual: value boolean;
              procedure run depends on left.old as a, right.old as b
                produces left, right, Less, LessEqual, Greater, GreaterEqual
              {
                left = a;
                right = b;
                let mut value = a;
                Less = value < { value = b; b };
                value = a;
                LessEqual = value <= { value = b; b };
                value = a;
                Greater = value > { value = b; b };
                value = a;
                GreaterEqual = value >= { value = b; b };
              }
            }
            node ordering: Ordering { Type: TYPE, Left: "LEFT", Right: "RIGHT" }
            '''.replace('TYPE', type_name).replace('LEFT', left).replace('RIGHT', right), add_std=True)
            calls, extension = counted_function(f'{type_name}.Less')
            machine = test_engine(program, override_extensions=[extension])
            machine.step()
            assert len(calls) == 4
            for output, result in zip(('Less', 'LessEqual', 'Greater', 'GreaterEqual'), expected):
                machine.assert_value_is(f'ordering.{output}', result)
            machine.assert_is_idle()


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
    """Return a Pair as the runtime represents it: a tuple of its field values."""
    fields = {'enabled': enabled, 'number': number}
    return tuple(fields[name] for name in engine.get_variable('pair_source').var_type.fields)


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



def check_conversions():
    source = engine.get_native_state('int_source')
    some = wirely.Some
    for value, wrapped, saturated, checked, level in [
            (300, 44, 127, None, 1), (-129, 127, -128, None, 0),
            (-128, -128, -128, some(-128), 0), (5, 5, 5, some(5), 1)]:
        source.set_value(value)
        engine.step()
        for output, expected in [('Wrapped', wrapped), ('Saturated', saturated), ('Checked', checked),
                                 ('Wide', value), ('Level', level)]:
            engine.assert_value_is(f'conversions.{output}', expected)
    engine.assert_is_idle()



def check_native_functions():
    # Runtime native functions run in procedures and in compile-time expressions.
    # Keys select implementations by native name.
    functions = compile_program('''namespace TestLibrary {
      native function Limit as "TestLibrary.Clamp"(input: int16, minimum: int16, maximum: int16): int16;
      native function Nothing(): unit;
    }
    node type Clamped {
      constant High: int16 = TestLibrary.Limit(500, 0, 10);
      default input: value int16;
      default output: value int16;
      output Done: value unit;
      procedure run depends on default input as input produces default output as output, Done {
        let mut low: int16 = -10;
        TestLibrary.Nothing();
        output = TestLibrary.Limit(input, low, { low = 100; High });
        Done = TestLibrary.Nothing();
      }
    }
    node source: Std.DebugValueSource { Type: int16 }
    node clamped: Clamped {}
    connect source -> clamped;
    ''', add_std=True)
    calls, clamp = counted_function('TestLibrary.Clamp')
    nothing_calls, nothing = counted_function('TestLibrary.Nothing')
    function_engine = test_engine(functions, override_extensions=[clamp, nothing])
    source = function_engine.get_native_state('source')
    for value, expected in [(5, 5), (500, 10), (-500, -10)]:
        source.set_value(value)
        calls.clear()
        function_engine.step()
        assert calls == [(wirely.Int16(value), wirely.Int16(-10), wirely.Int16(10))]
        assert not nothing_calls
        function_engine.assert_value_is('clamped', expected)
        function_engine.assert_value_is('clamped.Done', wirely.Unit())
    function_engine.assert_is_idle()

    # An exception from a runtime function is an internal error, not a program error.
    from tests.infra.native_extension.generated import extension as fixture
    diagnostics = []
    graph = wirely.compiler.compile_strs([('<fail>', '''namespace TestLibrary {
      native function Fail(): boolean;
    }
    constant Value: boolean = TestLibrary.Fail();
    ''')], on_diagnostic=diagnostics.append, extensions=[fixture])
    assert graph is not None
    assert program_compiler.compile(graph, on_diagnostic=diagnostics.append) is None
    assert any(diagnostic.kind == 'internal_error'
               and "Internal error in function `TestLibrary.Fail`" in diagnostic.message
               for diagnostic in diagnostics), diagnostics



def check_duration_functions():
    def duration(ticks):
        return internal(wirely.Duration(ticks=ticks))

    second = wirely.types.time.TICKS_PER_SECOND
    maximum = wirely.Duration.MAX_TICKS
    span_source = engine.get_native_state('span_source')
    factor_source = engine.get_native_state('factor_source')
    some = wirely.Some
    for span, factor, total, difference, scaled, divided in [
            (3 * second, 2, 4 * second, 2 * second, some(duration(6 * second)), some(duration(second + second // 2))),
            (second // 2, 0, second + second // 2, 0, some(duration(0)), None),
            (maximum, 2, maximum, maximum - second, None, some(duration(maximum // 2)))]:
        span_source.set_value(duration(span))
        factor_source.set_value(factor)
        engine.step()
        for output, expected in [('Sum', duration(total)), ('Difference', duration(difference)),
                                 ('Scaled', scaled), ('Divided', divided),
                                 ('FactorSecs', duration(factor * second)), ('Millis', span * 1_000 // second)]:
            engine.assert_value_is(f'duration_math.{output}', expected)
    engine.assert_is_idle()



def check_time_of_day_functions():
    parse = wirely.TimeOfDay.parse
    source = engine.get_native_state('time_source')
    for time, later, earlier, until_end in [('08:00', '10:00', '06:00', 'PT16H'),
                                            ('23:00', '01:00', '21:00', 'PT1H'),
                                            ('24:00', '02:00', '22:00', 'PT0S'),
                                            ('00:00', '02:00', '22:00', 'PT24H')]:
        source.set_value(internal(parse(time)))
        engine.step()
        engine.assert_value_is('time_math.Later', internal(parse(later)))
        engine.assert_value_is('time_math.Earlier', internal(parse(earlier)))
        engine.assert_value_is('time_math.UntilEnd', internal(wirely.Duration.parse(until_end)))
    engine.assert_is_idle()



def check_monotonic_time_functions():
    def time(generation, ticks):
        return internal(wirely.MonotonicTime(generation=generation, ticks=ticks))

    some, maximum = wirely.Some, wirely.MonotonicTime.MAX_TICKS
    sources = [engine.get_native_state(name) for name in ('monotonic_source', 'monotonic_other', 'monotonic_span')]
    outputs = ('Later', 'Earlier', 'CheckedLater', 'CheckedEarlier', 'Elapsed', 'Less')
    for value, other, span, expected in [
            # Within range; other is earlier in the same generation.
            (time(1, 100), time(1, 40), 10,
             (time(1, 110), time(1, 90), some(time(1, 110)), some(time(1, 90)), some(60), some(False))),
            # Saturation at both ends; other is later.
            (time(1, 5), time(1, maximum), maximum,
             (time(1, maximum), time(1, 0), None, None, None, some(True))),
            # Equal times.
            (time(1, 7), time(1, 7), 0,
             (time(1, 7), time(1, 7), some(time(1, 7)), some(time(1, 7)), some(0), some(False))),
            # Different generations, including the generation-0 default.
            (time(2, 100), time(0, 0), 1,
             (time(2, 101), time(2, 99), some(time(2, 101)), some(time(2, 99)), None, None))]:
        for source, item in zip(sources, (value, other, internal(wirely.Duration(ticks=span)))):
            source.set_value(item)
        engine.step()
        for output, result in zip(outputs, expected):
            if output == 'Elapsed' and result is not None:
                result = some(internal(wirely.Duration(ticks=result.value)))
            engine.assert_value_is(f'monotonic_math.{output}', result)
    engine.assert_is_idle()



def check_mutation():
    source = engine.get_native_state('source')
    for value in (False, True, False, True):
        source.set_value(value)
        engine.step()
        for output, expected in {
                'Saved': value, 'Changed': not value, 'Shadowed': not value,
                'Chosen': value, 'ReadOutput': not value, 'ShortCircuit': value,
                'Pattern': value, 'Difference': 8, 'SelfUpdate': 8, 'Snapshot': True,
                'Fields': pair(True, 2), 'Fallback': value, 'FallbackRan': True,
                'Unit': wirely.Unit()}.items():
            engine.assert_value_is(f'mutation.{output}', expected)
    engine.step()
    engine.assert_is_idle()


check_main_program()
check_constant_blocks()
check_pure_stages()
check_output_storage()
check_temporary_connections()
check_procedure_ports()
check_generic_copies()
check_mixed_procedures()
check_root_only()
check_root_dependencies()
check_root_state()
check_option_presence()
check_ordering()
check_builtin_ordering()
check_arithmetic()
check_struct_fields()
check_conversions()
check_native_functions()
check_duration_functions()
check_time_of_day_functions()
check_monotonic_time_functions()
check_mutation()


def check_multi_procedures():
    # Compile the accepted language fixture as setup for runtime checks. The
    # flattening order checked below follows declaration order, so do not shuffle.
    fixture = 'tests/compiler/multi_procedures.wrl'
    with open(fixture, encoding='utf-8') as file:
        graph = wirely.compiler.compile_strs([(fixture, file.read())], add_std=True, shuffle_seed=None)
    machine = test_engine(program_compiler.compile(graph))
    machine.step()
    for name, expected in {'digits': 21354, 'sum': 12, 'empty': 0, 'single': 6}.items():
        machine.assert_value_is(name, expected)
    machine.assert_is_idle()
    procedure = next(proc for schedule in machine.program.procs.values() for proc in schedule
                     if proc.node_id == 'digits')
    assert procedure.reference.endswith(':run')
    assert [name for name, _ in procedure.dependencies] == ['z[0]', 'z[1]', 'middle', 'a[0]', 'a[1]']
    assert all(isinstance(source, wirely.program.InputSource) for _, source in procedure.dependencies)

    # A short-circuited member still schedules a new invocation when it changes.
    program = compile_program('''
    node first: Std.DebugValueSource { Type: boolean }
    node second: Std.DebugValueSource { Type: boolean }
    node combined: Std.And {}
    connect first, second -> combined;
    ''', add_std=True)
    machine = test_engine(program)
    first = machine.get_native_state('first')
    second = machine.get_native_state('second')
    first.set_value(False)
    second.set_value(False)
    machine.step()
    machine.assert_value_is('combined', False)
    machine.assert_is_idle()
    second.set_value(True)
    run_count, _, _ = machine.step()
    assert run_count == 2
    machine.assert_value_is('combined', False)
    first.set_value(True)
    machine.step()
    machine.assert_value_is('combined', True)
    machine.assert_is_idle()


check_multi_procedures()


async def run_test():
    pass
