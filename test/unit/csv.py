import datetime
import io
from types import SimpleNamespace

import wirely
from wirely.native.csv import MyCsvWriter, ValueWriter
from wirely.program import RunStage


class ProcedureContext:
    def __init__(self):
        self.values = {
            'Type': wirely.ValueType(wirely.types.TypeOption(wirely.types.boolean)),
        }
        self.inputs = {}
        self.outputs = {}
        self.triggered = False
        self.time = SimpleNamespace(
            local=datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc),
            monotonic=wirely.MonotonicTime(1, 100),
        )

    def set_output(self, name, value):
        self.outputs[name] = value

    def trigger_self(self):
        self.triggered = True

    def start_time_for_stage(self, stage):
        assert stage is RunStage.DEFAULT
        return self.time


def check_repeat_recovery():
    context = ProcedureContext()
    writer = ValueWriter(context=context)
    output = io.BytesIO()
    writer.writer = MyCsvWriter(output)
    context.inputs = {
        'state': SimpleNamespace(object=writer),
        'input': wirely.Some(True),
        'repeat_elapsed': None,
        'last_written_at_old': None,
    }
    time = context.time.local.isoformat()
    expected = f'{time},,true\r\n'.encode()
    ValueWriter.run(context=context)
    assert output.getvalue() == expected
    assert context.outputs['last_written_at'] == wirely.Some(context.time.monotonic)
    assert context.triggered

    # An elapsed helper reports None when the previous clock generation is lost.
    context.inputs['last_written_at_old'] = context.outputs['last_written_at']
    context.time.monotonic = wirely.MonotonicTime(2, 50)
    ValueWriter.run(context=context)
    expected += f'{time},true,true\r\n'.encode()
    assert output.getvalue() == expected
    assert context.outputs['last_written_at'] == wirely.Some(context.time.monotonic)

    # The next invocation sees a running interval and does not repeat again.
    context.inputs['last_written_at_old'] = context.outputs['last_written_at']
    context.inputs['repeat_elapsed'] = wirely.Some(False)
    context.triggered = False
    ValueWriter.run(context=context)
    assert output.getvalue() == expected
    assert not context.triggered

    context.inputs['input'] = None
    ValueWriter.run(context=context)
    expected += f'{time},true,\r\n'.encode()
    assert output.getvalue() == expected

    # Empty values do not repeat for an expired interval or a lost generation.
    context.inputs['last_written_at_old'] = context.outputs['last_written_at']
    context.time.monotonic = wirely.MonotonicTime(3, 10)
    for elapsed in (wirely.Some(True), None):
        context.inputs['repeat_elapsed'] = elapsed
        context.triggered = False
        ValueWriter.run(context=context)
        assert output.getvalue() == expected
        assert context.outputs['last_written_at'] == context.inputs['last_written_at_old']
        assert not context.triggered


async def run_test():
    check_repeat_recovery()
