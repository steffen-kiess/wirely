import asyncio
import contextlib
import datetime
import os
import sys
import typing
from collections.abc import Iterator, Sequence
from typing import Any

import wirely
from wirely import DynamicValue, MonotonicTime, Some, ValueType
from wirely.generated import csv as contracts
from wirely.program import RunStage
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.util import verbose_print


class CsvValue:
    def __init__(self, value: str, do_quote: bool) -> None:
        self.value = value
        self.do_quote = do_quote

        if not do_quote:
            if '\r' in value or '\n' in value or ',' in value or '"' in value:
                raise Exception(f'Attempting to create CsvValue for string {value!r} without quoting')


class MyCsvWriter:
    def __init__(self, stream: typing.BinaryIO) -> None:
        self.stream = stream

    # https://www.rfc-editor.org/rfc/rfc4180
    def write_row(self, row: Sequence[CsvValue]) -> None:
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


class ValueWriter(contracts.ValueWriterCreate):
    # TODO: Should writing to the file be done on a background thread?

    def initialize(self, *, services: StateServices[typing.Literal['state']],
                   filename: str) -> contracts.ValueWriterCreateResult:
        self.filename = filename
        self.file: typing.BinaryIO | None = None

        self.last_value_csv = ('', False)
        return contracts.ValueWriterCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        async_exit_stack.enter_context(self.open_file())

    @contextlib.contextmanager
    def open_file(self) -> Iterator[None]:
        if self.file is not None:
            raise Exception('File already open')

        verbose_print(f'Opening CSV file {self.filename!r}', flush=True)
        with open(self.filename, 'a+b', buffering=0) as file:
            size = file.seek(0, 2)
            if size != 0:
                last_byte = os.pread(file.fileno(), 1, size - 1)
                verbose_print(f'  got byte at end: {last_byte!r}', flush=True)
                if last_byte == b'':
                    raise Exception('Failed to read last byte')
                elif last_byte != b'\n':
                    print(f'CSV.ValueWriter: Warning: Incomplete line at end of {self.filename!r}, writing newline', flush=True)
                    # Note: Write only \n instead of \r\n, so a reader can see that this was an incomplete line
                    # TODO: Use os.write instead?
                    file.write(b'\n')

            try:
                self.file = file
                self.writer: MyCsvWriter | None = MyCsvWriter(file)

                try:
                    yield
                finally:
                    time = self.services.shutdown_time
                    if not self.services.is_shutting_down:
                        if self.last_value_csv != ('', False):
                            print(f'CSV.ValueWriter: Warning: shutdown time is unavailable during shutdown but a value has been written', flush=True, file=sys.stderr)
                    else:
                        self.write_value(('', False), time)
                        # Make sure that if after the shutdown another step is run run() will be called again
                        self.services.trigger('state')
            finally:
                self.writer = None
                self.file = None

    # TODO: Periodically re-write the current value

    def write_value(self, new_value_csv: tuple[str, bool], time: datetime.datetime | None, *, force: bool = False) -> bool:
        if self.last_value_csv == new_value_csv and not force:
            return False

        row = [
            CsvValue('' if time is None else time.isoformat(), False),
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

    class Run(contracts.ValueWriterRun):
        def prepare(self, *, services: PreparationServices, state: 'ValueWriter', type: ValueType[Any]) -> None:
            pass

        def run(self, *, services: ProcedureServices, state: 'ValueWriter',
                last_written_at_old: Some[MonotonicTime] | None, input: DynamicValue,
                repeat_elapsed: Some[bool] | None, type: ValueType[Any]) -> contracts.ValueWriterRunResult:
            elapsed = repeat_elapsed
            last_written_at = last_written_at_old

            new_value_csv = input.to_csv_value()

            # If elapsed is None or Some(True), the value has to be repeated
            # Note: The empty value is not repeated
            force = elapsed != wirely.Some(False) and new_value_csv != ('', False)

            # Note: This uses RunStage.DEFAULT to be as consistent as possible with the time seen by procedures in the default stage
            time = services.start_time_for_stage(RunStage.DEFAULT).local

            if force:
                # Re-evaluate the repeat decision next step after recording this write.
                # TODO: Should this simply be done whenever write_value() is called?
                services.trigger_self()

            written = state.write_value(new_value_csv, time, force=force)
            if written:
                # TODO: Think what happens if new_value_csv is ('', False)
                last_written_at = wirely.Some(services.start_time_for_stage(RunStage.DEFAULT).monotonic)

            return contracts.ValueWriterRunResult(last_written_at=last_written_at)


ValueWriterState = ValueWriter  # Used as a resource type.
