"""Compile command orchestration."""

import sys

from ..compiler import compile_files
from ..compiler import program as program_compiler
from ..runtime.engine import Engine


def run(args):
    if args.dump_program and not args.create_program:
        print('Error: --dump-program cannot be used with --no-create-program',
              file=sys.stderr)
        return 2
    if args.create_engine and not args.create_program:
        print('Error: --create-engine cannot be used with --no-create-program',
              file=sys.stderr)
        return 2

    global_node_type = compile_files(args.input_filename)
    if global_node_type is None:
        return 1

    if args.create_program:
        prog = program_compiler.compile(global_node_type)
        if prog is None:
            return 1
        if args.dump_program:
            prog.dump()
        if args.create_engine:
            Engine(prog)
