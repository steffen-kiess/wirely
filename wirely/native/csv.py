import asyncio
import contextlib
import datetime
import os
import random
import sys
import typing
from collections.abc import Sequence
from typing import Any

import wirely
from wirely import MonotonicTime, Some
from wirely.runtime.value_types import DynamicValue, ValueType
from wirely.generated import csv as contracts
from wirely.misc.repeated_errors import RepeatedError
from wirely.program.program import RunStage
from wirely.runtime.native_api import PreparationServices, ProcedureServices, StateServices
from wirely.util import verbose_print


# Delay before the first attempt to open the file again after it could not be opened or written, doubled after each
# failed attempt up to the maximum, and shortened randomly by up to a fifth.
REOPEN_DELAY_MIN = 1.0
REOPEN_DELAY_MAX = 60.0


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
        # The file is unbuffered, so that each row reaches the kernel at once and nothing is left over to be written
        # later, for example when closing the file after an error. A raw write can be short, for example on a full
        # disk; the rest is written again, which raises the error.
        # TODO: Use os.write or stream.write? Is stream.write atomic?
        data = memoryview(encoded)
        while data:
            data = data[self.stream.write(data):]


class ValueWriterCreate(contracts.ValueWriterCreate):
    # TODO: Should writing to the file be done on a background thread?
    # Writing is synchronous on the event loop, which relies on the file system being fast, as local storage is; a
    # network file system could block the runtime.

    def initialize(self, *, services: StateServices[typing.Literal['state']],
                   filename: str) -> contracts.ValueWriterCreateResult:
        self.filename = filename
        self.file: typing.BinaryIO | None = None
        self.writer: MyCsvWriter | None = None

        self.last_value_csv = ('', False)
        # Set after a write failed, until the file is open again and a row with empty old and new values marks the
        # time of the failure.
        self.failed = False
        self.failed_at: datetime.datetime | None = None
        self.errors = RepeatedError(f'Writing CSV file {filename!r}')
        self.reopen_delays = (REOPEN_DELAY_MIN, REOPEN_DELAY_MAX)
        # Set while no file is open, which wakes the task that opens it again.
        self.closed = asyncio.Event()
        self.closed.set()

        # The rows of two writers of one file would be mixed up. This only compares the paths.
        writers: dict[str, str] = services.program_state.setdefault('CSV', {})
        path = os.path.normpath(filename)
        if path in writers:
            raise ValueError(f'CSV file {filename!r} is already written by {writers[path]}')
        writers[path] = services.name
        return contracts.ValueWriterCreateResult(state=self)

    def start_tasks(self, task_group: asyncio.TaskGroup, async_exit_stack: contextlib.AsyncExitStack) -> None:
        super().start_tasks(task_group, async_exit_stack)

        async_exit_stack.callback(self.close_for_shutdown)
        # The file is normally open from the first step on; if it cannot be opened, the task tries again.
        self.try_open()
        self.start_task(task_group, async_exit_stack, self.reopen_task())

    async def reopen_task(self) -> None:
        delay = self.reopen_delays[0]
        while True:
            if self.file is not None:
                await self.closed.wait()
                delay = self.reopen_delays[0]
                continue
            await asyncio.sleep(delay * random.uniform(0.8, 1.0))
            if not self.try_open():
                delay = min(delay * 2, self.reopen_delays[1])

    def try_open(self) -> bool:
        """Open the file for appending; log and return False if it cannot be opened."""
        verbose_print(f'Opening CSV file {self.filename!r}', flush=True)
        try:
            file = open(self.filename, 'a+b', buffering=0)
        except OSError as e:
            self.errors.report(str(e))
            return False
        try:
            size = file.seek(0, 2)
            if size != 0:
                last_byte = os.pread(file.fileno(), 1, size - 1)
                verbose_print(f'  got byte at end: {last_byte!r}', flush=True)
                if last_byte == b'':
                    raise OSError('Failed to read last byte')
                elif last_byte != b'\n':
                    print(f'CSV.ValueWriter: Warning: Incomplete line at end of {self.filename!r}, writing newline', flush=True)
                    # Note: Write only \n instead of \r\n, so a reader can see that this was an incomplete line
                    # TODO: Use os.write instead?
                    file.write(b'\n')
            writer = MyCsvWriter(file)
            if self.failed:
                # Before the failure, the last value is uncertain, since the failed row may have been written; after
                # it, values are missing.
                writer.write_row([_time_value(self.failed_at), CsvValue('', False), CsvValue('', False)])
        except OSError as e:
            file.close()
            self.errors.report(str(e))
            return False
        self.file = file
        self.writer = writer
        self.failed = False
        self.failed_at = None
        self.errors.end()
        self.closed.clear()
        # The current value is written as after a start.
        self.last_value_csv = ('', False)
        self.services.trigger('state')
        return True

    def fail(self, time: datetime.datetime | None, error: OSError) -> None:
        """Close the file after a failed write, so that the task opens it again."""
        self.errors.report(str(error))
        if not self.failed:
            self.failed = True
            self.failed_at = time
        self.close_file()

    def close_file(self) -> None:
        if self.file is not None:
            with contextlib.suppress(OSError):
                self.file.close()
        self.file = None
        self.writer = None
        self.last_value_csv = ('', False)
        self.closed.set()

    def close_for_shutdown(self) -> None:
        if self.writer is not None:
            time = self.services.shutdown_time
            if not self.services.is_shutting_down:
                if self.last_value_csv != ('', False):
                    print(f'CSV.ValueWriter: Warning: shutdown time is unavailable during shutdown but a value has been written', flush=True, file=sys.stderr)
            else:
                self.write_value(('', False), time)
                # Make sure that if after the shutdown another step is run run() will be called again
                self.services.trigger('state')
        self.close_file()

    # TODO: Periodically re-write the current value

    def write_value(self, new_value_csv: tuple[str, bool], time: datetime.datetime | None, *, force: bool = False) -> bool:
        if self.last_value_csv == new_value_csv and not force:
            return False

        row = [
            _time_value(time),
            CsvValue(*self.last_value_csv),
            CsvValue(*new_value_csv),
        ]
        # print(f'CSV: Got value {new_value} {time.isoformat()}')
        if self.writer is None:
            # Values are dropped while the file cannot be opened or written, and after shutdown.
            return False
        try:
            self.writer.write_row(row)
        except OSError as e:
            self.fail(time, e)
            return False
        self.last_value_csv = new_value_csv
        return True


def _time_value(time: datetime.datetime | None) -> CsvValue:
    return CsvValue('' if time is None else time.isoformat(), False)


class ValueWriterRun(contracts.ValueWriterRun):
    def prepare(self, *, services: PreparationServices, state: 'ValueWriterCreate', type: ValueType[Any]) -> None:
        # Converting the values relies on this.
        if not type.supports_csv:
            raise ValueError(f'Type must support CSV conversion: {type}')

    def run(self, *, services: ProcedureServices, state: 'ValueWriterCreate',
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

        written = state.write_value(new_value_csv, time, force=force)
        if force and written:
            # Re-evaluate the repeat decision next step after recording this write. A value that was not written,
            # because the file is unavailable, is written once the file is open again, which triggers this procedure.
            services.trigger_self()
        if written:
            # TODO: Think what happens if new_value_csv is ('', False)
            last_written_at = wirely.Some(services.start_time_for_stage(RunStage.DEFAULT).monotonic)

        return contracts.ValueWriterRunResult(last_written_at=last_written_at)


ValueWriterState = ValueWriterCreate  # Used as a resource type.
