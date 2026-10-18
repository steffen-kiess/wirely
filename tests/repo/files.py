from pathlib import Path

from wirely.test import example_tests, negative_tests, python_only_tests, runtime_tests, to_compile


root = Path(__file__).resolve().parents[2]
expected_files = [
    *(root / (name + '.wrl') for name in example_tests),
    *(root / f'tests/{name}.wrl' for name in negative_tests),
    *(root / f'tests/{name}.py' for name in python_only_tests),
    *(root / f'tests/{name}{suffix}' for name in runtime_tests for suffix in ('.py', '.wrl')),
    *(root / f'tests/{name}.wrl' for name in to_compile),
]
existing_files = [
    *(root / 'examples').rglob('*.wrl'),
    *(root / 'tests').rglob('*.wrl'),
    *(root / 'tests').rglob('*.py'),
]
expected = set(expected_files)
# The test infrastructure, including fixture packages, is not a runnable test.
existing = {path for path in existing_files
            if not path.is_relative_to(root / 'tests/infra') and path != root / 'tests/__init__.py'}
assert expected == existing, (
    f'Missing files: {sorted(str(path.relative_to(root)) for path in expected - existing)}\n'
    f'Unlisted files: {sorted(str(path.relative_to(root)) for path in existing - expected)}'
)


async def run_test():
    pass
