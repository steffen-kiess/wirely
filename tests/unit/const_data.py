"""Const values in the ProgramData representation of the binary program format."""

import struct
from unittest import TestCase

from wirely.program import types
from wirely.program.const_data import ConstDataWriter, const_layout, read_const
from wirely.program.limits import MAX_TOTAL_MEMBER_COUNT, is_runtime_type
from wirely.types.option import Some
from wirely.types.unit import Unit


checks = TestCase()

# Types in a ValueTypes table, for type values.
type_table = [types.uint8, types.TypeOption(types.String)]


def type_index(value_type):
    return type_table.index(value_type)


def type_at(index):
    if index >= len(type_table):
        raise ValueError(f'Unknown type {index}')
    return type_table[index]


def round_trip(value_type, value):
    writer = ConstDataWriter(type_index)
    offset = writer.add(value_type, value)
    return read_const(writer.data, offset, value_type, type_at)


def check_round_trips():
    mode = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    strings = types.TypeList(types.String)
    labelled = types.TypeStruct('Labelled', {'flag': types.boolean, 'label': types.String,
                                             'counts': types.TypeList(types.uint16)})
    # Too many members for a runtime type, but a valid const value.
    wide = types.TypeStruct('Wide', [types.uint8] * MAX_TOTAL_MEMBER_COUNT)
    assert not is_runtime_type(wide)
    cases = [
        (types.uint8, 7), (types.int64, -5), (types.boolean, True), (types.unit, Unit()), (mode, 1),
        (types.TypeOption(types.uint32), Some(9)), (types.TypeOption(types.uint32), None),
        (types.String, ''), (types.String, 'héllo'), (types.ValueType, type_table[1]),
        (types.TypeList(types.uint8), ()), (types.TypeList(types.unit), (Unit(),) * 3),
        (strings, ('a', '', 'bc')), (types.TypeList(strings), (('x',), (), ('y', 'z'))),
        (types.TypeOption(types.String), Some('text')), (types.TypeOption(types.String), None),
        (labelled, (True, 'name', (1, 2, 3))), (wide, tuple(index % 256 for index in range(MAX_TOTAL_MEMBER_COUNT))),
        (types.TypeList(types.ValueType), (type_table[0], type_table[1])),
    ]
    for value_type, value in cases:
        assert round_trip(value_type, value) == value, (value_type, value)


def check_layout():
    pair = types.TypeStruct('Pair', {'a': types.TypeOption(types.uint64), 'b': types.uint8})
    for value_type in (types.uint16, types.unit, types.TypeOption(types.TypeOption(types.uint64)), pair):
        assert const_layout(value_type) == (value_type.alignment, value_type.data_size, value_type.size)
    assert const_layout(types.String) == (4, 8, 8)
    assert const_layout(types.ValueType) == (4, 4, 4)
    assert const_layout(types.TypeList(types.uint8)) == (4, 8, 8)
    assert const_layout(types.TypeOption(types.String)) == (4, 9, 12)

    # Runtime values use their runtime representation.
    writer = ConstDataWriter(type_index)
    assert writer.add(pair, (Some(3), 4)) == 0
    assert writer.data == pair.serialize((Some(3), 4)) + bytes(pair.size - pair.data_size)

    # Referenced bytes precede the slot referring to them.
    writer = ConstDataWriter(type_index)
    assert writer.add(types.String, 'ab') == 4
    assert writer.data == b'ab\0\0' + struct.pack('<II', 0, 2)
    writer = ConstDataWriter(type_index)
    assert writer.add(types.TypeList(types.uint16), (1, 2, 3)) == 8
    assert writer.data == struct.pack('<HHHxx', 1, 2, 3) + struct.pack('<II', 0, 3)
    flagged = types.TypeStruct('Flagged', {'flag': types.boolean, 'label': types.String})
    writer = ConstDataWriter(type_index)
    assert writer.add(flagged, (True, '')) == 0
    assert writer.data == b'\x01\0\0\0' + struct.pack('<II', 0, 0)
    writer = ConstDataWriter(type_index)
    assert writer.add(types.ValueType, type_table[1]) == 0
    assert writer.data == struct.pack('<I', 1)


def check_sharing():
    writer = ConstDataWriter(type_index)
    first = writer.add(types.String, 'shared')
    size = len(writer.data)
    assert writer.add(types.String, 'shared') == first and len(writer.data) == size
    numbers = types.TypeList(types.uint32)
    first = writer.add(numbers, (1, 2))
    size = len(writer.data)
    assert writer.add(numbers, (1, 2)) == first and len(writer.data) == size
    writer.add(types.String, 'shared!')
    assert len(writer.data) > size
    with checks.assertRaises(ValueError):
        writer.add(types.uint8, 256)
    with checks.assertRaisesRegex(ValueError, 'not const values'):
        const_layout(types.TypeResource('Example.Bus', 'Example.Bus'))


def check_rejected(data, offset, value_type, message):
    with checks.assertRaisesRegex(ValueError, message):
        read_const(data, offset, value_type, type_at)


def check_rejection():
    def ref(offset, count):
        return struct.pack('<II', offset, count)

    check_rejected(bytes(4), 1, types.uint16, 'not aligned')
    check_rejected(bytes(1), 0, types.uint16, 'extends past the end')
    check_rejected(b'\0\0\0\0\0\0\0\0\0\0\0\x01', 0, types.TypeOption(types.String), 'tail padding')
    check_rejected(b'\x02', 0, types.boolean, 'Invalid boolean')
    check_rejected(b'\x02', 0, types.TypeOption(types.unit), 'presence byte')
    check_rejected(b'\x01\x00', 0, types.TypeOption(types.uint8), 'payload must be zero')
    mode = types.TypeEnum('Mode', {'Off': 0, 'On': 1}, is_open=False, underlying_type=types.uint8)
    check_rejected(b'\x02', 0, mode, 'member number')
    pair = types.TypeStruct('Pair', {'a': types.uint8, 'b': types.uint16})
    check_rejected(b'\x01\x01\x02\x00', 0, pair, 'Struct padding')

    check_rejected(ref(4, 0), 0, types.String, 'empty string')
    check_rejected(ref(8, 4), 0, types.String, 'String at offset 8')
    check_rejected(b'\xff\xfe' + bytes(6) + ref(0, 2), 8, types.String, 'Invalid UTF-8')
    check_rejected(ref(4, 0), 0, types.TypeList(types.uint8), 'empty list')
    check_rejected(bytes(4) + ref(1, 1), 4, types.TypeList(types.uint16), 'not aligned')
    check_rejected(ref(0, 3), 0, types.TypeList(types.uint32), 'List at offset 0')
    check_rejected(b'\x01\x00\x01\x05' + ref(0, 1), 4, types.TypeList(types.TypeOption(types.uint16)),
                   'element tail padding')
    check_rejected(struct.pack('<I', 5), 0, types.ValueType, 'Unknown type 5')


check_round_trips()
check_layout()
check_sharing()
check_rejection()


async def run_test():
    pass
