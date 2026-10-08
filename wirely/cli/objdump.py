"""Display the section container of a binary program file."""

import sys

from ..program.container import ContainerError, SectionList


def _print_headers(section_list):
    major, minor = section_list.version
    print(f'Format version {major}.{minor}, {len(section_list.sections)} sections')
    print()
    print('Idx  Section type  Record type  Length')
    for index, section in enumerate(section_list.sections):
        print(f'{index:3}  0x{section.section_type:08x}    0x{section.record_type:08x}   {section.length}')


def _print_contents(section_list):
    for index, section in enumerate(section_list.sections):
        print(f'Contents of section {index} (type 0x{section.section_type:08x}, '
              f'record type 0x{section.record_type:08x}):')
        data = section.data
        for start in range(0, len(data), 16):
            line = data[start:start + 16]
            groups = ' '.join(line[i:i + 4].hex() for i in range(0, 16, 4))
            text = ''.join(chr(b) if 0x20 <= b < 0x7f else '.' for b in line)
            print(f' {start:04x} {groups:<35}  {text}')


def run(args):
    show_headers = args.section_headers or args.all_headers
    if not show_headers and not args.full_contents:
        print('Error: objdump requires at least one of -h, -x, or -s', file=sys.stderr)
        return 2

    try:
        section_list = SectionList.load(args.input_filename)
    except (ContainerError, OSError) as error:
        print(f'Error: {args.input_filename}: {error}', file=sys.stderr)
        return 1

    if show_headers:
        _print_headers(section_list)
    if show_headers and args.full_contents:
        print()
    if args.full_contents:
        _print_contents(section_list)
    return 0
