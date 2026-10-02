import asyncio
import contextlib
import dataclasses
import os
import sys

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


class CsvValue:
    def __init__(self, value, do_quote):
        self.value = value
        self.do_quote = do_quote

        if not do_quote:
            if '\r' in value or '\n' in value or ',' in value or '"' in value:
                raise Exception(f'Attempting to create CsvValue for string {value!r} without quoting')


class MyCsvWriter:
    def __init__(self, stream):
        self.stream = stream

    # https://www.rfc-editor.org/rfc/rfc4180
    def write_row(self, row):
        csv_string = ''
        for i, value in enumerate(row):
            if not isinstance(value, CsvValue):
                raise Exception(f'Expected a CsvValue, got a {type(value)}')

            if i > 0:
                csv_string += ','

            if value.do_quote:
                csv_string += '"' + value.value.replace('"', '""')
            else:
                csv_string += value.value
        csv_string += '\r\n'

        encoded = csv_string.encode('utf-8')
        # print(encoded)
        # TODO: Use os.write or stream.write? Is stream.write atomic?
        self.stream.write(encoded)


@dataclasses.dataclass(frozen=True)
class CsvWriterParameters:
    type: blocks.Type
    filename: str

    @staticmethod
    def create(*, parameter_values):
        ty = parameter_values['Type']
        if not ty.has_to_csv_value:
            # TODO: Error message position?
            raise Exception(f'Type {ty} does not support converting to CSV')

        filename = parameter_values['Filename'].value

        return CsvWriterParameters(type=ty, filename=filename)


@native_block_type(native_block_types, has_state=True, data_type=CsvWriterParameters)
class ValueWriter(blocks.NativeState):
    # TODO: Should writing to the file be done on a background thread?

    def __init__(self, path, engine, static_block_context, *, inputs):
        super().__init__(path, engine, static_block_context, inputs=inputs)

        self.file = None

        self.data = static_block_context.parsed_native_instance

        self.last_value_csv = ('', False)

        # TODO: Clean this up
        proc = None
        for p in self.block_or_singleton.block_procedures:
            if p.name == 'run':
                proc = p
        if proc is None:
            raise Exception('proc is None')
        self.proc = proc

    def start_tasks(self, task_group, async_exit_stack: contextlib.AsyncExitStack):
        super().start_tasks(task_group, async_exit_stack)

        async_exit_stack.enter_context(self.open_file())

    @contextlib.contextmanager
    def open_file(self):
        if self.file is not None:
            raise Exception('File already open')

        print(f'Opening CSV file {self.data.filename!r}', flush=True)
        with open(self.data.filename, 'a+b', buffering=0) as file:
            size = file.seek(0, 2)
            if size != 0:
                last_byte = os.pread(file.fileno(), 1, size - 1)
                print(f'  got byte at end: {last_byte!r}', flush=True)
                if last_byte == b'':
                    raise Exception('Failed to read last byte')
                elif last_byte != b'\n':
                    print(f'  Incomplete line at end, writing newline', flush=True)
                    # Note: Write only \n instead of \r\n, so a reader can see that this was an incomplete line
                    # TODO: Use os.write instead?
                    file.write(b'\n')

            try:
                self.file = file
                self.writer = MyCsvWriter(file)

                try:
                    yield
                finally:
                    time = self.engine.shutdown_time
                    if time is None:
                        if self.last_value_csv != ('', False):
                            print(f'CSV.ValueWriter: Warning: engine.shutdown_time is None during shutdown but a value has been written', flush=True, file=sys.stderr)
                    else:
                        self.write_value(('', False), time)
                        # Make sure that if after the shutdown another step is run run() will be called again
                        self.engine.trigger(self.block_path, self.proc)
            finally:
                self.writer = None
                self.file = None

    # TODO: Periodically re-write the current value

    def write_value(self, new_value_csv, time, *, force=False):
        if self.last_value_csv == new_value_csv and not force:
            return False

        row = [
            CsvValue(time.isoformat(), False),
            CsvValue(*self.last_value_csv),
            CsvValue(*new_value_csv),
        ]
        # print(f'CSV: Got value {new_value} {time.isoformat()}')
        if self.writer is None:
            # TODO: What should be done here? Seems to happen during shutdown (called from run())
            print(f'CSV.ValueWriter: Warning: self.writer is None in write_value()', flush=True, file=sys.stderr)
            # TODO: What should be returned here?
            return True
        self.writer.write_row(row)
        self.last_value_csv = new_value_csv
        return True

    @native_procedure
    def read_update_time(self, *, context):
        value = context.inputs['repeat_time_old']
        context.set_result('repeat_time_old', value)

    @native_procedure
    def run(self, *, context):
        data = context.static.block.parsed_native_instance

        new_value = context.inputs['input']
        reached = context.inputs['repeat_time_reached']
        repeat_time = context.inputs['repeat_time_old']

        new_value_csv = self.data.type.to_csv_value(new_value)

        # If reached is None or Some(True), the value has to be repeated
        # Note: The empty value is not repeated
        force = reached != blocks.Some(False) and new_value_csv != ('', False)
        # print(f'NewValue={new_value} Reached={reached} force={force}')

        # Note: This uses RunStage.Default to be as consistent as possible with the time seen by procedures in the default stage
        time = context.step_info.stage_start_time[blocks.RunStage.Default]

        if force:
            # Make sure that if in the next cycle/step the timeout is reached again, the procedure will be called again.
            # TODO: Should this simply be done whenever write_value() is called?
            self.engine.trigger(self.block_path, self.proc)

        written = self.write_value(new_value_csv, time, force=force)
        if written:
            # TODO: Think what happens if new_value_csv is ('', False)
            now = context.step_info.stage_start_time_monotonic[blocks.RunStage.Default]
            # Write value once an hour
            repeat_time = now + blocks.Duration.parse('PT1H')

        context.set_result('repeat_time', repeat_time)
