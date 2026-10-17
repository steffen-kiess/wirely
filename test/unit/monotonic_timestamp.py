from unittest.mock import patch

import wirely
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import program as program_compiler
from wirely.program import RunStage
from wirely.runtime.engine import Engine
from wirely.test import test_native_bindings, test_contracts


source = '''namespace TestLibrary {
  resource type HeldEventState;
  native node type HeldEventSource {
    default output: event boolean;

    binding state: resource HeldEventState;
    native initializer produces state as "state";
    native procedure run depends on state as "state" produces default output as "output";
  }
}
node input: TestLibrary.HeldEventSource {}
node timestamp: Timer.MonotonicTimestamp { Type: boolean }
connect input -> timestamp;
'''
graph = compile_strs(_standard_library_sources(['std', 'timer']) + [('<held-event>', source)])
assert graph is not None
with test_contracts(graph) as contracts:
    registry = {'initializers': {}, 'procedures': {}}

    class HeldEventSource(contracts.HeldEventSourceInitializer):
        def initialize(self, *, services):
            self.value = None
            return contracts.HeldEventSourceInitializerResult(state=self)

        def set_value(self, value):
            if self.value != value:
                self.value = value
                self.services.trigger('state')

        class Run(contracts.HeldEventSourceRun):
            def run(self, *, services, state):
                # The timestamp node must keep stepping even when this output is cached.
                return contracts.HeldEventSourceRunResult(output=state.value)
    registry['initializers']['TestLibrary.HeldEventSource'] = HeldEventSource
    registry['procedures']['TestLibrary.HeldEventSource:run'] = HeldEventSource.Run

    with test_native_bindings(**registry), \
            test_native_bindings(resources={'TestLibrary.HeldEventState': HeldEventSource}):
        runtime = Engine(program_compiler.compile_nocatch(graph))

runtime.step()
runtime.assert_value_is('timestamp', None)
runtime.assert_is_idle()

held_source = runtime.get_native_state('input')
held_source.set_value(wirely.Some(False))
_, _, first_step = runtime.step()
first = runtime.get_value('timestamp')
assert isinstance(first, wirely.Some)
assert first.value == first_step.stage_start_time_monotonic[RunStage.DEFAULT]
assert runtime.triggered_procs

runtime.advance_fake_monotonic_time(wirely.Duration.from_seconds(1))
run_count, _, second_step = runtime.step()
second = runtime.get_value('timestamp')
assert isinstance(second, wirely.Some)
assert second.value == second_step.stage_start_time_monotonic[RunStage.DEFAULT]
assert wirely.MonotonicTime.is_comparable(first.value, second.value)
assert wirely.MonotonicTime.compare(first.value, second.value) < 0
assert run_count == 1
assert runtime.triggered_procs

held_source.set_value(None)
runtime.step()
runtime.assert_value_is('timestamp', None)
assert not runtime.triggered_procs
runtime.assert_is_idle()


async def run_test():
    pass
