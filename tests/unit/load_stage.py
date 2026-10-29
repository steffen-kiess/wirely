"""Load execution, fixed stores, native preparation, and resource identity."""

import asyncio
import contextlib
from dataclasses import replace
from unittest import TestCase

import wirely.compiler.compilation
from wirely.compiler import program as program_compiler
from wirely.program.program import InputSource, RunStage
from wirely.program.validation import validate_program
from wirely.runtime.engine import Engine
from wirely.runtime.loading import LoadedProgram
from tests.infra.support import test_contracts, test_extension


checks = TestCase()
source = '''constant Duration: Std.Duration = "PT1S";
namespace TestLibrary {
  resource type Handle;

  node type Provider {
    input InputReady: fixed boolean;
    output First: fixed Handle;
    output Second: fixed Handle;
    output Ready: fixed boolean;
    native initializer first writes First as "handle";
    native initializer second reads First as "first", InputReady as "ready"
      writes Second as "handle", Ready as "ready";
  }

  node type Choose {
    parameter Type: type;
    parameter First: boolean;
    input Left: fixed Type;
    input Right: fixed Type;
    default output: fixed Type;
    native procedure choose stage load
      reads Type as "type", First as "first", Left as "left", Right as "right"
      writes default output as "output";
  }

  node type Plain {
    parameter Flag: boolean;
    binding inverted: fixed boolean;
    default output: fixed boolean;
    procedure invert stage load writes inverted { inverted = !Flag; }
    procedure forward stage load reads inverted
      writes default output as output { output = inverted; }
  }

  node type Probe {
    input Handle: fixed Handle;
    input Flag: fixed boolean;
    default output: value boolean;
    native procedure run reads Handle as "handle", Flag as "flag"
      writes default output as "output";
  }

  node type Forward {
    default input: value boolean;
    default output: value boolean;
    procedure run reads default input as input
      writes default output as output { output = input; }
  }
}
node provider: TestLibrary.Provider {}
node plain: TestLibrary.Plain { Flag: false }
node choose_handle: TestLibrary.Choose { Type: TestLibrary.Handle, First: true }
connect provider.First -> choose_handle.Left;
connect provider.Second -> choose_handle.Right;
node choose_plain: TestLibrary.Choose { Type: boolean, First: false }
connect plain -> choose_plain.Left;
connect plain -> choose_plain.Right;
node probe: TestLibrary.Probe {}
connect choose_handle -> probe.Handle;
connect choose_plain -> probe.Flag;
node forward: TestLibrary.Forward {}
connect plain -> forward;
connect plain -> provider.InputReady;
node choose_duration: TestLibrary.Choose { Type: Std.Duration, First: true }
connect Duration -> choose_duration.Left;
connect Duration -> choose_duration.Right;
'''
graph = wirely.compiler.compilation.compile_strs([('<load-stage>', source)], add_std=True)
assert graph is not None
program = program_compiler.compile_nocatch(graph)
calls = []
lifecycle = []


class Handle:
    pass


with test_contracts(graph) as contracts:
    class ProviderFirst(contracts.ProviderFirst, Handle):
        def initialize(self, *, services):
            calls.append(('first', services.name))
            return contracts.ProviderFirstResult(handle=self)

        def start_tasks(self, task_group, async_exit_stack):
            lifecycle.append('first')
            async_exit_stack.callback(lifecycle.append, 'stop first')

    class ProviderSecond(contracts.ProviderSecond, Handle):
        def initialize(self, *, services, first, ready):
            calls.append(('second', first))
            self.first = first
            assert ready is True
            return contracts.ProviderSecondResult(handle=self, ready=ready)

        def start_tasks(self, task_group, async_exit_stack):
            lifecycle.append('second')
            async_exit_stack.callback(lifecycle.append, 'stop second')

    class ChooseChoose(contracts.ChooseChoose):
        def load(self, *, services, type, first, left, right):
            calls.append(('choose', services.name))
            assert not hasattr(services, 'trigger_self')
            assert not hasattr(services, 'set_wakeup_time')
            return contracts.ChooseChooseResult(output=left if first else right)

    class ProbeRun(contracts.ProbeRun):
        def prepare(self, *, services, handle, flag):
            calls.append(('prepare', handle, flag))
            self.handle = handle
            self.flag = flag

        def run(self, *, services, handle, flag):
            assert handle is self.handle and flag is self.flag
            calls.append(('run', handle))
            return contracts.ProbeRunResult(output=flag)

    extension = test_extension(
        initializers={'TestLibrary.Provider:first': ProviderFirst, 'TestLibrary.Provider:second': ProviderSecond},
        procedures={'TestLibrary.Choose:choose': ChooseChoose, 'TestLibrary.Probe:run': ProbeRun},
        resources={'TestLibrary.Handle': Handle}, contracts=contracts,
    )
    engine = Engine(LoadedProgram.load(program, override_extensions=[extension]))
    first = engine.get_native_state('provider.first')
    second = engine.get_native_state('provider.second')
    assert second.first is first
    assert calls.index(('first', 'provider')) < calls.index(('second', first))
    assert calls[-1] == ('prepare', first, True)
    assert engine.get_value('plain') is True
    assert engine.get_value('choose_plain') is True
    assert engine.get_value('provider.Ready') is True
    assert engine.get_value('choose_duration') == (1 << 64,)
    loaded = list(calls)
    assert engine.step()[0] == 2
    engine.assert_value_is('probe', True)
    engine.assert_value_is('forward', True)
    assert engine.step()[0] == 0
    first.services.trigger('handle')
    assert engine.step()[0] == 1
    assert calls == loaded + [('run', first), ('run', first)]
    assert second.services is not first.services
    # Each engine loads independently and owns distinct resource objects.
    other = Engine(LoadedProgram.load(program, override_extensions=[extension]))
    assert other.get_native_state('provider.first') is not first

# The runtime rejects malformed load schedules before executing native code.
load = program.procs[RunStage.LOAD]
with checks.assertRaisesRegex(ValueError, 'assigned more than once'):
    validate_program(replace(program, procs={**program.procs, RunStage.LOAD: (load[0],) + load}))
with checks.assertRaisesRegex(ValueError, 'read before assignment'):
    validate_program(replace(program, procs={**program.procs, RunStage.LOAD: tuple(reversed(load))}))
with checks.assertRaisesRegex(ValueError, 'must be assigned once'):
    validate_program(replace(program, procs={RunStage.LOAD: ()}))
with checks.assertRaisesRegex(ValueError, 'Unknown procedure name'):
    engine.trigger('choose_handle.choose')


async def run_test():
    async with asyncio.TaskGroup() as task_group, contextlib.AsyncExitStack() as exit_stack:
        engine.activate(task_group, exit_stack)
        assert lifecycle == ['first', 'second']
    assert lifecycle == ['first', 'second', 'stop second', 'stop first']
