import enum
import struct
from unittest import TestCase

from wirely.program import types
from wirely.program.records import pack_record, record_size, unpack_record
from wirely.program.generated.format import (
    ClosedEnumType, CompositeType, Header, IntegerType, MonotonicTime, OpenEnumType, OptionalRef,
    ProcedureInstance, Range, Ref, ResourceType, RunStage, SectionEntry, SimpleType, Stage, StructType, TypeKind,
    VariableBank, VariableRef, enum_types, record_types,
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
    # The bytecode reference precedes the native reference.
    procedure = ProcedureInstance(bytecode=OptionalRef(2), native=OptionalRef(0xffffffff),
                                  inputs=Range(Ref(3), 4), outputs=Range(Ref(5), 6))
    assert record_size(ProcedureInstance) == 24
    assert pack_record(procedure) == struct.pack('<IIIIII', 2, 0xffffffff, 3, 4, 5, 6)
    assert unpack_record(ProcedureInstance, pack_record(procedure)) == procedure

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

    output = VariableRef(VariableBank.NewState, Ref(1))
    assert pack_record(output) == struct.pack('<BxxxI', 5, 1)
    assert type(unpack_record(VariableRef, pack_record(output)).bank) is VariableBank

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
    # VariableRef has padding between Bank (offset 0) and Variable (offset 4).
    data = bytearray(pack_record(VariableRef(VariableBank.ConstValues, Ref(2))))
    data[1] = 1
    check_rejected(VariableRef, bytes(data), 'padding must be zero')
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


def check_generated_schemas():
    record_classes = {value_type.name: cls for cls, value_type in record_types.items()}
    enum_classes = {value_type.name: cls for cls, value_type in enum_types.items()}

    def value(value_type, variant, named):
        if isinstance(value_type, types.TypeStruct):
            fields = tuple(value(field_type, variant, named) for field_type in value_type.field_types)
            return record_classes[value_type.name](*fields) if named else fields
        if isinstance(value_type, types.TypeEnum):
            number = value(value_type.underlying_type, variant, False)
            return enum_classes[value_type.name](number) if named else number
        if isinstance(value_type, types.TypeBoolean):
            return bool(variant % 2)
        assert isinstance(value_type, types.TypeInt)
        return (value_type.min_value, value_type.max_value, value_type.max_value // 3)[variant]

    def padding(value_type, offset=0):
        end = offset
        for field_type in value_type.field_types:
            start = (end + field_type.alignment - 1) & ~(field_type.alignment - 1)
            yield from range(end, start)
            if isinstance(field_type, types.TypeStruct):
                yield from padding(field_type, start)
            end = start + field_type.size
        yield from range(end, offset + value_type.size)

    def check_classes(value_type, decoded):
        assert type(decoded) is record_classes[value_type.name]
        for field_type, field in zip(value_type.field_types, decoded):
            if isinstance(field_type, types.TypeStruct):
                check_classes(field_type, field)
            elif isinstance(field_type, types.TypeEnum):
                assert type(field) is enum_classes[field_type.name]

    # Compare every fixed schema to the general Wirely serializer, including
    # uint128 extrema, unnamed enum values, and structural nested tuples.
    for cls, value_type in record_types.items():
        for variant in range(3):
            plain = value(value_type, variant, False)
            reference = value_type.serialize(plain)
            expected = reference + bytes(value_type.size - len(reference))
            record = value(value_type, variant, True)
            assert pack_record(record) == expected
            assert pack_record(cls(*plain)) == expected
            decoded = unpack_record(cls, memoryview(b'prefix' + expected), 6)
            assert decoded == record
            check_classes(value_type, decoded)
            assert value_type.deserialize(reference) == plain
        for position in padding(value_type):
            damaged = bytearray(expected)
            damaged[position] = 1
            check_rejected(cls, damaged, 'padding must be zero')
        check_rejected(cls, expected, 'past the end', -1)
        check_rejected(cls, expected[:-1], 'past the end')


def check_invalid_values():
    checks = TestCase()
    for invalid in (-1, 1 << 32, True, 1.0, '1', None):
        with checks.assertRaises(ValueError):
            pack_record(Ref(invalid))
    for invalid in ((), (1, 2), [1], None):
        with checks.assertRaises(ValueError):
            pack_record(Range(invalid, 0))
    for invalid in (1, None, enum.IntEnum('Flag', {'True': 1})(1)):
        with checks.assertRaises(Exception):
            pack_record(IntegerType(TypeKind.Integer, invalid, 32))
    # IntEnums are also accepted in ordinary integer fields.
    assert pack_record(Ref(TypeKind.Integer)) == pack_record(Ref(int(TypeKind.Integer)))


check_layout()
check_type_record_sizes()
check_invalid_records()
check_generated_schemas()
check_invalid_values()


async def run_test():
    from wirely.compiler.compilation import compile_strs
    from wirely.compiler.native_contracts import generate_format_records

    graph = compile_strs([('<json-records>', '''
#[JSON]
closed enum type Mode {
  #[JSON.Name = "heat"] Heat,
}
closed enum type Plain { Off, }
#[JSON]
struct type Record {
  #[JSON.Name = "mode"] Mode: Mode,
}
struct type Empty { #![JSON] }
struct type PlainRecord { Value: uint8, }
''')], add_std=False)
    assert graph is not None
    namespace = {'__name__': '_json_records'}
    exec(generate_format_records(graph), namespace)
    mode = namespace['enum_types'][namespace['Mode']]
    assert mode.is_json_serializable and mode.json_encode(0, None) == 'heat'
    assert not namespace['enum_types'][namespace['Plain']].is_json_serializable
    records = namespace['record_types']
    assert records[namespace['Record']].json_names == ('mode',)
    assert records[namespace['Empty']].json_names == ()
    assert records[namespace['PlainRecord']].json_names is None
