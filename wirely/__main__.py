import sys
import argparse
import importlib
import asyncio


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title='subcommands', dest='command', required=True)

    run_service_parser = subparsers.add_parser('run-service')
    run_service_parser.add_argument('--verbose', action='store_true')
    run_service_parser.add_argument('--debug', action='store_true')
    run_service_parser.add_argument('input_filename', type=str, nargs='+')
    run_service_parser.set_defaults(module_name='wirely.cli.service')

    compile_parser = subparsers.add_parser('compile')
    program_group = compile_parser.add_mutually_exclusive_group()
    program_group.add_argument('--create-program', action='store_true')
    program_group.add_argument('--no-create-program', dest='create_program', action='store_false')
    engine_group = compile_parser.add_mutually_exclusive_group()
    engine_group.add_argument('--create-engine', action='store_true')
    engine_group.add_argument('--no-create-engine', dest='create_engine', action='store_false')
    compile_parser.add_argument('--dump-program', action='store_true',
                                help='dump the compiled runtime program to stdout')
    compile_parser.add_argument('input_filename', nargs='+')
    compile_parser.set_defaults(module_name='wirely.cli.compile_command',
                                create_program=True, create_engine=False)

    show_graph_parser = subparsers.add_parser('show-graph')
    show_graph_parser.add_argument('input_filename')
    show_graph_parser.set_defaults(module_name='wirely.cli.graphviz')

    args = parser.parse_args()
    module = importlib.import_module(args.module_name)
    has_async_run = hasattr(module, 'async_run')
    has_run = hasattr(module, 'run')
    if has_async_run == has_run:
        raise RuntimeError(f'{args.module_name} must define exactly one of run() or async_run()')
    if has_async_run:
        sys.exit(asyncio.run(module.async_run(args)))
    else:
        sys.exit(module.run(args))


if __name__ == '__main__':
    main()
