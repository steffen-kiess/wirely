import datetime
import io
from types import SimpleNamespace

from wirely.generated import extension as builtin_extension
import wirely
from wirely.native.csv import MyCsvWriter, ValueWriterCreate, ValueWriterRun
from wirely.program.program import RunStage
from wirely.runtime.native_api import ProcedureServices, StateServices


class Invocation:
    """Calls a ValueWriter's procedure with chosen inputs and stage time."""

    def __init__(self, writer):
        self.writer = writer
        self.procedure = ValueWriterRun()
        self.value_type = wirely.value_types.option(wirely.value_types.boolean)
        self.inputs = {}
        self.last_written_at = None
        self.triggered = False
        self.time = SimpleNamespace(
            local=datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc),
            monotonic=wirely.MonotonicTime(generation=1, ticks=100),
        )

    def run(self):
        def trigger_self():
            self.triggered = True

        services = ProcedureServices(name='writer', stage_times={RunStage.DEFAULT: self.time},
                                     trigger_self=trigger_self, native_types=builtin_extension.native_types)
        result = self.procedure.run(
            services=services, state=self.writer,
            last_written_at_old=self.inputs['last_written_at_old'],
            input=self.value_type.from_native(self.inputs['input'], native_types=builtin_extension.native_types),
            repeat_elapsed=self.inputs['repeat_elapsed'],
            type=self.value_type,
        )
        self.last_written_at = result.last_written_at


def check_repeat_recovery():
    writer = ValueWriterCreate()
    writer.initialize(services=StateServices(name='writer', output_keys={'state': 'state'},
                                             trigger=lambda key: None, shutdown_time=lambda: None,
                                             is_shutting_down=lambda: False, native_types=builtin_extension.native_types),
                      filename='unused.csv')
    invocation = Invocation(writer)
    output = io.BytesIO()
    writer.writer = MyCsvWriter(output)
    invocation.inputs = {
        'input': wirely.Some(True),
        'repeat_elapsed': None,
        'last_written_at_old': None,
    }
    time = invocation.time.local.isoformat()
    expected = f'{time},,true\r\n'.encode()
    invocation.run()
    assert output.getvalue() == expected
    assert invocation.last_written_at == wirely.Some(invocation.time.monotonic)
    assert invocation.triggered

    # An elapsed helper reports None when the previous clock generation is lost.
    invocation.inputs['last_written_at_old'] = invocation.last_written_at
    invocation.time.monotonic = wirely.MonotonicTime(generation=2, ticks=50)
    invocation.run()
    expected += f'{time},true,true\r\n'.encode()
    assert output.getvalue() == expected
    assert invocation.last_written_at == wirely.Some(invocation.time.monotonic)

    # The next invocation sees a running interval and does not repeat again.
    invocation.inputs['last_written_at_old'] = invocation.last_written_at
    invocation.inputs['repeat_elapsed'] = wirely.Some(False)
    invocation.triggered = False
    invocation.run()
    assert output.getvalue() == expected
    assert not invocation.triggered

    invocation.inputs['input'] = None
    invocation.run()
    expected += f'{time},true,\r\n'.encode()
    assert output.getvalue() == expected

    # Empty values do not repeat for an expired interval or a lost generation.
    invocation.inputs['last_written_at_old'] = invocation.last_written_at
    invocation.time.monotonic = wirely.MonotonicTime(generation=3, ticks=10)
    for elapsed in (wirely.Some(True), None):
        invocation.inputs['repeat_elapsed'] = elapsed
        invocation.triggered = False
        invocation.run()
        assert output.getvalue() == expected
        assert invocation.last_written_at == invocation.inputs['last_written_at_old']
        assert not invocation.triggered


async def run_test():
    check_repeat_recovery()
