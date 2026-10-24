"""Command line of the test runner."""

from __future__ import annotations

import argparse
import sys
import time
import traceback

from .execution import run_named_test
from .parallel import run_parallel
from .registry import example_tests, exclusive_tests, negative_tests, python_only_tests, runtime_tests, to_compile


def main() -> None:
    parser = argparse.ArgumentParser(description='Run Wirely tests')
    parser.add_argument('--bytecode-roundtrip', action='store_true',
                        help='Write and reload programs before creating test engines')
    parser.add_argument('--timings', action='store_true',
                        help='List test durations, slowest first, at the end')
    parser.add_argument('--keep-going', action='store_true',
                        help='Run the remaining tests after a failure and list the failed tests at the end')
    parser.add_argument('--processes', type=int, default=1, metavar='N',
                        help='Run tests in N worker processes (default: 1, serial in this process)')
    parser.add_argument('--threads', type=int, default=1, metavar='N',
                        help='Run up to N tests concurrently in each worker process (default: 1)')
    parser.add_argument('names', nargs='*', metavar='TEST',
                        help='Extensionless test names or directories (default: all tests)')
    args = parser.parse_intermixed_args()
    if args.processes < 1 or args.threads < 1:
        parser.error('--processes and --threads must be at least 1')
    all_tests = example_tests + to_compile + negative_tests + python_only_tests + runtime_tests
    unknown = set(exclusive_tests) - set(all_tests)
    if unknown:
        raise Exception(f'Unregistered exclusive tests: {sorted(unknown)!r}')
    selected = set(all_tests)
    if args.names:
        selected = set()
        for selector in args.names:
            prefix = selector.rstrip('/') + '/'
            matches = {name for name in all_tests if name == selector or name.startswith(prefix)}
            if not matches:
                raise Exception(f'Unknown test name {selector!r}')
            selected.update(matches)
    names = [name for name in all_tests if name in selected]

    durations: list[tuple[str, float]] = []
    failed: list[str] = []

    def run_one(name: str) -> None:
        if not args.keep_going:
            run_named_test(name, bytecode_roundtrip=args.bytecode_roundtrip)
            return
        try:
            run_named_test(name, bytecode_roundtrip=args.bytecode_roundtrip)
        except Exception:
            traceback.print_exc()
            failed.append(name)

    try:
        if args.processes > 1 or args.threads > 1:
            run_parallel(names, processes=args.processes, threads=args.threads, keep_going=args.keep_going,
                         durations=durations, failed=failed, bytecode_roundtrip=args.bytecode_roundtrip)
        else:
            for name in names:
                start = time.perf_counter()
                try:
                    run_one(name)
                finally:
                    durations.append((name, time.perf_counter() - start))
    finally:
        if args.timings:
            print('\nTest durations (slowest first):', flush=True)
            for name, elapsed in sorted(durations, key=lambda item: (-item[1], item[0])):
                print(f'  {elapsed:.3f}s  {name}', flush=True)
    if failed:
        if args.keep_going or args.processes > 1 or args.threads > 1:
            print('\nFailed tests:', flush=True)
            for name in failed:
                print(f'  {name}', flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
