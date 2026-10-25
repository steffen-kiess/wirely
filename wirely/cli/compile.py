"""Compile command orchestration."""

import argparse
import sys
import time
from pathlib import Path

from ..compiler.compilation import compile_files
from ..compiler import program as program_compiler
from ..runtime.engine import Engine
from ..program.writer import write_program


def run(args: argparse.Namespace) -> int | None:
    if args.output is not None and not args.create_program:
        print('Error: --output cannot be used with --no-create-program', file=sys.stderr)
        return 2
    if args.dump_program and not args.create_program:
        print('Error: --dump-program cannot be used with --no-create-program',
              file=sys.stderr)
        return 2
    if args.create_engine and not args.create_program:
        print('Error: --create-engine cannot be used with --no-create-program',
              file=sys.stderr)
        return 2

    timings: list[tuple[str, float]] | None = [] if args.timings else None
    try:
        return _compile(args, timings)
    finally:
        if timings:
            sys.stdout.flush()  # Keep the table after diagnostics.
            print_timings(timings)


def _compile(args: argparse.Namespace, timings: list[tuple[str, float]] | None) -> int | None:
    global_node_type = compile_files(args.input_filename, timings=timings)
    if global_node_type is None:
        return 1

    if args.create_program:
        start = time.perf_counter()
        prog = program_compiler.compile(global_node_type)
        if prog is None:
            return 1
        if timings is not None:
            timings.append(('lower program', time.perf_counter() - start))
        if args.dump_program:
            prog.dump()
        if args.output is not None:
            try:
                data = write_program(prog)
                Path(args.output).write_bytes(data)
            except (ValueError, OSError) as error:
                print(f'Error: {error}', file=sys.stderr)
                return 1
        if args.create_engine:
            start = time.perf_counter()
            Engine(prog)
            if timings is not None:
                timings.append(('create engine', time.perf_counter() - start))
    return None


def print_timings(timings: list[tuple[str, float]]) -> None:
    rows = timings + [('total', sum(duration for _, duration in timings))]
    width = max(len(name) for name, _ in rows)
    for name, duration in rows:
        print(f'{name:<{width}}  {duration * 1000:9.1f} ms', file=sys.stderr)
