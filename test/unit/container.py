import argparse
import contextlib
import io
import os
import struct
import tempfile

from wirely.cli import objdump
from wirely.program import ContainerError, Section, SectionList


sections = [
    Section(1, 10, b'abc'),
    Section(2, 20, b''),
    Section(0xffffffff, 0, bytes(range(20))),
]
unsorted_sections = [sections[2], sections[0], sections[1]]


def check_round_trip():
    data = SectionList(unsorted_sections, (0, 3)).to_bytes()
    loaded = SectionList.from_bytes(data)
    assert loaded.version == (0, 3)
    assert loaded.sections == sections
    assert SectionList.from_bytes(SectionList().to_bytes()).sections == []

    previous_end = 16 + len(sections) * 16
    for index, section in enumerate(sections):
        offset, length, section_type, _ = struct.unpack_from('<IIII', data, 16 + index * 16)
        assert section_type == section.section_type
        assert offset % 8 == 0
        assert offset >= previous_end
        assert data[previous_end:offset] == bytes(offset - previous_end)
        assert length == section.length
        previous_end = offset + length
    assert previous_end == len(data)


def corrupt(data, offset, fmt, value):
    data = bytearray(data)
    struct.pack_into(fmt, data, offset, value)
    return bytes(data)


def check_rejected(data, message):
    try:
        SectionList.from_bytes(data)
    except ContainerError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError(f'Expected rejection: {message}')


def check_invalid_files():
    data = SectionList(sections).to_bytes()
    first_entry = 16
    check_rejected(data[:7], 'identification prefix')
    check_rejected(data[:15], '16-byte header')
    check_rejected(struct.pack('<IHH', SectionList.MAGIC, 1, 0), 'major version 1')
    check_rejected(corrupt(data, 0, '<I', 0), 'magic')
    check_rejected(corrupt(data, 4, '<H', 1), 'major version 1')
    check_rejected(data + bytes(8), 'header says')
    check_rejected(corrupt(data, 12, '<I', 1000), 'Directory')
    check_rejected(corrupt(data, first_entry + 4, '<I', len(data)), 'past the end')
    check_rejected(corrupt(data, first_entry, '<I', 68), 'aligned')
    check_rejected(corrupt(data, first_entry + 16 + 8, '<I', 1), 'strictly ascending order at section 1')
    check_rejected(corrupt(data, first_entry + 8, '<I', 3), 'strictly ascending order at section 1')
    assert SectionList.from_bytes(corrupt(data, 6, '<H', 7)).version == (0, 7)

    for invalid in [SectionList([Section(1, 0, b''), Section(1, 0, b'')]),
                    SectionList([Section(1 << 32, 0, b'')]),
                    SectionList([], (1 << 16, 0))]:
        try:
            invalid.to_bytes()
        except ValueError:
            pass
        else:
            raise AssertionError(f'Expected write failure for {invalid}')


def run_objdump(path, **options):
    options = {'section_headers': False, 'all_headers': False, 'full_contents': False, **options}
    args = argparse.Namespace(input_filename=path, **options)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
        status = objdump.run(args)
    return status, output.getvalue()


def check_objdump():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, 'program.wrly')
        SectionList(sections, (0, 1)).save(path)

        status, headers = run_objdump(path, section_headers=True)
        assert status == 0
        assert 'Format version 0.1, 3 sections' in headers
        assert '  0  0x00000001    0x0000000a   3' in headers
        assert run_objdump(path, all_headers=True) == (0, headers)

        status, contents = run_objdump(path, full_contents=True)
        assert status == 0
        assert 'Contents of section 2 (type 0xffffffff, record type 0x00000000):' in contents
        assert ' 0000 00010203 04050607 08090a0b 0c0d0e0f  ................' in contents
        assert ' 0010 10111213' in contents

        status, both = run_objdump(path, section_headers=True, full_contents=True)
        assert status == 0 and both == headers + '\n' + contents
        assert run_objdump(path)[0] == 2
        assert run_objdump(os.path.join(directory, 'missing'), section_headers=True)[0] == 1


check_round_trip()
check_invalid_files()
check_objdump()


async def run_test():
    pass
