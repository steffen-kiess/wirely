"""Bytecode mode propagation through serial and parallel test entry points."""

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tests.infra.bytecode import bytecode_roundtrip_mode
from tests.infra.runner import __main__ as runner
from tests.infra.runner.execution import run_named_test

checks = TestCase()
root = Path(__file__).resolve().parents[2]


def check_context():
    initial_mode = bytecode_roundtrip_mode.get()
    for enabled in (True, False):
        with patch('tests.infra.runner.execution._run_named_test') as run:
            run.side_effect = lambda name: checks.assertEqual(bytecode_roundtrip_mode.get(), enabled)
            run_named_test('test', bytecode_roundtrip=enabled)
            run.assert_called_once_with('test')
        assert bytecode_roundtrip_mode.get() == initial_mode
    with patch('tests.infra.runner.execution._run_named_test', side_effect=ValueError('test failure')):
        with checks.assertRaisesRegex(ValueError, 'test failure'):
            run_named_test('test', bytecode_roundtrip=not initial_mode)
    assert bytecode_roundtrip_mode.get() == initial_mode

    # Context values stay local to each thread without changing process state.
    barrier = threading.Barrier(2)

    def thread_mode(enabled):
        token = bytecode_roundtrip_mode.set(enabled)
        try:
            barrier.wait(timeout=10)
            return bytecode_roundtrip_mode.get()
        finally:
            bytecode_roundtrip_mode.reset(token)
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(thread_mode, (True, False))) == [True, False]
    assert bytecode_roundtrip_mode.get() == initial_mode


def check_serial_cli():
    for enabled in (True, False):
        argv = ['run-tests', 'unit/bytecode_roundtrip']
        if enabled:
            argv.append('--bytecode-roundtrip')
        with patch.object(sys, 'argv', argv), patch.object(runner, 'run_named_test') as run:
            runner.main()
            run.assert_called_once_with('unit/bytecode_roundtrip', bytecode_roundtrip=enabled)


def check_processes():
    # The probe runs no child processes, keeping these entry-point checks finite.
    for executable in ([sys.executable, 'tools/run-tests'], [sys.executable, '-m', 'tests.infra.runner']):
        result = subprocess.run([*executable, '--bytecode-roundtrip', '--processes', '2', '--threads', '2',
                                 'unit/bytecode_roundtrip'], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Bytecode round-trip decoded: True' in result.stdout, result.stdout
        assert 'Done running test unit/bytecode_roundtrip' in result.stdout


async def run_test():
    check_context()
    check_serial_cli()
    check_processes()
