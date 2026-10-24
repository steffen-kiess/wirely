import struct

from wirely.program import pack_record, record_size, unpack_record
from wirely.program.generated.format import (
    ClosedEnumType, CompositeType, Header, Input, IntegerType, MonotonicTime, OpenEnumType, OptionalRef, Output,
    Procedure, Range, Ref, ResourceType, RunStage, SectionEntry, SimpleType, Stage, StructType, TypeKind, VariableBank,
)


def check_rejected(record_class, data, message, offset=0):
    try:
        unpack_record(record_class, data, offset)
    except ValueError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError(f'Expected rejection: {message}')


def check_layout():
    # The header and directory entries match the container's encoding.
    header = Header(0x4c5257ff, 0, 0, 16, 0)
    assert pack_record(header) == struct.pack('<IHHII', 0x4c5257ff, 0, 0, 16, 0)
    assert unpack_record(Header, pack_record(header)) == header
    assert record_size(SectionEntry) == 16
    assert pack_record(SectionEntry(32, 3, 1, 2)) == struct.pack('<IIII', 32, 3, 1, 2)

    # Two single-byte fields align the following uint16 without padding.
    assert pack_record(IntegerType(TypeKind.Integer, True, 32)) == bytes([3, 1, 32, 0])
    assert record_size(IntegerType) == 4
    assert unpack_record(IntegerType, bytes([3, 0, 0, 1])) == IntegerType(TypeKind.Integer, False, 256)

    # Nested records round-trip as nested NamedTuples.
    value = Range(Ref(3), 4)
    assert pack_record(value) == struct.pack('<II', 3, 4)
    assert unpack_record(Range, pack_record(value)) == value
    assert type(unpack_record(Range, pack_record(value)).first) is Ref
    procedure = Procedure(OptionalRef(0xffffffff), OptionalRef(2), Range(Ref(3), 4), Range(Ref(5), 6))
    assert record_size(Procedure) == 24
    assert unpack_record(Procedure, pack_record(procedure)) == procedure

    # The storage size includes zero tail padding.
    time = MonotonicTime(1 << 100, 7)
    assert record_size(MonotonicTime) == 32
    data = pack_record(time)
    assert len(data) == 32 and data[24:] == bytes(8)
    assert unpack_record(MonotonicTime, data) == time

    # Enum fields unpack as enum members, including unnamed numbers of open enums.
    composite = CompositeType(TypeKind.Option, Ref(2))
    assert pack_record(composite) == bytes([10, 0, 0, 0, 2, 0, 0, 0])
    kind = unpack_record(CompositeType, pack_record(composite)).kind
    assert type(kind) is TypeKind and kind is TypeKind.Option
    assert pack_record(CompositeType(10, Ref(2))) == pack_record(composite)
    unknown = unpack_record(CompositeType, bytes([200]) + bytes(7)).kind
    assert type(unknown) is TypeKind and unknown == 200 and unknown.name is None

    stage = Stage(RunStage.Output, Range(Ref(2), 3))
    assert pack_record(stage) == struct.pack('<BxxxII', 4, 2, 3)
    assert type(unpack_record(Stage, pack_record(stage)).stage) is RunStage

    output = Output(VariableBank.NewState, Ref(1))
    assert pack_record(output) == struct.pack('<BxxxI', 5, 1)
    assert type(unpack_record(Output, pack_record(output)).bank) is VariableBank

    # Records are read at an offset into a larger buffer.
    table = pack_record(Ref(1)) + pack_record(Ref(2)) + pack_record(Ref(3))
    assert [unpack_record(Ref, table, offset) for offset in (0, 4, 8)] == [Ref(1), Ref(2), Ref(3)]


def check_type_record_sizes():
    # Each record selected by TypeKind fits a 32-byte ValueTypes entry.
    for record_class in (SimpleType, IntegerType, ClosedEnumType, OpenEnumType, StructType, ResourceType,
                         CompositeType):
        assert record_size(record_class) <= 32, record_class.__name__


def check_invalid_records():
    check_rejected(IntegerType, bytes([3, 2, 32, 0]), 'Invalid boolean byte 2')
    # Input has padding between Bank (offset 0) and Variable (offset 4).
    data = bytearray(pack_record(Input(VariableBank.ConstValues, Ref(2))))
    data[1] = 1
    check_rejected(Input, bytes(data), 'padding must be zero')
    data = bytearray(pack_record(MonotonicTime(0, 0)))
    data[30] = 1
    check_rejected(MonotonicTime, bytes(data), 'tail padding must be zero')
    check_rejected(Range, bytes(7), 'past the end')
    check_rejected(Ref, bytes(8), 'past the end', offset=6)
    try:
        pack_record(Ref(1 << 32))
    except ValueError as error:
        assert 'out of range' in str(error), str(error)
    else:
        raise AssertionError('Expected an out-of-range field to be rejected')


check_layout()
check_type_record_sizes()
check_invalid_records()


async def run_test():
    pass
