import sys
import argparse
import importlib
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title='subcommands', dest='command', required=True)

    run_service_parser = subparsers.add_parser('run-service')
    run_service_parser.add_argument('--verbose', action='store_true')
    run_service_parser.add_argument('--debug', action='store_true')
    run_service_parser.add_argument('--state-directory',
                                    help='directory for the control sockets')
    run_service_parser.add_argument('--control-tls-cert',
                                    help='certificate chain for TLS on the control socket')
    run_service_parser.add_argument('--control-tls-key',
                                    help='private key for TLS on the control socket')
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
    compile_parser.add_argument('--output', metavar='FILE',
                                help='write the compiled binary program to FILE')
    compile_parser.add_argument('--timings', action='store_true',
                                help='print the duration of each compiler stage to stderr')
    compile_parser.add_argument('input_filename', nargs='+')
    compile_parser.set_defaults(module_name='wirely.cli.compile_command',
                                create_program=True, create_engine=False)

    show_graph_parser = subparsers.add_parser('show-graph')
    show_graph_parser.add_argument('input_filename')
    show_graph_parser.set_defaults(module_name='wirely.cli.graphviz')

    edit_parser = subparsers.add_parser('edit', help='edit programs as source and diagrams')
    edit_parser.add_argument('input_filename', nargs='+')
    edit_parser.set_defaults(module_name='wirely.cli.edit')

    export_parser = subparsers.add_parser('export-image', help='draw a diagram as a PDF or PNG image')
    export_parser.add_argument('-o', '--output', required=True, help='output file ending in .pdf or .png')
    export_parser.add_argument('--scope', help='dot-separated node type or namespace; default: the program')
    export_parser.add_argument('--scale', type=float, default=4, help='PNG pixels per grid unit (default: 4)')
    export_parser.add_argument('input_filename', nargs='+')
    export_parser.set_defaults(module_name='wirely.cli.export_image')

    client_parser = subparsers.add_parser('client', help='control a running runtime')
    importlib.import_module('wirely.cli.client').add_arguments(client_parser)
    client_parser.set_defaults(module_name='wirely.cli.client')

    objdump_parser = subparsers.add_parser('objdump', add_help=False,
                                           help='show the sections of a binary program file')
    objdump_parser.add_argument('--help', action='help', help='show this help message and exit')
    objdump_parser.add_argument('-h', '--section-headers', action='store_true',
                                help='list the section directory')
    objdump_parser.add_argument('-x', '--all-headers', action='store_true',
                                help='list all headers; currently the same as -h')
    objdump_parser.add_argument('-s', '--full-contents', action='store_true',
                                help='dump the contents of all sections')
    objdump_parser.add_argument('input_filename')
    objdump_parser.set_defaults(module_name='wirely.cli.objdump')

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
