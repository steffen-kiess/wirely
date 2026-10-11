import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from wirely.test import run_python_only_test, run_python_test, run_runtime_test


checks = TestCase()


async def run_test():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        script = root / 'test.py'
        marker = root / 'completed'
        script.write_text(
            'from pathlib import Path\n'
            "assert 'engine' not in globals()\n"
            'async def run_test():\n'
            f'    Path({str(marker)!r}).touch()\n', encoding='utf-8',
        )
        await run_python_test(str(script))
        assert marker.is_file()
        script.write_text('raise AssertionError("test failed")\n', encoding='utf-8')
        with checks.assertRaisesRegex(AssertionError, 'test failed'):
            await run_python_test(str(script))
        script.write_text('async def run_test():\n    raise AssertionError("async failed")\n',
                          encoding='utf-8')
        with checks.assertRaisesRegex(AssertionError, 'async failed'):
            await run_python_test(str(script))
        script.write_text('assert engine == 42\nasync def run_test():\n    pass\n',
                          encoding='utf-8')
        await run_python_test(str(script), engine=42)
        script.write_text('', encoding='utf-8')
        with checks.assertRaisesRegex(AttributeError, 'run_test'):
            await run_python_test(str(script))
        script.write_text('def run_test():\n    pass\n', encoding='utf-8')
        with checks.assertRaises(TypeError):
            await run_python_test(str(script))

        (root / 'test' / 'unit').mkdir(parents=True)
        (root / 'test/unit/plain.py').write_text('async def run_test():\n    pass\n',
                                               encoding='utf-8')
        (root / 'test/unit/plain.wrl').touch()
        (root / 'test/unit/paired.py').touch()
        (root / 'test/unit/paired.wrl').touch()
        with patch('wirely.test.__file__', str(root / 'wirely/test.py')), \
                patch('wirely.test.model.compiler.compile_files',
                      side_effect=AssertionError('Compiled a Python-only test')):
            await run_python_only_test('unit/plain')

        # An explicitly selected runtime test requires its companion even if
        # its Python module does not use an engine.
        (root / 'wirely').mkdir()
        (root / 'test/unit/paired.wrl').unlink()
        with patch('wirely.test.__file__', str(root / 'wirely/test.py')):
            with checks.assertRaises(FileNotFoundError):
                await run_runtime_test('unit/paired')
            (root / 'test/unit/paired.wrl').touch()
            (root / 'test/unit/paired.py').unlink()
            with checks.assertRaises(FileNotFoundError):
                await run_runtime_test('unit/paired')
            (root / 'test/unit/plain.py').unlink()
            with checks.assertRaises(FileNotFoundError):
                await run_python_only_test('unit/plain')

    # This test has no Wirely fixture and must never invoke the compiler.
    with patch('wirely.test.model.compiler.compile_files',
               side_effect=AssertionError('Compiled a Python-only test')):
        await run_python_only_test('unit/types')

    repository = Path(__file__).resolve().parents[2]
    selected = ['compiler/types', 'negative/assertions', 'examples/test',
                'unit/types', 'wrl/length']
    result = subprocess.run([sys.executable, '-m', 'wirely.test', '--timings', *selected],
                            cwd=repository, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    timings = result.stdout.split('Test durations (slowest first):', 1)[1]
    assert {line.split()[-1] for line in timings.splitlines() if line.strip()} == set(selected)
    for name in selected:
        suffix = '.py' if name == 'unit/types' else '.wrl'
        result = subprocess.run([sys.executable, '-m', 'wirely.test', name + suffix],
                                cwd=repository, capture_output=True, text=True)
        assert result.returncode != 0, name
        assert 'Unknown test name' in result.stderr, result.stderr
    for name in selected:
        result = subprocess.run([sys.executable, '-m', 'wirely.test', 'test/' + name],
                                cwd=repository, capture_output=True, text=True)
        assert result.returncode != 0, name
        assert 'Unknown test name' in result.stderr, result.stderr
