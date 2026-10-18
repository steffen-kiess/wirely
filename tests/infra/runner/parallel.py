"""Running tests in worker processes and threads."""

from __future__ import annotations

import contextlib
import io
import multiprocessing
import os
import queue
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Iterator, Sequence
from typing import Any, cast

from .execution import run_named_test
from .registry import exclusive_tests


class _ThreadOutput(io.TextIOBase):
    """A stream writing to the current thread's buffer, if it has one."""

    def __init__(self, original: Any) -> None:
        self.original = original
        self.local = threading.local()

    def _target(self) -> Any:
        buffer = getattr(self.local, 'buffer', None)
        return self.original if buffer is None else buffer

    def write(self, text: str) -> int:
        return cast(int, self._target().write(text))

    def flush(self) -> None:
        self._target().flush()

    def fileno(self) -> int:
        return cast(int, self.original.fileno())

    def isatty(self) -> bool:
        return cast(bool, self.original.isatty())

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return cast(str, self.original.encoding)


class _ProcessLock:
    """Lets tests in one process run concurrently, or one exclusive test alone."""

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.shared = 0
        self.exclusive = False
        self.waiting_exclusive = 0

    @contextlib.contextmanager
    def hold(self, exclusive: bool) -> Iterator[None]:
        with self.condition:
            if exclusive:
                self.waiting_exclusive += 1
                self.condition.wait_for(lambda: not self.exclusive and self.shared == 0)
                self.waiting_exclusive -= 1
                self.exclusive = True
            else:
                self.condition.wait_for(lambda: not self.exclusive and self.waiting_exclusive == 0)
                self.shared += 1
        try:
            yield
        finally:
            with self.condition:
                if exclusive:
                    self.exclusive = False
                else:
                    self.shared -= 1
                self.condition.notify_all()


@contextlib.contextmanager
def _captured_descriptors(output: io.StringIO) -> Iterator[None]:
    """Capture file descriptors 1 and 2, including output of child processes."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    with tempfile.TemporaryFile() as capture:
        os.dup2(capture.fileno(), 1)
        os.dup2(capture.fileno(), 2)
        try:
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            for descriptor in saved:
                os.close(descriptor)
            capture.seek(0)
            output.write(capture.read().decode('utf-8', errors='replace'))


def _worker(threads: int, normal: Any, exclusive: Any, results: Any, stop: Any) -> None:
    """Run tests from the queues until their stop markers; report to results."""
    for stream in (sys.stdout, sys.stderr):
        cast(io.TextIOWrapper, stream).reconfigure(line_buffering=True)
    stdout, stderr = _ThreadOutput(sys.stdout), _ThreadOutput(sys.stderr)
    sys.stdout, sys.stderr = stdout, stderr  # type: ignore[assignment]
    lock = _ProcessLock()

    def run(name: str, is_exclusive: bool) -> None:
        if stop.is_set():
            return
        results.put(('started', os.getpid(), name))
        output = io.StringIO()
        error = None
        with lock.hold(is_exclusive):
            start = time.perf_counter()
            alone = is_exclusive or threads == 1
            try:
                if alone:
                    with _captured_descriptors(output):
                        run_named_test(name)
                else:
                    stdout.local.buffer = stderr.local.buffer = output
                    try:
                        run_named_test(name)
                    finally:
                        stdout.local.buffer = stderr.local.buffer = None
            except KeyboardInterrupt:
                raise
            except BaseException:
                error = traceback.format_exc()
        results.put(('finished', os.getpid(), name, time.perf_counter() - start, output.getvalue(), error))

    def consume(tasks: Any, is_exclusive: bool) -> None:
        while (name := tasks.get()) is not None:
            run(name, is_exclusive)

    # Exclusive tests run on the main thread, which Qt requires.
    helpers = [threading.Thread(target=consume, args=(normal, False)) for _ in range(threads - 1)]
    for helper in helpers:
        helper.start()
    consume(exclusive, True)
    consume(normal, False)
    for helper in helpers:
        helper.join()
    results.put(('exit', os.getpid()))


def run_parallel(names: Sequence[str], *, processes: int, threads: int, keep_going: bool,
                 durations: list[tuple[str, float]], failed: list[str]) -> None:
    """Run tests in worker processes; record durations and failed tests."""
    context = multiprocessing.get_context('spawn')
    normal, exclusive, results = context.Queue(), context.Queue(), context.Queue()
    stop = context.Event()
    for name in names:
        (exclusive if name in exclusive_tests else normal).put(name)
    for _ in range(processes):
        exclusive.put(None)
    for _ in range(processes * threads):
        normal.put(None)

    workers: dict[int, Any] = {}
    for _ in range(processes):
        process = context.Process(target=_worker, args=(threads, normal, exclusive, results, stop))
        process.start()
        assert process.pid is not None
        workers[process.pid] = process
    running: dict[int, set[str]] = {pid: set() for pid in workers}
    exited: set[int] = set()

    def handle(message: tuple[Any, ...]) -> None:
        kind, pid = message[0], message[1]
        if kind == 'started':
            running[pid].add(message[2])
        elif kind == 'finished':
            _, _, name, duration, output, error = message
            running[pid].discard(name)
            durations.append((name, duration))
            sys.stdout.write(output)
            sys.stdout.flush()
            if error is not None:
                print(error, file=sys.stderr, end='', flush=True)
                failed.append(name)
                if not keep_going:
                    stop.set()
        else:
            exited.add(pid)

    try:
        while len(exited) < len(workers):
            try:
                handle(results.get(timeout=0.5))
                continue
            except queue.Empty:
                pass
            for pid, process in workers.items():
                if pid in exited or process.is_alive():
                    continue
                # Messages sent before the worker ended arrive first.
                with contextlib.suppress(queue.Empty):
                    while pid not in exited:
                        handle(results.get(timeout=0.5))
                if pid in exited:
                    continue
                exited.add(pid)
                for name in sorted(running[pid]):
                    print(f'Test {name} failed: worker exited with code {process.exitcode}',
                          file=sys.stderr, flush=True)
                    failed.append(name)
                if not running[pid]:
                    print(f'Worker exited with code {process.exitcode}', file=sys.stderr, flush=True)
                    failed.append(f'(worker exited with code {process.exitcode})')
                # The other workers take over its remaining tests.
                if not keep_going:
                    stop.set()
    finally:
        for process in workers.values():
            if process.is_alive() and stop.is_set() and len(exited) < len(workers):
                process.terminate()
            process.join()
