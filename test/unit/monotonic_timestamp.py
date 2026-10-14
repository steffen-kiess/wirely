from unittest.mock import patch

import wirely
import wirely.native.test_library
from wirely.compiler import _standard_library_sources, compile_strs
from wirely.compiler import program as program_compiler
from wirely.native import native_node_type
from wirely.program import RunStage
from wirely.runtime.contexts import NativeState
from wirely.runtime.engine import Engine


registry = {}


@native_node_type(registry, has_native_state=True)
class HeldEventSource(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)
        self.value = None

    def set_value(self, value):
        if self.value != value:
            self.value = value
            self.trigger('state')

    @staticmethod
    def run(*, context):
        # The timestamp node must keep stepping even when this output is cached.
        context.set_output('output', context.inputs['state'].object.value)


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
with patch.dict(wirely.native.test_library.native_node_types, registry), \
        patch.dict(wirely.native.test_library.native_resource_types, {'HeldEventState': HeldEventSource}):
    graph = compile_strs(_standard_library_sources(['std', 'timer']) + [('<held-event>', source)])
    assert graph is not None
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
