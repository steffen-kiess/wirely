import asyncio
import contextlib
import datetime
import enum
import re
from pathlib import Path
from textwrap import indent
from typing import NamedTuple
from unittest import TestCase

from wirely.runtime.extensions import RuntimeNativeExtension
from wirely.generated import extension as builtin_extension
import wirely
from wirely.compiler import program as program_compiler
import wirely.compiler
from wirely.program import RunStage
from wirely.runtime.engine import Engine
from wirely.runtime.native_api import NativeProcedure
from tests.infra.support import test_extension, test_contracts
from wirely.runtime.signatures import Builtin, Enum, Int
from wirely.types.value_types import NativeEnum, NativeStruct, OpenIntEnum
import wirely.generated.structs
import wirely.generated.enums


checks = TestCase()
std_source = Path('wirely/lib/std.wrl').read_text()


class Raw(NamedTuple):
    """A callback result that a probe returns as is, instead of building its result class."""

    result: object


def compile_program(sources):
    graph = wirely.compiler.compile_strs(sources)
    assert graph is not None
    program = program_compiler.compile(graph)
    assert program is not None
    return program


def make_engine(declaration, callback, *, extra='', parameters='', singleton=False, override_extensions=()):
    """Create an Engine with a TestLibrary.Probe whose native procedure run calls a callback.

    The callback receives the procedure's services, inputs, and prepared static values by
    Python name and returns its outputs by Python name, None for none, or a Raw result.
    """
    qualifier = 'singleton ' if singleton else ''
    source = f'''
namespace TestLibrary {{
  {qualifier}node type Probe {{
{indent(declaration.strip(), "    ")}
  }}
}}

node probe: TestLibrary.Probe {{ {parameters} }}
{extra}
'''
    program = compile_program([('wirely/lib/std.wrl', std_source), ('<native-interface>', source)])
    with test_contracts(program.graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class Probe:
            class Run(contracts.ProbeRun):
                def prepare(self, *, services, **values):
                    self.values = values

                def run(self, *, services, **inputs):
                    outputs = callback(services, inputs, self.values)
                    if isinstance(outputs, Raw):
                        return outputs.result
                    return contracts.ProbeRunResult(**(outputs or {}))
        registry['procedures']['TestLibrary.Probe:run'] = Probe.Run

        bindings = test_extension(**registry, contracts=contracts)
        return Engine(program, override_extensions=[*override_extensions, bindings])


def expect_output_error(declaration, output, message, **options):
    """Check that a probe returning output for its port 'output' fails with a message."""
    probe = make_engine(declaration, lambda services, inputs, values: {'output': output}, **options)
    with checks.assertRaisesRegex(ValueError, message):
        probe.step()


def check_input_ports():
    seen = []

    def check_inputs(services, inputs, values):
        assert inputs == {'value': wirely.UInt8(7), 'named': wirely.UInt8(7)}
        assert not values
        with checks.assertRaises(AttributeError):
            services.name = 'other'
        assert services.name == 'probe'
        seen.append(inputs)
        return {'output': inputs['value']}

    probe = make_engine('''
    default input: value uint8;
    input named: value uint8;
    default output: value uint8;

    native procedure run
      depends on default input as "value", named
      produces default output as "output";
    ''', check_inputs, extra='''
    node source: Std.Constant { Type: uint8, Value: 7 }
    connect source -> probe;
    connect source -> probe.named;
    ''')
    probe.step()
    probe.assert_value_is('probe', 7)
    probe.assert_is_idle()
    assert len(seen) == 1


def check_wakeups():
    def check_wakeup(services, deadline):
        assert services.wakeup_time is None
        with checks.assertRaises(ValueError):
            services.set_wakeup_time('soon')
        services.set_wakeup_time(deadline)
        assert services.wakeup_time == deadline
        with checks.assertRaisesRegex(ValueError, 'already set'):
            services.set_wakeup_time(None)

    for generation_kind in [None, 'current', 'default', 'previous', 'future']:
        wakeup_probe = make_engine(
            'native procedure run;',
            lambda services, inputs, values: check_wakeup(services, deadline),
        )
        wakeup_probe.enable_fixed_monotonic_time()
        now = wakeup_probe.get_monotonic_time()
        generations = {'current': now.generation, 'default': 0,
                       'previous': now.generation - 1, 'future': now.generation + 1}
        deadline = (None if generation_kind is None else
                    wirely.MonotonicTime(generation=generations[generation_kind], ticks=100))
        wakeup_probe.step()
        if deadline is None:
            assert wakeup_probe.next_wakeup is None
        else:
            # A wakeup from a non-current generation is due now.
            expected = deadline if deadline.generation == now.generation else now
            assert wakeup_probe.next_wakeup == expected

    for explicit_cancellation in [False, True]:
        wakeup_calls = []

        def schedule_wakeup(services, inputs, values):
            assert services.wakeup_time is None
            wakeup_calls.append(services)
            if len(wakeup_calls) == 1:
                services.set_wakeup_time(deadline)
            elif explicit_cancellation:
                services.set_wakeup_time(None)

        wakeup_probe = make_engine('native procedure run;', schedule_wakeup)
        wakeup_probe.enable_fixed_monotonic_time()
        deadline = wakeup_probe.get_monotonic_time() + wirely.Duration(ticks=10)
        wakeup_probe.step()
        assert wakeup_probe.next_wakeup == deadline
        wakeup_probe.step()
        assert len(wakeup_calls) == 1
        assert wakeup_probe.next_wakeup == deadline
        wakeup_probe.advance_fake_monotonic_time(wirely.Duration(ticks=10))
        wakeup_probe.step()
        assert len(wakeup_calls) == 2
        assert wakeup_probe.next_wakeup is None
        wakeup_probe.step()
        assert len(wakeup_calls) == 2
        wakeup_probe.assert_is_idle()


def check_context_names():
    named_contexts = []

    def check_node_name(services, inputs, values):
        named_contexts.append(services.name)

    named_engine = make_engine('native procedure run;', check_node_name, extra='''
    node type Outer {
      node child: TestLibrary.Probe {}
    }
    node outer: Outer {}
    namespace TestLibrary {
      singleton node type SharedContainer {
        node child: Probe {}
      }
    }
    node shared: TestLibrary.SharedContainer {}
    node shared_again: TestLibrary.SharedContainer {}
    ''')
    named_engine.step()
    assert sorted(named_contexts) == ['TestLibrary.SharedContainer.child', 'outer.child', 'probe']
    named_contexts.clear()
    named_engine = make_engine('native procedure run;', check_node_name, singleton=True)
    named_engine.step()
    assert named_contexts == ['TestLibrary.Probe']


def check_stage_times():
    clock_registry = {'initializers': {}, 'procedures': {}}
    clock_declarations = []
    stage_snapshots = {}
    clock_contexts = []
    stages = list(RunStage)
    utc_times = [datetime.datetime(2026, 1, 1, 12, 0, index,
                                   tzinfo=datetime.timezone.utc)
                 for index in range(3)]
    local_timezone = datetime.timezone(datetime.timedelta(hours=2))
    local_times = [time.astimezone(local_timezone) for time in utc_times]
    clock_offset = wirely.Duration.from_seconds(5)

    def check_stage_time(services, stage):
        snapshot = services.start_time_for_stage(stage)
        index = stages.index(stage)
        assert snapshot.monotonic == monotonic_times[index]
        assert snapshot.utc == utc_times[index]
        assert snapshot.utc.tzinfo is datetime.timezone.utc
        assert snapshot.local.isoformat() == local_times[index].isoformat()
        assert snapshot.local.tzinfo == local_timezone
        with checks.assertRaises(AttributeError):
            snapshot.utc = None
        if stage in stage_snapshots:
            assert stage_snapshots[stage] == snapshot
        else:
            stage_snapshots[stage] = snapshot
            clock_engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
            clock_engine.set_fake_wall_time(snapshot.local + datetime.timedelta(seconds=1))
        for candidate in stages:
            if stages.index(candidate) <= index:
                assert services.start_time_for_stage(candidate) == stage_snapshots[candidate]
            else:
                with checks.assertRaises(KeyError):
                    services.start_time_for_stage(candidate)
        with checks.assertRaises(ValueError):
            services.start_time_for_stage('DEFAULT')
        clock_contexts.append(services)

    procedure_stages = {}
    for stage in stages:
        for index in range(2):
            name = f'{stage.name.lower()}_{index}'
            procedure_stages[name] = stage
            stage_clause = '' if stage is RunStage.DEFAULT else f' stage {stage.name.lower()}'
            clock_declarations.append(f'native procedure {name}{stage_clause};')

    clock_source = '''namespace TestLibrary {
      node type ClockProbe {
    ''' + '\n'.join(f'    {line}' for line in clock_declarations) + '''
      }
    }
    node probe: TestLibrary.ClockProbe {}
    '''
    program = compile_program([('<stage-times>', clock_source)])
    with test_contracts(program.graph) as contracts:
        for name, stage in procedure_stages.items():
            contract = getattr(contracts, 'ClockProbe' + name.title().replace('_', ''))

            def record_time(self, *, services, stage=stage, contract=contract):
                check_stage_time(services, stage)
                return getattr(contracts, contract.__name__ + 'Result')()

            clock_registry['procedures'][f'TestLibrary.ClockProbe:{name}'] = type(name, (contract,), {'run': record_time})
        bindings = test_extension(**clock_registry, contracts=contracts)
        clock_engine = Engine(program, override_extensions=[bindings])
    clock_engine.fake_monotonic_time_offset = clock_offset
    clock_engine.enable_fixed_monotonic_time()
    monotonic_times = [clock_engine.get_monotonic_time() + wirely.Duration.from_seconds(index)
                       for index in range(3)]

    clock_engine.set_fake_wall_time(local_times[0])
    clock_engine.step()
    assert len(clock_contexts) == 6
    clock_engine.step()
    assert len(clock_contexts) == 6


class ProbeState:
    """The Python type of TestLibrary.ProbeState resources in check_triggers()."""


def check_triggers():
    def trigger_probe(prefix, contracts):
        """Return the native state class of TestLibrary.TriggerProbe or a type with its body."""
        def contract(name):
            return getattr(contracts, prefix + name)

        class TriggerProbe(contract('Initializer'), ProbeState):
            def initialize(self, *, services):
                with checks.assertRaises(AttributeError):
                    services.name = 'other'
                self.calls = []
                self.reschedule_once = False
                services.trigger('refresher')
                assert not self.calls
                return contract('InitializerResult')(refresher=self, runner=self)

            class Refresh(contract('Refresh')):
                def run(self, *, services, state):
                    assert services.name == state.services.name
                    state.calls.append('refresh')
                    if state.reschedule_once:
                        state.reschedule_once = False
                        services.trigger_self()
                    return contract('RefreshResult')()

            class Run(contract('Run')):
                def run(self, *, services, state):
                    state.calls.append('run')
                    return contract('RunResult')()

            class Log(contract('Log')):
                def run(self, *, services, state):
                    state.calls.append('log')
                    return contract('LogResult')()

        TriggerProbe.__name__ = prefix
        return TriggerProbe

    reader_calls = []

    probe_body = '''
        binding refresher: resource ProbeState;
        binding runner: resource ProbeState;
        native initializer produces refresher as "refresher", runner as "runner";
        native procedure refresh depends on refresher as "state";
        native procedure run depends on runner as "state";
        native procedure log depends on runner as "state";
    '''
    trigger_graph = wirely.compiler.compile_strs([('<native-triggers>', f'''
    namespace TestLibrary {{
      resource type ProbeState;
      resource type Service;
      native node type TriggerProbe {{{probe_body}}}
      singleton native node type SharedTriggerProbe {{{probe_body}}}
      native node type Provider {{
        default output: resource Service;
        native initializer produces default output as "service";
      }}
      node type Reader {{
        input Service: resource Service;
        native procedure poll depends on Service as "service";
      }}
    }}
    node first: TestLibrary.TriggerProbe {{}}
    node second: TestLibrary.TriggerProbe {{}}
    node shared: TestLibrary.SharedTriggerProbe {{}}
    node shared_again: TestLibrary.SharedTriggerProbe {{}}
    node provider: TestLibrary.Provider {{}}
    node other_provider: TestLibrary.Provider {{}}
    node reader: TestLibrary.Reader {{}}
    node another_reader: TestLibrary.Reader {{}}
    node other_reader: TestLibrary.Reader {{}}
    connect provider -> reader.Service;
    connect provider -> another_reader.Service;
    connect other_provider -> other_reader.Service;
    ''')])
    assert trigger_graph is not None
    trigger_program = program_compiler.compile(trigger_graph)
    assert trigger_program is not None
    with test_contracts(trigger_graph) as contracts:
        trigger_registry = {'initializers': {}, 'procedures': {}}
        for prefix in ('TriggerProbe', 'SharedTriggerProbe'):
            cls = trigger_probe(prefix, contracts)
            trigger_registry['initializers']['TestLibrary.' + prefix] = cls
            trigger_registry['procedures'].update({
                f'TestLibrary.{prefix}:refresh': cls.Refresh,
                f'TestLibrary.{prefix}:run': cls.Run,
                f'TestLibrary.{prefix}:log': cls.Log,
            })

        class Provider(contracts.ProviderInitializer):
            def initialize(self, *, services):
                return contracts.ProviderInitializerResult(service=self)
        trigger_registry['initializers']['TestLibrary.Provider'] = Provider

        class Reader:
            class Poll(contracts.ReaderPoll):
                def run(self, *, services, service):
                    reader_calls.append(services.name)
                    return contracts.ReaderPollResult()
        trigger_registry['procedures']['TestLibrary.Reader:poll'] = Reader.Poll

        resource_types = {'TestLibrary.ProbeState': ProbeState, 'TestLibrary.Service': Provider}
        bindings = test_extension(**trigger_registry, contracts=contracts)
        resource_bindings = test_extension(resources=resource_types, contracts=contracts)
        trigger_engine = Engine(trigger_program, override_extensions=[bindings, resource_bindings])
    first = trigger_engine.get_native_state('first')
    second = trigger_engine.get_native_state('second')
    shared = trigger_engine.get_native_state('shared')
    assert first.services.name == 'first'
    assert second.services.name == 'second'
    assert shared.services.name == 'TestLibrary.SharedTriggerProbe'
    assert shared is trigger_engine.get_native_state('shared_again')
    assert not first.calls and not second.calls and not shared.calls
    trigger_engine.step()
    for state in [first, second, shared]:
        checks.assertCountEqual(state.calls, ['refresh', 'run', 'log'])
    first_startup = first.calls.copy()
    second_startup = second.calls.copy()
    shared_startup = shared.calls.copy()
    reader_calls.clear()
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    with checks.assertRaisesRegex(ValueError, "Unknown output 'missing' on first"):
        first.services.trigger('missing')
    trigger_engine.step()
    assert first.calls == first_startup

    # A trigger reruns the procedures depending on the output, once per step.
    first.services.trigger('refresher')
    first.services.trigger('refresher')
    assert first.calls == first_startup
    trigger_engine.step()
    assert first.calls == first_startup + ['refresh']
    assert second.calls == second_startup
    assert shared.calls == shared_startup
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    first.reschedule_once = True
    first.services.trigger('refresher')
    trigger_engine.step()
    assert first.calls == first_startup + ['refresh', 'refresh']
    trigger_engine.step()
    assert first.calls == first_startup + ['refresh', 'refresh', 'refresh']
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    second.services.trigger('runner')
    shared.services.trigger('refresher')
    trigger_engine.step()
    checks.assertCountEqual(second.calls, second_startup + ['run', 'log'])
    assert shared.calls == shared_startup + ['refresh']
    assert len(first.calls) == 6
    trigger_engine.step()
    trigger_engine.assert_is_idle()

    # Dependents in other nodes rerun too.
    trigger_engine.get_native_state('provider').services.trigger('service')
    trigger_engine.step()
    checks.assertCountEqual(reader_calls, ['reader', 'another_reader'])
    trigger_engine.assert_is_idle()


def check_typed_ports():
    # Typed ports receive their sources through connections.
    snapshots = []

    def check_ports(services, inputs, values):
        events = inputs['values'], inputs['other']
        snapshots.append(events)
        total = inputs['level'].value + sum(event.value.value for event in events if event is not None)
        return {'total': wirely.UInt8(total)}

    probe = make_engine('''
    default output: value uint8;
    node first: Std.DebugEventSource { Type: uint8 }
    node second: Std.DebugEventSource { Type: uint8 }
    node level: Std.Constant { Type: uint8, Value: 7 }

    native procedure run
      depends on values: event uint8, other: event uint8, level: value uint8
      produces total: value uint8;
    connect first -> run.values;
    connect second -> run.other;
    connect level -> run.level;
    connect run.total -> default output;
    ''', check_ports)
    probe.step()
    assert snapshots[-1] == (None, None)
    probe.assert_value_is('probe', 7)
    probe.get_native_state('probe.first').send_event(wirely.Some(1))
    probe.get_native_state('probe.second').send_event(wirely.Some(3))
    probe.step()
    assert snapshots[-1] == (wirely.Some(wirely.UInt8(1)), wirely.Some(wirely.UInt8(3)))
    probe.assert_value_is('probe', 11)


def check_outputs():
    # Outputs need the native representation of their type.
    declaration = '''
    default output: value uint8;

    native procedure run produces default output as "output";
    '''
    for invalid in ('invalid', 7, True, wirely.UInt16(7), wirely.Int8(7)):
        expect_output_error(declaration, invalid, "Native procedure 'TestLibrary.Probe:run' of probe: "
                                                  "invalid value for output 'output': Expected a wirely.UInt8")
    probe = make_engine(declaration, lambda services, inputs, values: {'output': wirely.UInt8(7)})
    probe.step()
    probe.assert_value_is('probe', 7)

    # Implementations return their contract's result class.
    for result in (None, {'output': wirely.UInt8(7)}):
        probe = make_engine(declaration, lambda services, inputs, values, result=result: Raw(result))
        with checks.assertRaisesRegex(ValueError, re.escape(f'of probe must return a ProbeRunResult, got {result!r}')):
            probe.step()

    # Generic outputs are DynamicValues of their bound type.
    generic = '''
    parameter Type: ValueType;
    default output: value Type;

    native procedure run produces default output as "output" uses Type;
    '''
    option = wirely.value_types.option(wirely.value_types.boolean)
    for invalid, message in [(wirely.Some(True), 'Expected a DynamicValue of Option\\[boolean\\], got Some'),
                             (wirely.value_types.boolean.from_native(True, native_types=builtin_extension.native_types),
                              'Expected a DynamicValue of Option\\[boolean\\], got one of boolean')]:
        expect_output_error(generic, invalid, message, parameters='Type: Option[boolean]')

    def generic_output(services, inputs, values):
        assert values == {'type': option}
        return {'output': values['type'].from_native(wirely.Some(True), native_types=builtin_extension.native_types)}

    probe = make_engine(generic, generic_output, parameters='Type: Option[boolean]')
    probe.step()
    probe.assert_value_is('probe', wirely.Some(True))


def check_events():
    # Events use an outer option even when the payload is itself an option.
    for payload_type, event, command in [
        ('unit', wirely.Some(wirely.Unit()), '()'),
        ('Option[boolean]', wirely.Some(None), 'none'),
        ('Option[boolean]', wirely.Some(wirely.Some(True)), 'true'),
    ]:
        seen_events = []

        def forward_event(services, inputs, values):
            seen_events.append(inputs['event'])
            return {'output': inputs['event']}

        probe = make_engine(f'''
    default input: event {payload_type};
    default output: event {payload_type};

    native procedure run
      depends on default input as "event"
      produces default output as "output";
    ''', forward_event, extra=f'''
    node source: Std.DebugEventSource {{ Type: {payload_type} }}
    connect source -> probe;
    ''')
        probe.step()
        probe.assert_value_is('probe', None)
        source = probe.get_native_state('source')
        for send in [lambda: source.send_event(event),
                     lambda: source.debug_command(['send', command])]:
            send()
            probe.step()
            probe.assert_value_is('probe', event)
            probe.assert_value_is('source', event)
            assert seen_events[-1] == event
            probe.step()
            probe.assert_value_is('probe', None)
            probe.assert_value_is('source', None)
            assert seen_events[-1] is None
            probe.assert_is_idle()


def check_unit_outputs():
    value_declaration = '''
    default output: value unit;

    native procedure run produces default output as "output";
    '''
    expect_output_error(value_declaration, None, 'invalid value for output')
    probe = make_engine(value_declaration, lambda services, inputs, values: {'output': wirely.Unit()})
    probe.step()
    probe.assert_value_is('probe', wirely.Unit())

    event_declaration = '''
    default output: event unit;

    native procedure run produces default output as "output";
    '''
    for invalid in [wirely.Some(None), wirely.Unit()]:
        expect_output_error(event_declaration, invalid, 'invalid value for output')
    probe = make_engine(event_declaration, lambda services, inputs, values: {'output': wirely.Some(wirely.Unit())})
    probe.step()
    probe.assert_value_is('probe', wirely.Some(wirely.Unit()))


def check_enum_outputs():
    # Enum outputs accept only members of the Python enum of the output's enum type.
    output = 'Off'

    class Mode(OpenIntEnum, minimum=0, maximum=255):
        Off = 1
        On = 2

    class Other(OpenIntEnum, minimum=0, maximum=255):
        Off = 1
        On = 2

    class State(enum.IntEnum):
        Idle = 0
        Running = 1
        Failed = 2

    registry = {'Test.Mode': NativeEnum(Mode, is_open=True, underlying_type='uint8'),
                'Test.Other': NativeEnum(Other, is_open=True, underlying_type='uint8'),
                'Test.State': NativeEnum(State, is_open=False, underlying_type='uint8')}
    declaration = '''
    default output: value Example.Mode;

    native procedure run produces default output as "output"
      uses Example.Mode.On as mode, Example.Other.On as other, Example.State.Running as state;
    '''
    extra = '''
namespace Example {
  native open enum type Mode as "Test.Mode": uint8 { Off = 1, On = 2, }
  native open enum type Other as "Test.Other": uint8 { Off = 1, On = 2, }
  native closed enum type State as "Test.State" { Idle, Running, Failed, }
}
'''

    def check_enum_output(services, inputs, values):
        assert values['mode'] is Mode.On and values['other'] is Other.On
        # Closed members are numbered in declaration order.
        assert values['state'] is State.Running
        return {'output': Mode[output] if isinstance(output, str) else Mode(output)}

    enum_bindings = RuntimeNativeExtension(enums=registry)
    for invalid in [2, Other.On]:
        expect_output_error(declaration, invalid, 'Expected a Mode for Example.Mode', extra=extra, override_extensions=[enum_bindings])
    probe = make_engine(declaration, check_enum_output, extra=extra, override_extensions=[enum_bindings])
    probe.step()
    # Inside the runtime, named and unnamed enum values are numbers.
    probe.assert_value_is('probe', 1)
    output = 255
    probe.trigger('probe', 'run')
    probe.step()
    probe.assert_value_is('probe', 255)


def check_native_enums():
    # Native enums link by native name, kind, underlying type, and closed
    # member count; member names and numbers are not compared.
    class Level(OpenIntEnum, minimum=0, maximum=255):
        Minimum = 0
        Maximum = 2

    class Day(enum.IntEnum):
        First = 0
        Second = 1

    class Week(enum.IntEnum):
        First = 0
        Second = 1
        Third = 2

    runs = []

    def check_values(services, inputs, values):
        runs.append(None)
        assert values['level'] is Level.Maximum
        assert values['day'] is Day.Second

    def make(registry):
        enum_bindings = RuntimeNativeExtension(enums=registry)
        probe = make_engine('''
    native procedure run uses Example.Level.High as level, Example.Day.Tuesday as day;
    ''', check_values, extra='''
namespace Example {
  native open enum type Level as "Test.Level": uint8 { Off = 0, High = 2, }
  native closed enum type Day as "Test.Day" { Monday, Tuesday, }
  native open enum type Unused as "Test.Unused": uint8 {}
}
''', override_extensions=[enum_bindings])
        probe.step()

    level = NativeEnum(Level, is_open=True, underlying_type='uint8')
    day = NativeEnum(Day, is_open=False, underlying_type='uint8')
    # Unused native enums need no runtime entry.
    make({'Test.Level': level, 'Test.Day': day})
    assert len(runs) == 1
    for registry, message in [
        ({'Test.Day': day}, "Cannot find native enum type 'Test.Level'"),
        ({'Test.Level': NativeEnum(Level, is_open=False, underlying_type='uint8'), 'Test.Day': day},
         "Native enum type 'Test.Level' must be closed, got open"),
        ({'Test.Level': NativeEnum(Level, is_open=True, underlying_type='int8'), 'Test.Day': day},
         "Native enum type 'Test.Level' must have underlying type int8, got uint8"),
        ({'Test.Level': level, 'Test.Day': NativeEnum(Day, is_open=False, underlying_type='uint16')},
         "Native enum type 'Test.Day' must have underlying type uint16, got uint8"),
        ({'Test.Level': level, 'Test.Day': NativeEnum(Week, is_open=False, underlying_type='uint8')},
         "Native enum type 'Test.Day' must have 3 members, got 2"),
    ]:
        with checks.assertRaisesRegex(ValueError, message):
            make(registry)


def check_native_interface_signatures():
    # Generated signatures describe the standard library's native interfaces;
    # types passed to native code as ValueType through an entry's own uses
    # become type variables.
    from wirely.generated import _signatures as native_interfaces
    from wirely.runtime.signatures import (
        Builtin, Enum, Function, Initializer, Int, Option, Port, Procedure, ResourceType, Struct, TypeVar,
    )

    assert native_interfaces.native_state_types['MQTT.EventSender'] == Initializer(
        depends={'Broker': Port('resource', ResourceType('MQTT.Broker'))},
        produces={'state': Port('resource', ResourceType('MQTT.EventSenderState'))},
        uses={'Type': Builtin('ValueType'), 'Topic': Builtin('String'), 'Format': Option(Enum('MQTT.Format')),
              'template_parts': Struct('MQTT.TemplateParts')},
    )
    assert native_interfaces.native_procedures['MQTT.EventSender:send_value'] == Procedure(
        stage='output',
        depends={'state': Port('resource', ResourceType('MQTT.EventSenderState')),
                 'input': Port('event', TypeVar('Type'))},
        produces={}, uses={'Type': Builtin('ValueType')},
    )
    assert native_interfaces.native_procedures['Std.DebugValuePrinter:run'] == Procedure(
        stage='output', depends={'input': Port('value', TypeVar('Type'))}, produces={},
        uses={'Type': Builtin('ValueType')},
    )
    assert native_interfaces.native_procedures['Timer.MonotonicTimestamp:run'] == Procedure(
        stage='default', depends={'input': Port('event', TypeVar('Type'))},
        produces={'output': Port('event', Builtin('Timer.MonotonicTime'))}, uses={'Type': Builtin('ValueType')},
    )
    assert native_interfaces.native_functions['Std.Duration.CheckedMul'] == Function(
        (Builtin('Std.Duration'), Int(32, False)), Option(Builtin('Std.Duration')))
    # Constant functions run in the compiler, not the runtime.
    assert 'Std.ParseFormat' not in native_interfaces.native_functions


def check_native_signature_linking():
    # Linking checks the native interfaces a program uses against the
    # runtime's signatures, with type variables bound by each entry's uses.
    from dataclasses import replace
    from wirely.compiler.native_signatures import native_signatures
    from wirely.generated import _signatures as native_interfaces
    from wirely.runtime.signatures import Builtin, Function, Int, Option, Port, ResourceType, TypeVar

    source = '''
namespace TestLibrary {
  resource type ProbeState;
  native function Clamp(value: int16, low: int16, high: int16): int16;

  node type Probe {
    parameter Type: ValueType;
    input In: value Type;
    output Out: event uint8;
    native procedure run stage output
      depends on In as "input" produces Out as "output" uses Type;
  }

  native node type Stateful {
    input Number: value int16;
    output Clamped: value int16;
    #[Allow(Unused)] binding state: resource ProbeState;
    native initializer produces state as "state";
    procedure clamp depends on Number produces Clamped { Clamped = TestLibrary.Clamp(Number, 0, 100); }
  }
}
node value: Std.Constant { Type: uint16, Value: 1 }
node probe: TestLibrary.Probe { Type: uint16 }
connect value -> probe.In;
node number: Std.Constant { Type: int16, Value: 500 }
node stateful: TestLibrary.Stateful {}
connect number -> stateful.Number;
'''
    program = compile_program([('wirely/lib/std.wrl', std_source), ('<signatures>', source)])
    with test_contracts(program.graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class Probe:
            class Run(contracts.ProbeRun):
                def prepare(self, *, services, type):
                    pass

                def run(self, *, services, input):
                    return contracts.ProbeRunResult(output=None)
        registry['procedures']['TestLibrary.Probe:run'] = Probe.Run

        class Stateful(contracts.StatefulInitializer):
            def initialize(self, *, services):
                return contracts.StatefulInitializerResult(state=self)
        registry['initializers']['TestLibrary.Stateful'] = Stateful

        state_types, procedures, functions = native_signatures(program.graph, 'TestLibrary')
        run = procedures['TestLibrary.Probe:run']
        stateful = state_types['TestLibrary.Stateful']
        clamp = functions['TestLibrary.Clamp']

        def link(state_types=state_types, procedures=procedures, functions=functions):
            from wirely.runtime.extensions import NativeInitializer, NativeProcedure, NativeFunction
            from tests.infra.native_extension.generated import extension as fixture
            bindings = test_extension(**registry, contracts=contracts,
                                      resources={'TestLibrary.ProbeState': Stateful})
            extension = RuntimeNativeExtension(
                states={name: NativeInitializer(sig, bindings.states[name].load) for name, sig in state_types.items()},
                procedures={name: NativeProcedure(sig, bindings.procedures[name].load)
                            for name, sig in procedures.items()},
                functions={name: NativeFunction(sig, fixture.functions[name].load) for name, sig in functions.items()},
                resources=bindings.resources)
            Engine(program, extensions=[extension])

        link()
        for changes, message in [
            (dict(procedures={}), "Cannot find the signature of native procedure 'TestLibrary.Probe:run'"),
            (dict(state_types={}), "Cannot find the signature of native state type 'TestLibrary.Stateful'"),
            (dict(functions={}), "Cannot find the signature of native function 'TestLibrary.Clamp'"),
            ({'procedures': {'TestLibrary.Probe:run': replace(run, stage='input')}},
             'runs in stage output, expected input'),
            ({'procedures': {'TestLibrary.Probe:run': replace(run, uses={'Type': Builtin('String')})}},
             "uses binding 'Type' has type ValueType, expected String"),
            ({'procedures': {'TestLibrary.Probe:run': replace(
                run, depends={'input': Port('value', Int(8, False))})}},
             "'input' has type uint16, expected uint8"),
            # Type variables are bound to the entry's own ValueType bindings.
            ({'procedures': {'TestLibrary.Probe:run': replace(
                run, depends={'input': Port('value', Option(TypeVar('Type')))})}},
             "'input' has type uint16, expected Option\\[uint16\\]"),
            # Programs store an event of T as Option[T].
            ({'procedures': {'TestLibrary.Probe:run': replace(
                run, produces={'output': Port('value', Int(8, False))})}},
             "'output' has type Option\\[uint8\\], expected uint8"),
            ({'procedures': {'TestLibrary.Probe:run': replace(
                run, depends={**run.depends, 'extra': Port('value', Builtin('boolean'))})}},
             "inputs \\['input'\\] do not match the expected \\['extra', 'input'\\]"),
            ({'state_types': {'TestLibrary.Stateful': replace(
                stateful, produces={'state': Port('resource', ResourceType('TestLibrary.Other'))})}},
             "'state' is TestLibrary.ProbeState, expected resource TestLibrary.Other"),
            ({'functions': {'TestLibrary.Clamp': Function(clamp.parameters[:2], clamp.result)}},
             'has 3 parameters, expected 2'),
            ({'functions': {'TestLibrary.Clamp': Function((Int(16, False),) * 3, clamp.result)}},
             'parameter 0 has type int16, expected uint16'),
        ]:
            with checks.assertRaisesRegex(ValueError, message):
                link(**changes)


def check_struct_outputs():
    # Struct outputs accept only instances of the Python type of the output's struct type.
    class Pair(NamedTuple):
        enabled: bool
        number: wirely.UInt8

    class Other(NamedTuple):
        enabled: bool
        number: wirely.UInt8

    fields = (Builtin('boolean'), Int(8, False))
    registry = {'Test.Pair': NativeStruct(Pair, fields), 'Test.Other': NativeStruct(Other, fields)}
    declaration = '''
    default output: value Example.Pair;

    native procedure run produces default output as "output"
      uses Example.Pair { enabled: true, number: 7 } as pair,
        Example.Other { enabled: true, number: 7 } as other;
    '''
    extra = '''
namespace Example {
  native struct type Pair as "Test.Pair" { enabled: boolean, number: uint8 }
  native struct type Other as "Test.Other" { enabled: boolean, number: uint8 }
}
'''

    def check_struct_output(services, inputs, values):
        assert values == {'pair': Pair(True, wirely.UInt8(7)), 'other': Other(True, wirely.UInt8(7))}
        assert type(values['pair']) is Pair
        return {'output': Pair(False, wirely.UInt8(8))}

    struct_bindings = RuntimeNativeExtension(structs=registry)
    for invalid in [(True, wirely.UInt8(7)), Other(True, wirely.UInt8(7))]:
        expect_output_error(declaration, invalid, 'Expected a Pair for Example.Pair', extra=extra, override_extensions=[struct_bindings])
    expect_output_error(declaration, Pair(True, 7), "in field 'number': Expected a wirely.UInt8", extra=extra, override_extensions=[struct_bindings])
    probe = make_engine(declaration, check_struct_output, extra=extra, override_extensions=[struct_bindings])
    probe.step()
    # Inside the runtime, a struct is a tuple of its field values.
    probe.assert_value_is('probe', (False, 8))


def check_native_structs():
    # Native structs link by native name and field types in order; field
    # names are not compared, and nested enums and structs match by native name.
    class Level(OpenIntEnum, minimum=0, maximum=255):
        High = 2

    class Reading(NamedTuple):
        first: wirely.UInt16
        second: Level

    class Empty(NamedTuple):
        pass

    def check_values(services, inputs, values):
        reading = values['reading']
        assert type(reading) is Reading and reading.first == wirely.UInt16(5) and reading.second is Level.High
        assert values['empty'] == Empty()

    def make(registry):
        enums = {'Test.Level': NativeEnum(Level, is_open=True, underlying_type='uint8')}
        enum_bindings = RuntimeNativeExtension(enums=enums)
        struct_bindings = RuntimeNativeExtension(structs=registry)
        probe = make_engine('''
    native procedure run uses Example.Reading { value: 5, level: Example.Level.High } as reading,
      Example.Empty {} as empty;
    ''', check_values, extra='''
namespace Example {
  native open enum type Level as "Test.Level": uint8 { High = 2, }
  native struct type Reading as "Test.Reading" { value: uint16, level: Example.Level }
  native struct type Empty as "Test.Empty" {}
}
''', override_extensions=[enum_bindings, struct_bindings])
        probe.step()

    reading = NativeStruct(Reading, (Int(16, False), Enum('Test.Level')))
    empty = NativeStruct(Empty, ())
    make({'Test.Reading': reading, 'Test.Empty': empty})
    for registry, message in [
        ({'Test.Empty': empty}, "Cannot find native struct type 'Test.Reading'"),
        ({'Test.Reading': NativeStruct(Reading, (Int(16, False),)), 'Test.Empty': empty},
         "Native struct type 'Test.Reading' must have 1 fields, got 2"),
        ({'Test.Reading': NativeStruct(Reading, (Int(8, False), Enum('Test.Level'))), 'Test.Empty': empty},
         "Native struct type 'Test.Reading' field 0 has type uint16, expected uint8"),
        ({'Test.Reading': NativeStruct(Reading, (Int(16, False), Enum('Test.Other'))), 'Test.Empty': empty},
         "Native struct type 'Test.Reading' field 1 has type Example.Level, expected native enum Test.Other"),
    ]:
        with checks.assertRaisesRegex(ValueError, message):
            make(registry)


def check_port_keys():
    # Keys that are not identifiers become Python names; "" becomes _.
    def check_keys(services, inputs, values):
        assert inputs == {'line_key': wirely.UInt8(7), 'raw_key': wirely.UInt8(7)}
        return {'_': wirely.UInt8(7)}

    probe = make_engine(r'''
    node source: Std.Constant { Type: uint8, Value: 7 }
    default output: value uint8;

    native procedure run
      depends on source as "line\nkey", source as r"raw\key"
      produces default output as "";
    ''', check_keys)
    probe.step()
    probe.assert_value_is('probe', 7)

    # Aliases are checked independently for input and output ports.
    probe = make_engine('''
    node source: Std.Constant { Type: uint8, Value: 7 }
    default output: value uint8;

    native procedure run
      depends on source as "value"
      produces default output as "value";
    ''', lambda services, inputs, values: {'value': inputs['value']})
    probe.step()
    probe.assert_value_is('probe', 7)

    # Typed port keys replace port names only in Python.
    probe = make_engine('''
    node source: Std.Constant { Type: uint8, Value: 7 }
    default output: value uint8;

    native procedure run
      depends on level as "value": value uint8
      produces result as "output": value uint8;
    connect source -> run.level;
    connect run.result -> default output;
    ''', lambda services, inputs, values: {'output': inputs['value']})
    probe.step()
    probe.assert_value_is('probe', 7)


startup_observations = []
cleanup_observations = []
cleanup_clock_observations = []


def make_lifecycle_engine(*, fail=False):
    program = compile_program([('<native-lifecycle>', f'''
namespace TestLibrary {{
  native node type LifecycleProbe {{
    parameter Fail: boolean = false;
    native initializer uses Fail;
  }}
}}
node first: TestLibrary.LifecycleProbe {{}}
node second: TestLibrary.LifecycleProbe {{ Fail: {'true' if fail else 'false'} }}
''')])
    with test_contracts(program.graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class LifecycleProbe(contracts.LifecycleProbeInitializer):
            def initialize(self, *, services, fail):
                self.fail = fail
                return contracts.LifecycleProbeInitializerResult()

            def start_tasks(self, task_group, async_exit_stack):
                async_exit_stack.callback(self.observe_cleanup)
                startup_observations.append(self.services.name)

            async def start_tasks_async(self, task_group, async_exit_stack):
                if self.fail:
                    raise RuntimeError('Startup failed')

            def observe_cleanup(self):
                cleanup_observations.append(self.services.name)
                cleanup_clock_observations.append((self.services.is_shutting_down, self.services.shutdown_time))
        registry['initializers']['TestLibrary.LifecycleProbe'] = LifecycleProbe

        bindings = test_extension(**registry, contracts=contracts)
        return Engine(program, override_extensions=[bindings])


async def run_test():
    startup_observations.clear()
    cleanup_observations.clear()
    cleanup_clock_observations.clear()
    lifecycle_engine = make_lifecycle_engine()
    assert not lifecycle_engine.is_shutting_down
    async with asyncio.TaskGroup() as task_group:
        async with contextlib.AsyncExitStack() as exit_stack:
            await lifecycle_engine.start_tasks_async(task_group, exit_stack)
    timestamp = lifecycle_engine.last_shutdown_time
    assert timestamp is not None and timestamp.tzinfo is not None
    assert cleanup_clock_observations == [(True, timestamp)] * len(cleanup_observations)
    assert not lifecycle_engine.is_shutting_down
    assert lifecycle_engine.shutdown_time is None
    checks.assertCountEqual(startup_observations, ['first', 'second'])
    assert cleanup_observations == startup_observations[::-1]

    startup_observations.clear()
    cleanup_observations.clear()
    cleanup_clock_observations.clear()
    unknown_time_engine = make_lifecycle_engine()
    unknown_time_engine.set_fake_wall_time(None)
    async with asyncio.TaskGroup() as task_group:
        async with contextlib.AsyncExitStack() as exit_stack:
            await unknown_time_engine.start_tasks_async(task_group, exit_stack)
            assert not unknown_time_engine.is_shutting_down
    assert cleanup_clock_observations == [(True, None)] * len(cleanup_observations)
    assert len(cleanup_observations) == 2
    assert not unknown_time_engine.is_shutting_down

    startup_observations.clear()
    cleanup_observations.clear()
    cleanup_clock_observations.clear()
    failing_engine = make_lifecycle_engine(fail=True)
    async with asyncio.TaskGroup() as task_group:
        with checks.assertRaisesRegex(RuntimeError, 'Startup failed'):
            async with contextlib.AsyncExitStack() as exit_stack:
                await failing_engine.start_tasks_async(task_group, exit_stack)
    assert startup_observations in (['second'], ['first', 'second'])
    assert cleanup_observations == startup_observations[::-1]
    assert failing_engine.last_shutdown_time is None
    assert cleanup_clock_observations == [(False, None)] * len(cleanup_observations)
    assert not failing_engine.is_shutting_down


def check_native_methods():
    # Native procedures are nested classes of the class their name selects or of its bases.
    source = '''namespace TestLibrary {
  resource type ProbeState;
  node type ClassProbe {
    native procedure shared;
    native procedure inherited;
  }
  node type OverrideProbe {
    native procedure shared;
    native procedure inherited;
  }
  native node type StateProbe {
    binding state: resource ProbeState;
    native initializer produces state as "state";
    native procedure inherited depends on state as "state";
  }
  // Procedures may use another type's implementation.
  node type KeyedProbe {
    native procedure shared as "TestLibrary.ClassProbe:shared";
  }
}
node class_only: TestLibrary.ClassProbe {}
node overridden: TestLibrary.OverrideProbe {}
node stateful: TestLibrary.StateProbe {}
node keyed: TestLibrary.KeyedProbe {}
'''
    program = compile_program([('<native-methods>', source)])
    calls = []
    with test_contracts(program.graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class Methods:
            class Shared(contracts.ClassProbeShared):
                def run(self, *, services):
                    calls.append(('shared', services.name))
                    return contracts.ClassProbeSharedResult()

            # One class can implement several contracts with compatible methods.
            class Inherited(contracts.ClassProbeInherited, contracts.OverrideProbeInherited,
                            contracts.StateProbeInherited):
                def prepare(self, *, services, **resources):
                    pass

                def run(self, *, services, **resources):
                    calls.append(('inherited', services.name))
                    result_types = {'class_only': contracts.ClassProbeInheritedResult,
                                    'overridden': contracts.OverrideProbeInheritedResult,
                                    'stateful': contracts.StateProbeInheritedResult}
                    return result_types[services.name]()

        class ClassProbe(Methods):
            pass

        class OverrideProbe(ClassProbe):
            class Shared(contracts.OverrideProbeShared):
                def run(self, *, services):
                    calls.append(('override', services.name))
                    return contracts.OverrideProbeSharedResult()
        registry['procedures']['TestLibrary.OverrideProbe:shared'] = OverrideProbe.Shared

        class StateProbe(Methods, contracts.StateProbeInitializer):
            def initialize(self, *, services):
                return contracts.StateProbeInitializerResult(state=self)
        registry['initializers']['TestLibrary.StateProbe'] = StateProbe
        registry['procedures'].update({
            'TestLibrary.ClassProbe:shared': ClassProbe.Shared,
            'TestLibrary.ClassProbe:inherited': ClassProbe.Inherited,
            'TestLibrary.OverrideProbe:inherited': OverrideProbe.Inherited,
            'TestLibrary.StateProbe:inherited': StateProbe.Inherited,
        })

        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources={'TestLibrary.ProbeState': StateProbe}, contracts=contracts)
        runtime = Engine(program, override_extensions=[bindings, resource_bindings])
    runtime.step()
    expected = [
        ('shared', 'class_only'), ('inherited', 'class_only'),
        ('override', 'overridden'), ('inherited', 'overridden'),
        ('inherited', 'stateful'), ('shared', 'keyed'),
    ]
    assert sorted(calls) == sorted(expected)
    runtime.assert_is_idle()


def check_procedure_construction():
    # The engine prepares each native procedure once per node instance, after all native states.
    events = []

    def make(source):
        program = compile_program([('<procedure-construction>', source)])
        with test_contracts(program.graph) as contracts:
            registry = {'initializers': {}, 'procedures': {}}

            class LoadProbe(contracts.LoadProbeInitializer):
                def initialize(self, *, services):
                    events.append(('state', services.name))
                    return contracts.LoadProbeInitializerResult(state=self)

                class Run(contracts.LoadProbeRun):
                    def prepare(self, *, services, state, fail):
                        self.state = state
                        assert isinstance(self.state, LoadProbe)
                        if fail:
                            raise ValueError(f'{services.name}: unsupported configuration')
                        events.append(('procedure', services.name, fail))

                    def run(self, *, services, state):
                        assert state is self.state
                        events.append(('call', services.name))
                        return contracts.LoadProbeRunResult()
            registry['initializers']['TestLibrary.LoadProbe'] = LoadProbe
            registry['procedures']['TestLibrary.LoadProbe:run'] = LoadProbe.Run

            bindings = test_extension(**registry, contracts=contracts)
            resource_bindings = test_extension(resources={'TestLibrary.LoadState': LoadProbe}, contracts=contracts)
            return Engine(program, override_extensions=[bindings, resource_bindings])

    source = '''namespace TestLibrary {
  resource type LoadState;
  native node type LoadProbe {
    parameter Fail: boolean = false;
    binding state: resource LoadState;
    native initializer produces state as "state";
    native procedure run depends on state as "state" uses Fail;
  }
}
node first: TestLibrary.LoadProbe {}
node second: TestLibrary.LoadProbe {}
'''
    runtime = make(source)
    checks.assertCountEqual(events[:2], [('state', 'first'), ('state', 'second')])
    checks.assertCountEqual(events[2:], [('procedure', 'first', False), ('procedure', 'second', False)])
    events.clear()
    runtime.step()
    checks.assertCountEqual(events, [('call', 'first'), ('call', 'second')])

    events.clear()
    failing = source.replace('node second: TestLibrary.LoadProbe {}',
                             'node second: TestLibrary.LoadProbe { Fail: true }')
    with checks.assertRaisesRegex(ValueError, 'second: unsupported configuration'):
        make(failing)
    checks.assertNotIn(('call', 'first'), events)


def check_native_linking_errors():
    # Missing bindings must fail before any initializer runs.
    constructed = []
    source = """namespace TestLibrary {
  native node type ValidProbe {
    native initializer;
    native procedure run;
  }
  node type InvalidProbe {
    native procedure missing;
  }
}
node valid: TestLibrary.ValidProbe {}
node invalid: TestLibrary.InvalidProbe {}
"""
    program = compile_program([('<invalid-native-implementation>', source)])
    with test_contracts(program.graph) as contracts:
        class ValidProbe(contracts.ValidProbeInitializer):
            def initialize(self, *, services):
                constructed.append(services.name)
                return contracts.ValidProbeInitializerResult()

            class Run(contracts.ValidProbeRun):
                def run(self, *, services):
                    return contracts.ValidProbeRunResult()

        bindings = test_extension(initializers={'TestLibrary.ValidProbe': ValidProbe},
                                          procedures={'TestLibrary.ValidProbe:run': ValidProbe.Run}, contracts=contracts)
        with checks.assertRaisesRegex(ValueError, 'Cannot find the signature of native procedure.*InvalidProbe:missing'):
            Engine(program, override_extensions=[bindings])
    assert constructed == []


def check_input_length_values():
    source = '''
namespace TestLibrary {
  resource type LengthState;
  native node type LengthProbe {
    default input: multi value boolean;
    default output: value uint32;
    binding state: resource LengthState;
    native initializer produces state as "state" uses (default input).Length as Count;
    native procedure run depends on state as "state" produces default output as output
      uses (default input).Length as Count;
  }
}
node source: Std.DebugValueSource { Type: boolean }
node empty: TestLibrary.LengthProbe {}
node multiple: TestLibrary.LengthProbe {}
connect source, source -> multiple;
'''
    program = compile_program([('wirely/lib/std.wrl', std_source), ('<input-length-values>', source)])
    with test_contracts(program.graph) as contracts:
        registry = {'initializers': {}, 'procedures': {}}

        class LengthProbe(contracts.LengthProbeInitializer):
            def initialize(self, *, services, count):
                self.count = count
                return contracts.LengthProbeInitializerResult(state=self)

            class Run(contracts.LengthProbeRun):
                def prepare(self, *, services, state, count):
                    assert count == state.count

                def run(self, *, services, state):
                    return contracts.LengthProbeRunResult(output=state.count)
        registry['initializers']['TestLibrary.LengthProbe'] = LengthProbe
        registry['procedures']['TestLibrary.LengthProbe:run'] = LengthProbe.Run

        bindings = test_extension(**registry, contracts=contracts)
        resource_bindings = test_extension(resources={'TestLibrary.LengthState': LengthProbe}, contracts=contracts)
        machine = Engine(program, override_extensions=[bindings, resource_bindings])
    for value in (False, True):
        machine.get_native_state('source').set_value(value)
        machine.step()
        machine.assert_value_is('empty', 0)
        machine.assert_value_is('multiple', 2)
        machine.assert_is_idle()


check_input_length_values()
check_native_linking_errors()
check_input_ports()
check_wakeups()
check_context_names()
check_stage_times()
check_triggers()
check_typed_ports()
check_outputs()
check_events()
check_unit_outputs()
check_enum_outputs()
check_native_enums()
check_native_interface_signatures()
check_native_signature_linking()
check_struct_outputs()
check_native_structs()
check_port_keys()
check_native_methods()
check_procedure_construction()
