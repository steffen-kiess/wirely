# Wirely program binary format

This format represents `Program` definitions as indexed binary tables
for desktop and embedded runtimes. Program files normally use the `.wbc`
extension. The current format version is 0.0, an unstable development
format.

## File identification

The first eight bytes of a file are `Magic` (`uint32`), `VersionMajor`
(`uint16`), and `VersionMinor` (`uint16`), in that order and always
little-endian. `Magic` is `0x4c5257ff`, the bytes `ff 57 52 4c` (`0xff`,
then "WRL"). The byte `0xff` never occurs in UTF-8, so a program file
cannot be mistaken for a `.wrl` source file.
For an unknown major version, only these fields can be interpreted; the
rest of the file may have a different layout or endianness.

A major-version change is breaking. Older consumers can inspect understood
resources in a newer minor version, but may miss new sections containing
critical information or encounter unknown types and opcodes. Partial
inspection does not establish full parsing or execution support. Execution
requires support for the file's major and minor version; runtimes reject
newer, unsupported minor versions.

The rest of this document describes version 0.0.

## Header and sections

```wirely
// Identifies the image and locates its section directory.
// File offset 0.
struct type Header {
  // 0x4c5257ff; bytes ff 57 52 4c (0xff, then "WRL").
  Magic: uint32,
  // Breaking format version; currently 0.
  VersionMajor: uint16,
  // Format revision within the major version; currently 0.
  VersionMinor: uint16,
  // Total file length in bytes.
  FileLength: uint32,
  // Number of immediately following directory entries.
  SectionCount: uint32,
}
```

```wirely
// Locates one section and describes its contents.
// Directory immediately follows Header.
struct type SectionEntry {
  // Absolute byte offset of the section from file start.
  Offset: uint32,
  // Section byte length, including internal padding.
  Length: uint32,
  // Section's purpose, identified by name below.
  SectionType: uint32,
  // Layout of the section's records or bytes.
  RecordType: uint32,
}
```

The directory contains `header.SectionCount` consecutive entries with
strictly ascending `SectionType`, so each section type occurs at most once.
Sections occupy disjoint byte ranges starting on 16-byte boundaries. Their
offsets locate bytes independently of directory order. Section type zero
is invalid. Transition section IDs are not assigned yet.

Each section holds a table, a record, or a buffer, depending on its
layout. A table contains consecutive records of its record type, so its
length is an exact multiple of the record size. A record section contains
one record with its storage size. A buffer contains `Length` bytes of
values, strings, or instruction streams. Empty tables and buffers have
length zero. Padding between sections is outside their lengths.
Buffers use record type `Buffer` (0).

Consumers ignore unknown section types. Structured records are always
stored in tables or record sections, so a known `RecordType` lets
inspection tools display records even when the section's purpose is
unknown. Resolving section-specific references requires knowledge of that
section's semantics.

`wirely objdump -h FILE` lists the sections of a file, and `-s` dumps
their contents.

| Section | Record type | Layout |
| --- | --- | --- |
| [`BuildIDSHA512`](#buildidsha512) | `Buffer` | Buffer |
| [`NativeNames`](#nativenames) | `Buffer` | Buffer |
| [`ValueTypes`](#valuetypes) | `TypeRecord` | Table |
| [`TypeLists`](#typelists) | `TypeEntry` | Table |
| [`ProgramData`](#programdata) | `Buffer` | Buffer |
| [`ConstValues`](#constvalues) | `Variable` | Table |
| [`FixedValues`](#fixedvalues-statevariables-and-temporaries) | `Variable` | Table |
| [`Temporaries`](#fixedvalues-statevariables-and-temporaries) | `Variable` | Table |
| [`StateVariables`](#fixedvalues-statevariables-and-temporaries) | `Variable` | Table |
| [`InitialState`](#initialstate) | `Buffer` | Buffer |
| [`NativeFunctions`](#nativefunctions) | `NativeFunction` | Table |
| [`ProcedureCode`](#procedurecode) | `Buffer` | Buffer |
| [`BytecodeProcedures`](#bytecodeprocedures) | `BytecodeProcedure` | Table |
| [`InterfaceNames`](#interfacenames) | `NameRef` | Table |
| [`NativeProcedures`](#nativeprocedures) | `NativeProcedure` | Table |
| [`Bindings`](#bindings) | `VariableRef` | Table |
| [`ProcedureInstances`](#procedureinstances) | `ProcedureInstance` | Table |
| [`Stages`](#stages) | `Stage` | Table |
| [`JsonData`](#jsondata) | `Buffer` | Buffer |
| [`JsonNames`](#jsontypes-and-jsonnames) | `JsonName` | Table |
| [`JsonTypes`](#jsontypes-and-jsonnames) | `JsonType` | Table |
| [`SymbolData`](#symboldata) | `Buffer` | Buffer |
| [`SymbolNames`](#symbolnames) | `SymbolName` | Table |
| [`TypeSymbols`](#typesymbols) | `TypeSymbol` | Table |
| [`StateVariableSymbols`](#statevariablesymbols-and-proceduresymbols) | `IndexedSymbol` | Table |
| [`ProcedureSymbols`](#statevariablesymbols-and-proceduresymbols) | `IndexedSymbol` | Table |
| [`VariableSymbolAliases`](#variablesymbolaliases) | `VariableSymbolAlias` | Table |

```wirely
// The purpose of a section, in SectionEntry.SectionType. 0 is not a valid
// section type.
open enum type SectionType: uint32 {
  BuildIDSHA512 = 1,
  NativeNames = 2,
  ValueTypes = 3,
  TypeLists = 4,
  ProgramData = 5,
  ConstValues = 6,
  FixedValues = 7,
  Temporaries = 8,
  StateVariables = 9,
  InitialState = 10,
  NativeFunctions = 11,
  ProcedureCode = 12,
  BytecodeProcedures = 13,
  InterfaceNames = 14,
  NativeProcedures = 15,
  Bindings = 16,
  ProcedureInstances = 17,
  Stages = 18,
  JsonData = 19,
  JsonNames = 20,
  JsonTypes = 21,
  SymbolData = 0x10000,
  SymbolNames = 0x10001,
  TypeSymbols = 0x10002,
  StateVariableSymbols = 0x10003,
  ProcedureSymbols = 0x10004,
  VariableSymbolAliases = 0x10005,
}
```

The sections from `SymbolData` on are
[symbol sections](#symbol-sections); each of them is optional. All other
sections are present.

Record type IDs identify section layouts. IDs for records used only by
transitions are not assigned yet.

```wirely
// The layout of a section's contents, in SectionEntry.RecordType.
open enum type RecordType: uint32 {
  Buffer = 0,
  TypeRecord = 1,
  TypeEntry = 2,
  Variable = 3,
  NativeFunction = 4,
  BytecodeProcedure = 5,
  NameRef = 6,
  NativeProcedure = 7,
  VariableRef = 8,
  ProcedureInstance = 9,
  Stage = 10,
  JsonName = 11,
  JsonType = 12,
  SymbolName = 13,
  TypeSymbol = 14,
  IndexedSymbol = 15,
  VariableSymbolAlias = 16,
}
```

A known section with a record type other than its listed one fails
verification. This lets a later format allow a second record type for a
section, such as an extended record: files that use the extension become
unreadable for older runtimes, while files that don't remain compatible.

### BuildIDSHA512

`BuildIDSHA512` contains the 64-byte SHA-512 digest of the entire
unstripped file, including its header, directory, symbol sections, and
padding, computed with the digest bytes set to zero.

Stripping removes symbol sections and updates the directory, section
locations, and file length while retaining this original build ID. It
associates the stripped file with the original file and its symbols.
The digest is verified against the unstripped file with the ID bytes
zeroed.

## Common encodings

Records are Wirely structs in the
[runtime binary representation](value-types.md#runtime-binary-representation).
`wirely/program/format.wrl` declares them, and `tools/generate-native-types`
generates their Python classes.
Tables use each record's storage size as their stride, except
`ValueTypes`, whose entries are 32 bytes.

Table indices are numbered from zero and local to the file. Valid indices
range from zero through `0xfffffffe`. Lengths, counts, and byte offsets use
the full `uint32` range; files fit within that address space.

### Ref and OptionalRef

```wirely
// An entry in the table named by the referring field's comment.
struct type Ref {
  Value: uint32,
}

// An entry in the table named at use, or 0xffffffff for no entry.
struct type OptionalRef {
  Value: uint32,
}
```

### Range

```wirely
// A consecutive slice of the table named by the referring field's comment.
struct type Range {
  // First target-table entry; zero when Count is zero.
  First: Ref,
  // Number of consecutive target entries.
  Count: uint32,
}
```

Ranges fit their target tables. A record cannot contain a variable-length
list, so lists are stored as consecutive entries of a table and referred to
by a range.

### ByteRange

```wirely
// Bytes in the buffer named by the referring field's comment.
struct type ByteRange {
  // Byte offset into the buffer; zero when Length is zero.
  Offset: uint32,
  // Number of bytes starting at Offset.
  Length: uint32,
}
```

### StringRef and OptionalStringRef

```wirely
// UTF-8 bytes in the buffer named by the referring field's comment.
struct type StringRef {
  // Byte offset into the buffer.
  Offset: uint32,
  // Number of UTF-8 bytes starting at Offset.
  LengthBytes: uint32,
}

// UTF-8 bytes in the buffer named at use, or no string.
struct type OptionalStringRef {
  // Byte offset into the buffer, or 0xffffffff for no string.
  Offset: uint32,
  // Number of UTF-8 bytes starting at Offset, or 0xffffffff for no string.
  LengthBytes: uint32,
}
```

Strings are stored in four buffers: `NativeNames` holds names for linking,
`JsonData` holds JSON names, `ProgramData` holds const string values, and
`SymbolData` holds symbols and aliases. A string occupies exactly
`[Offset, Offset + LengthBytes)` in its buffer and is valid UTF-8
(RFC 3629). An empty string has both fields zero. Equal strings can share
bytes. An absent `OptionalStringRef` has both fields `0xffffffff`; a
present one is a string like a `StringRef`.

A qualified name is one string whose components are joined by `.`, such as
`ABB.JRA.Shutter`.

## Types

### ValueTypes

Each `ValueTypes` entry is a `TypeRecord`: one of the records below,
selected by its first byte (`Kind`). Every entry occupies 32 bytes; the
bytes after the record's fields are zero. Runtime value sizes and
alignments are determined from this table.

```wirely
// Selects the record of a ValueTypes entry and the type it represents.
// 0 is not a valid kind.
open enum type TypeKind: uint8 {
  Unit = 1,
  Boolean = 2,
  Integer = 3,
  String = 4,
  Type = 5,
  ClosedEnum = 6,
  OpenEnum = 7,
  Struct = 8,
  Resource = 9,
  Option = 10,
  List = 11,
}
```

```wirely
// Kinds Unit, Boolean, String, and Type.
struct type SimpleType {
  // Type kind.
  Kind: TypeKind,
}
```

```wirely
// Kind Integer.
struct type IntegerType {
  // Integer kind.
  Kind: TypeKind,
  // Whether values are signed, in two's complement.
  Signed: boolean,
  // Width: 8, 16, 32, 64, or 128.
  Bits: uint16,
}
```

```wirely
// Kind ClosedEnum: a nonempty, densely numbered enumeration.
struct type ClosedEnumType {
  Kind: TypeKind,
  // Index into ValueTypes; uint8, uint16, or uint32, fitting all members.
  UnderlyingType: Ref,
  // Number of members; member N has number N.
  MemberCount: uint32,
  // Native name in NativeNames, or none if the enum is not native.
  NativeName: OptionalStringRef,
}
```

```wirely
// Kind OpenEnum: an enumeration accepting every value of an integer type.
struct type OpenEnumType {
  Kind: TypeKind,
  // Index into ValueTypes; underlying integer type.
  UnderlyingType: Ref,
  // Native name in NativeNames, or none if the enum is not native.
  NativeName: OptionalStringRef,
}
```

```wirely
// Kind Struct: a named struct.
struct type StructType {
  // Struct kind.
  Kind: TypeKind,
  // Whether the runtime implements the struct; field access instructions
  // are invalid for it.
  Opaque: boolean,
  // Range into TypeLists; field types in declaration order.
  Fields: Range,
  // Native name in NativeNames, or none if the struct is neither native
  // nor opaque.
  NativeName: OptionalStringRef,
}
```

```wirely
// Kind Resource: a handle to a native object; a load-time type only.
struct type ResourceType {
  // Resource kind.
  Kind: TypeKind,
  // Native name in NativeNames, such as KNX.Bus.
  NativeName: StringRef,
}
```

```wirely
// Kinds Option and List: a type containing values of another type.
struct type CompositeType {
  // Option kind, or List kind for const values.
  Kind: TypeKind,
  // Index into ValueTypes.
  InnerType: Ref,
}
```

`Kind` identifies the record layout and represented type:

| Kind | Record |
| --- | --- |
| `Unit`, `Boolean`, `String`, `Type` | `SimpleType` |
| `Integer` | `IntegerType` |
| `ClosedEnum` | `ClosedEnumType` |
| `OpenEnum` | `OpenEnumType` |
| `Struct` | `StructType` |
| `Resource` | `ResourceType` |
| `Option`, `List` | `CompositeType` |

Both enum kinds explicitly store their underlying integer type and default
to zero. Closed enums accept numbers from zero through `MemberCount - 1`;
open enums accept every underlying value. Field index N selects entry N of
a struct's `Fields` range. Runtime field offsets follow the
[runtime binary representation](value-types.md#runtime-binary-representation);
const-only field offsets follow
[ProgramData](#programdata).

Types are equal exactly when their indices are equal. Enums, structs, and
resource types have one entry per declaration. Since verified files never
mix two entries where one type is required, readers may treat structurally
equal entries of other types as the same type. Inner, underlying, and field
types occur before the types using them, making size calculation possible
in one forward pass.

A resource is a handle to a native object, such as a KNX connection. It
has storage size zero and alignment 1; the runtime keeps the native object
separately. No option, list, or struct contains a resource. `string`,
`type`, and lists, including composites and structs containing them, are
only used for const values. Other types are runtime types within the size,
depth, and member-count limits in [Value types](value-types.md#type-overview);
composites exceeding those limits are const-only. Runtime types and
resources are load-time types.

### Opaque structs

An opaque struct is implemented by the runtime, which accepts only some of
its values and gives them meaning. Its values use the struct
representation, and its default is the all-zero value. Opaque structs have
a `NativeName`; opaque and non-opaque structs have separate native-name
namespaces. They [link](#linking) by native name and field types.

The standard libraries declare the time types as opaque structs with the
fields below, in order. Each has its qualified name as `NativeName`.

```wirely
// Non-negative elapsed time. Opaque struct Std.Duration.
struct type Duration {
  // Ticks of 2^-64 s.
  Ticks: uint128,
}
```

```wirely
// Time within a day. Opaque struct Schedule.TimeOfDay.
struct type TimeOfDay {
  // Ticks of 2^-64 s since midnight; at most 24 hours (86400 * 2^64).
  Ticks: uint128,
}
```

```wirely
// A monotonic clock reading. Opaque struct Timer.MonotonicTime.
struct type MonotonicTime {
  // Ticks of 2^-64 s.
  Ticks: uint128,
  // Clock generation; generation 0 is never current.
  Generation: uint64,
}
```

The values of these types are described in [Value types](value-types.md);
`Std.Duration` and `Schedule.TimeOfDay` are ordered.

### TypeLists

```wirely
// One type in a list of types. TypeLists table.
struct type TypeEntry {
  // Index into ValueTypes.
  ValueType: Ref,
}
```

`TypeLists` stores lists of types, such as the field types of a struct, the
parameter types of a native function, or the locals of a bytecode
procedure, as ranges of entries. Equal lists can share entries.

### JsonTypes and JsonNames

```wirely
// Serialization names of the members of one enum or the fields of one
// struct. JsonTypes table.
struct type JsonType {
  // Index into ValueTypes; an enum or struct type.
  Type: Ref,
  // Number of names: one per member of a closed enum or field of a
  // struct, or one per named number of an open enum.
  Count: uint32,
  // Index into JsonNames of the first of Count consecutive names; zero
  // when Count is zero.
  Names: Ref,
  // Byte offset into JsonData of the Count named numbers of an open enum,
  // in ascending order; zero when Count is zero. 0xffffffff for other
  // types.
  Numbers: uint32,
}
```

```wirely
// One JSON name of a member or field. JsonNames table.
struct type JsonName {
  // UTF-8 name in JsonData.
  Name: StringRef,
}
```

`JsonTypes` is sorted by `Type`, without duplicates. Only enum and struct
types with an entry convert to and from JSON. Name N of a closed enum names
member N, and name N of a struct names field N. Name N of an open enum
names element N of its `Numbers` array. JSON names can differ from declared
names.

### JsonData

`JsonData` contains the UTF-8 bytes of JSON names and the named-number
arrays of `JsonTypes`. The numbers occupy
`[Numbers, Numbers + Count * storage_size(UnderlyingType))` in the
underlying type's runtime binary representation. `Numbers` is aligned for
the underlying type. Equal strings and arrays can share bytes.

## Const data

### ConstValues

```wirely
// A typed value with a byte offset into its associated storage.
// ConstValues, FixedValues, StateVariables, and Temporaries tables.
struct type Variable {
  // Index into ValueTypes; resolved stored-value type.
  ValueType: Ref,
  // Byte offset into ProgramData for ConstValues, the fixed buffer for
  // FixedValues, the state buffer for StateVariables, or the temporary
  // buffer for Temporaries.
  Offset: uint32,
}
```

Const values describe constants read by bytecode procedures and the values
bound to procedure inputs, such as parameters, constants, and compile-time
expressions read by native procedures. `const` operands and const inputs
reference these entries, which can be shared for equal typed values. Their
data and referenced payloads are stored in `ProgramData`. Only values used
in one of these ways are stored; node parameters, constants, and
expressions supply those values.

### ProgramData

`ProgramData` stores const value slots, list arrays, and string payloads.
Payloads use their type's storage layout. Equal values or payloads can
share storage.

Runtime types use their runtime binary representation. Const `string`
values use `StringRef` into `ProgramData`; const `type` and list values use
the records below.

```wirely
// A const value identifying another value type.
struct type ValueTypeValue {
  // Index into ValueTypes.
  ValueType: Ref,
}
```

```wirely
// A const list identifying element slots in ProgramData.
struct type ListValue {
  // Byte offset into ProgramData; first element.
  Offset: uint32,
  // Number of consecutive elements, including units.
  ElementCount: uint32,
}
```

Const structs and options apply the runtime field and option layout rules
recursively to these const representations. Data size, alignment, storage
size, and zero padding follow the same rules, including for composites
that exceed runtime-type limits. Const slots are aligned for their types.

For `List[T]`, `CompositeType.InnerType` selects `T` from `ValueTypes`.
The list occupies `[Offset, Offset + ElementCount * storage_size(T))`
in `ProgramData`, using `T`'s const storage layout, with `Offset` aligned
for `T`. An empty list has both fields zero. Zero-sized units retain their
element count.
Nested list elements contain `ListValue` offset/count pairs into the same
buffer.

## Variables

### FixedValues, StateVariables, and Temporaries

`FixedValues`, `StateVariables`, and `Temporaries` use the `Variable`
record. Their offsets locate values in the runtime's fixed buffer, state
buffer, and temporary buffer. The load stage writes the fixed buffer; the
file contains no data for it.

Entries are ordered by ascending byte offset, preserving runtime layout
order for equal offsets. Each variable identity retains its own entry,
including zero-sized units and resources sharing an offset.

`FixedValues` uses load-time types; the other two tables use runtime types.
Each variable occupies its type's storage size at an offset aligned for
its type, starting at or after the end of the previous slot. The buffer
alignment is the maximum type alignment, and its size rounds the last
slot's end up to that alignment. Empty buffers have size zero and
alignment 1. Slot and final buffer padding are zero. The runtime allocates
fixed, state, and temporary buffers using this layout. Producers lay out
variables in decreasing type alignment, preserving variable order within
an alignment group.

### InitialState

`InitialState` has the same byte layout and size as the runtime state
buffer. It stores every state variable's initial data bytes at its declared
offset, with zero slot and final buffer padding. The runtime can copy this
buffer directly into its state buffer. A program started by a
[state transition](#state-transitions) receives its state from the
transition instead. An empty buffer has length zero.

## Execution

### Stages

```wirely
// An execution stage. 0 is not a valid stage.
open enum type RunStage: uint8 {
  Load = 1,
  Input = 2,
  Default = 3,
  Output = 4,
}
```

```wirely
// An execution stage and its ordered procedure range. Stages table.
struct type Stage {
  // Stage these procedures run in.
  Stage: RunStage,
  // Range into ProcedureInstances, in execution order.
  Schedule: Range,
}
```

`Stages` contains exactly four records, in order: `Load`, `Input`,
`Default`, and `Output`, including empty stages. Their nonempty schedule
ranges partition `ProcedureInstances` in execution order. Empty stages
have a zero-count range with `First` zero.

The `Load` stage runs once when a program is loaded, before its first
step. Its procedures, the load procedures, always run. The other stages
run in every step; their procedures are runtime procedures.

Load procedures may fail, which makes loading fail. Preparing runtime
native procedures is part of loading and may also fail; preparation has
no side effects and does not use the resources it receives. Loading a new
program leaves the running program unchanged until loading succeeds.
Running the steps of runtime procedures and state transitions cannot
fail.

Whether loading succeeds is deterministic. Verification, linking, load
procedures, and preparation depend only on the program and the runtime,
not on the environment, and in a transition not on the transition, the
old program, or its state. A program that loaded once therefore also
loads after a restart.

The runtime is what stays the same when it restarts, for example after a
reboot or power loss: its implementation, the native code it links
against with that code's dependencies, and whatever it provides as part
of itself, such as a built-in KNX interface or GPIO lines. Anything else
is the environment, which may change across restarts, such as network
peers, files a program writes, and time. Native objects created during
loading do not access the environment. The runtime activates them after
loading succeeds. Activation and later operation cannot fail; native code
handles an unavailable environment itself. If loading fails, the runtime
discards the native objects created so far.

### ProcedureInstances

```wirely
// A scheduled instance of a native or bytecode procedure with its bindings.
// ProcedureInstances table.
struct type ProcedureInstance {
  // Index into BytecodeProcedures, or no entry for a native procedure.
  Bytecode: OptionalRef,
  // Index into NativeProcedures, or no entry for a bytecode procedure.
  Native: OptionalRef,
  // Range into Bindings; sources of the procedure's inputs.
  Inputs: Range,
  // Range into Bindings; destinations of the procedure's outputs.
  Outputs: Range,
}
```

Procedure instances are stored in execution order, consecutively within
each stage. Each instance refers to either a native or a bytecode
procedure.

Each instance's bindings determine its input and output types, and its
stage comes from the containing schedule. Native and bytecode procedures
can each be shared by instances with different types or stages, provided
each use is compatible with the implementation.

The native procedure identifies the native implementation and its
interfaces. Native load procedures can create native objects and output
them as resources. Other procedures reach native objects only through
their resource inputs. Procedure instances retain distinct entries even
when they invoke the same native procedure.

Native code can request that a runtime native procedure executes, for
example through a wakeup; the mechanism is part of the runtime's native
interface.

A bytecode procedure instance executes its instruction stream. Its inputs,
outputs, and locals have runtime types. Its `Inputs` and `Outputs` ranges
have the procedure's `InputCount` and `OutputCount` entries, and operands
`input.N` and `output.N` select entry N.

Load procedures read const values and fixed values and write fixed values.
Runtime procedures write temporaries and new state. Each fixed value,
temporary, and state variable is the destination of exactly one output.
An input from fixed values, temporaries, or new state reads a variable
written by an earlier procedure.

### NativeProcedures

```wirely
// A named native procedure and its interfaces. NativeProcedures table.
struct type NativeProcedure {
  // Native procedure name in NativeNames, such as Std.Constant:run.
  Name: StringRef,
  // Range into InterfaceNames; names of the inputs.
  InputNames: Range,
  // Range into InterfaceNames; names of the outputs.
  OutputNames: Range,
  // Whether the procedure is an initializer, creating native state.
  Initializer: boolean,
}
```

Entries are sorted by UTF-8 name bytes, without duplicates.

Native state consists of the native objects created by initializers:
load procedures whose native procedure has `Initializer`, such as a KNX
connection. A [state transition](#state-transitions) can keep it. Only
load procedures use native procedures with `Initializer`. Runtime native
procedures keep no state between steps beyond what they can rebuild from
their inputs.

### InterfaceNames

```wirely
// A name in NativeNames. InterfaceNames table.
struct type NameRef {
  // UTF-8 name in NativeNames.
  Name: StringRef,
}
```

`InterfaceNames` represents the input and output names of native
procedures as ranges. Each range contains unique names in UTF-8 bytewise
lexical order.

### Bindings

```wirely
// The storage a variable reference selects. 0 is not a valid bank.
open enum type VariableBank: uint8 {
  ConstValues = 1,
  FixedValues = 2,
  Temporaries = 3,
  OldState = 4,
  NewState = 5,
}
```

```wirely
// A variable or const value bound to a procedure input or output.
// Bindings table.
struct type VariableRef {
  // Const values, fixed values, temporaries, old state, or new state.
  Bank: VariableBank,
  // Index into ConstValues, FixedValues, Temporaries, or StateVariables,
  // selected by Bank.
  Variable: Ref,
}
```

`ProcedureInstance.Inputs` and `ProcedureInstance.Outputs` select ranges
in `Bindings`.
Inputs can refer to any bank; outputs refer only to fixed values,
temporaries, or new state.

#### Inputs

For native calls, the `Inputs` range has one entry per name of the native
procedure's `InputNames` range; entry N binds input N. Optional parameter
presence requested by `optional` is represented by the bound option value.
Each value input binds exactly one source.
Bytecode procedure inputs follow read declaration order; multi value
inputs expand into scalar inputs in connection order.

An old-state input reads the step's old-state snapshot. A new-state input
reads the state value written earlier in the step. A temporary input reads
a temporary computed during the step. A fixed input reads a value written
by the load stage. A const input reads a value from `ProgramData`.

Old-state inputs compare the step's old-state snapshot with the snapshot
read by the previous step, using the variable's value-type equality.
New-state inputs compare this step's new state with the step's old state,
which the previous step produced. Temporary inputs compare this step's
values with the previous step's cached values. A difference requires
procedure execution. On the first step, procedures execute without
previous values. Const and fixed inputs never change and are not compared.
Requests from native code also require execution. A skipped procedure
preserves its state and temporary outputs.

#### Outputs

For native calls, the `Outputs` range has one entry per name of the native
procedure's `OutputNames` range; entry N binds output N.
A fixed output writes a fixed value. A new-state output writes the state
that becomes the next step's old state. A temporary output writes a
temporary.

### BytecodeProcedures

```wirely
// An instruction stream, its locals, and its interface.
// BytecodeProcedures table.
struct type BytecodeProcedure {
  // Byte range in ProcedureCode; the instruction stream.
  Code: ByteRange,
  // Range into TypeLists; local.N has the type of entry N.
  Locals: Range,
  // Number of inputs; operands input.N have N < InputCount.
  InputCount: uint32,
  // Number of outputs; operands output.N have N < OutputCount.
  OutputCount: uint32,
}
```

### NativeFunctions

```wirely
// A native function called by bytecode procedures.
// NativeFunctions table.
struct type NativeFunction {
  // Native name in NativeNames.
  Name: StringRef,
  // Range into TypeLists; parameter types in argument order.
  Parameters: Range,
  // Index into ValueTypes; runtime result type.
  ReturnType: Ref,
}
```

Entries are sorted by UTF-8 name bytes, without duplicates.

### NativeNames

`NativeNames` contains the UTF-8 names used to link native types,
procedures, and functions, and to bind native procedure inputs and
outputs by name.

### Linking

The runtime matches the qualified names of native procedures, native
functions, and resource types to native code, and binds interface names
to inputs and outputs. Native procedure names can be resolved
independently, but interface compatibility is checked for each instance:
the runtime's interface must accept its stage, the descriptor's
`Initializer` flag and interface names, and its bound input and output
types. Native inputs can accept const-only values from `ConstValues` when
the native interface allows them. Native functions link against their
declared parameter and return types.

An enum with a `NativeName` links only when the runtime knows that native
name with the same kind, underlying type, and, for a closed enum, member
count. Other enums need no linking.

A struct with a `NativeName` links only when the runtime knows that native
name, among native or opaque structs, with the same field types in the
same order; nested native enums and structs match by kind, opacity, and
native name. Other structs need no linking.

The runtime's implementation of each opaque struct also checks its values
in `ConstValues`, list elements, and `InitialState`. `Schedule.TimeOfDay`
values are at most 24 hours. The only `Timer.MonotonicTime` value in a file
is the default, because generations of a file are never current.

The reserved linking name for a native procedure with multi value/event
reads is `Namespace.NodeType:procedure[2,3,4]`. The bracketed decimal values
are member counts in read declaration order, including zero counts; scalar
and fixed reads contribute no count. Procedures without multi reads have
no bracketed suffix. Native multi value/event specialization is not
currently supported. Procedure debug symbols remain instance names such
as `somenode.procedure`.

Embedded adapters can retain numeric bindings and function pointers after
linking.

## ProcedureCode

`ProcedureCode` contains the instruction streams of bytecode procedures.
Each instruction starts with one `uint8` opcode, followed by its operands
in the order listed below. The opcode determines the operand count and
instruction length. Multi-byte integers are little endian; instructions
and operands require no alignment.

### Value operands

The written form uses lowercase operand kinds and zero-based indices, for
example `COPY local.0, input.1`.

```wirely
// The kind of a value operand, in bits 7–5 of its descriptor byte.
// Values 5–7 are not valid.
open enum type OperandKind: uint8 {
  Immediate = 0,
  Const = 1,
  Local = 2,
  Input = 3,
  Output = 4,
}
```

| Operand kind | Index selects | Access |
| --- | --- | --- |
| `immediate` | The number itself, used as the value | Read |
| `const` | Entry in `ConstValues` | Read |
| `local` | Entry in the bytecode procedure's `Locals` range | Read/write |
| `input` | Entry in the procedure's `Inputs` range | Read |
| `output` | Entry in the procedure's `Outputs` range | Read/write |

Operands identify whole typed values. There are no references. Field
indices and branch displacements are integer parameters, encoded
separately from value operands.

A value operand starts with a descriptor byte. Bits 7–5 hold its
`OperandKind`; bits 4–0 select the index encoding:

| Selector | Index encoding | Total bytes |
| --- | --- | --- |
| 0–28 | Selector is the index | 1 |
| 29 | Following `uint8` | 2 |
| 30 | Following `uint16` | 3 |
| 31 | Following `uint32` | 5 |

Indices use the shortest form: inline for 0–28, `uint8` for 29–255,
`uint16` for 256–65535, and `uint32` for larger indices. Every index is
within the selected range or table.

### Immediates

An `immediate` operand stores its value N in the index field, so
instructions can be decoded without type information. The operand has the
type the instruction requires at its position, as determined by another
operand, the called function, or the instruction itself. Shift counts have
type `uint32`, and branch conditions have type boolean; both allow
immediates. Conversion sources and `FAIL` values cannot be immediates.
For `EQUAL` and `LESS`, at most one source is an immediate; the other
source determines its type. The type is an integer, boolean, or enum type,
and N is interpreted as follows:

- An unsigned integer is N.
- A signed integer is N zigzag-decoded, `(N >> 1) ^ -(N & 1)`: N = 0, 1, 2,
  3, 4, … gives 0, −1, 1, −2, 2, …, so small values of either sign have
  short encodings.
- A boolean is N, which is 0 or 1.
- A closed enum is member N, with N below `MemberCount`.
- An open enum uses its underlying integer's interpretation of N.

Integer and open enum values fit their underlying widths.
Other values use `const` operands.

### Integer parameters

Field indices, branch displacements, function indices, and argument counts
share this unsigned encoding:

| First byte | Value encoding | Total bytes |
| --- | --- | --- |
| 0–253 | First byte is the value | 1 |
| 254 | Following `uint16` | 3 |
| 255 | Following `uint32` | 5 |

There is no extended `uint8` form. Values use the shortest form:
one byte for 0–253, `uint16` for 254–65535, and `uint32` for larger values.
Field indices select struct fields in declaration order, starting at zero.

### Instructions

Mnemonics are uppercase and name the members of `Opcode`. Instructions
with a destination place it first. Each name below denotes a value operand
except `field`, `offset`, `function`, `format`, and `count`, which denote
integer parameters.

| Instruction | Behavior |
| --- | --- |
| `COPY destination, source` | Copy the whole value, including structs and options. Both operands have the same type. |
| `SET_DEFAULT destination` | Assign the destination type's default value. |
| `GET_FIELD destination, source, field` | Copy a non-opaque struct field into a destination of the field's type. |
| `SET_FIELD destination, source, field` | Copy the source into a non-opaque struct destination's field of the same type, preserving other fields. |
| `IS_SOME destination, source` | Assign a boolean indicating whether the option is `Some`. |
| `GET_OPTION_VALUE destination, source` | Copy the value of `Some`, or assign the contained type's default for `None`. The destination has that contained type. |
| `MAKE_SOME destination, source` | Copy a value of type `T` into a destination of type `Option[T]` as `Some`. |
| `NOT destination, source` | Complement an integer within its width, or negate a boolean. |
| `AND destination, left, right` | Compute bitwise or Boolean conjunction. |
| `OR destination, left, right` | Compute bitwise or Boolean disjunction. |
| `XOR destination, left, right` | Compute bitwise or Boolean exclusive disjunction. |
| `SHIFT_LEFT destination, value, shift_count` | Shift left, discarding outgoing bits and filling with zeros. |
| `SHIFT_RIGHT destination, value, shift_count` | Shift right, extending the sign for signed integers and filling with zeros for unsigned integers. |
| `ADD_WRAPPING`, `SUB_WRAPPING`, `MUL_WRAPPING` `destination, left, right` | Compute the integer result modulo 2^bits within the operand type's range. |
| `ADD_SATURATING`, `SUB_SATURATING`, `MUL_SATURATING` `destination, left, right` | Compute the integer result clamped to the operand type's range. |
| `ADD_CHECKED`, `SUB_CHECKED`, `MUL_CHECKED`, `DIV_CHECKED`, `REM_CHECKED` `destination, left, right` | Assign `Some` with the integer result, or `None` when it is outside the operand type's range or the divisor is zero. |
| `CONVERT_WRAPPING destination, source` | Convert the source number modulo the destination type's range. |
| `CONVERT_SATURATING destination, source` | Convert the source number clamped to the destination type's range. |
| `CONVERT_CHECKED destination, source` | Assign `Some` with the source number, or `None` when it is outside the destination type's range. |
| `CALL destination, function, count, arguments…` | Call a native function with `count` argument operands and assign its result. |
| `EQUAL destination, left, right` | Compare same-type values for equality, assigning a boolean. |
| `LESS destination, left, right` | Assign whether `left` orders before `right`. Both have the same integer type. |
| `JUMP offset` | Continue at the branch target. |
| `JUMP_IF_TRUE condition, offset` | Branch when the boolean condition is true; otherwise continue with the next instruction. |
| `JUMP_IF_FALSE condition, offset` | Branch when the boolean condition is false; otherwise continue with the next instruction. |
| `RETURN` | End the procedure with its current output values. |
| `FAIL format, count, values…` | Fail loading with a diagnostic formatted from `count` values. Valid only in the `Load` stage. |

```wirely
// The first byte of an instruction. 0 is not a valid opcode, and 0xff is
// reserved for extended opcodes.
open enum type Opcode: uint8 {
  COPY = 1,
  SET_DEFAULT = 2,
  GET_FIELD = 3,
  SET_FIELD = 4,
  IS_SOME = 5,
  GET_OPTION_VALUE = 6,
  MAKE_SOME = 7,
  NOT = 8,
  AND = 9,
  OR = 10,
  XOR = 11,
  SHIFT_LEFT = 12,
  SHIFT_RIGHT = 13,
  ADD_WRAPPING = 14,
  SUB_WRAPPING = 15,
  MUL_WRAPPING = 16,
  ADD_SATURATING = 17,
  SUB_SATURATING = 18,
  MUL_SATURATING = 19,
  ADD_CHECKED = 20,
  SUB_CHECKED = 21,
  MUL_CHECKED = 22,
  DIV_CHECKED = 23,
  REM_CHECKED = 24,
  CONVERT_WRAPPING = 25,
  CONVERT_SATURATING = 26,
  CONVERT_CHECKED = 27,
  CALL = 28,
  EQUAL = 29,
  LESS = 30,
  JUMP = 31,
  JUMP_IF_TRUE = 32,
  JUMP_IF_FALSE = 33,
  RETURN = 34,
  FAIL = 35,
}
```

`NOT`, `AND`, `OR`, and `XOR` require matching integer or boolean sources
and destinations. Binary operations evaluate both sources.
For shifts, `value` and the destination share one integer type;
`shift_count` is an ordinary `uint32` value operand. Counts at least the
value's width produce zero, except that right-shifting a negative signed
value produces minus one. Counts are not masked.
Arithmetic sources share one integer type `T`. The destination has type
`T`, or `Option[T]` for `_CHECKED` instructions. Division truncates toward
zero, and the remainder has the dividend's sign.
A conversion source is an integer, enum, or boolean; an enum contributes
its member number. The destination is an integer or boolean type `T`, or
`Option[T]` for `CONVERT_CHECKED`. Booleans convert as 1-bit unsigned
integers: `false` is 0 and `true` is 1, so wrapping keeps the low bit and
saturating maps negative numbers to `false` and numbers above 1 to `true`.
Equality compares struct fields recursively; options are equal when both
are `None`, or both are `Some` with equal contained values. All unit values
are equal.

`CALL` selects an entry of `NativeFunctions`. `count` repeats the entry's
parameter count so that instructions can be decoded without the table;
they match, and the argument and destination types match the entry.
Functions are pure and return a value for every argument.

`FAIL` selects a `ConstValues` entry with the integer parameter `format`.
The entry has type `List[string]` and contains exactly `2 * count + 1`
strings: a literal, then a format specification and the next literal for
each value operand. An empty specification uses the value type's text
format. Only empty specifications are supported. Literal strings have no
format syntax; braces in them are ordinary text. Each value operand has a
runtime type and cannot be an immediate, since no other operand determines
its type. Decoding reads `count` value operands without consulting the
constant table or interpreting format specifications.

Executing `FAIL` renders the diagnostic by alternating literals and values,
then stops loading the candidate program. It publishes no outputs from the
failed invocation. Diagnostics identify the procedure and instruction,
with node and source information when available. A malformed format list
or an unsupported specification is a verification failure, including in
unreachable instructions.

Ordering comparisons of `Std.Duration` and `Schedule.TimeOfDay` use native
functions `Std.Duration.Less` and `Schedule.TimeOfDay.Less`, respectively.

Each executed bytecode invocation initializes every local and output to
its type's default. For unit this is the unit value, for boolean false,
for integers zero, for options `None`, for enums zero, and for structs,
including opaque ones, the defaults of their fields. The default
`Timer.MonotonicTime` has
generation 0, which is never current, so it compares to no current time.
Values remain valid and canonical, including padding and unused option
payloads, as specified by the runtime binary representation.

Skipped instances retain their outputs. Writes to new state leave the
old-state snapshot unchanged.

Instructions read all source values before modifying their destination,
allowing a destination also to be a source. `COPY` preserves its source
unless it is also the destination. Struct and option copies are whole-value
operations subject to the runtime type size and nesting limits.

### Control flow

A branch target is the byte offset immediately after the complete branch
instruction plus its unsigned displacement. A zero displacement targets
the next instruction; every branch moves forward from its own instruction.
The target must be an instruction start inside the stream. The position
immediately after the stream is not a target: for a 120-byte stream, offset
119 is valid only if an instruction starts there, and offset 120 is invalid.

The stream is nonempty and its final instruction is `RETURN` or `FAIL`.
Either instruction ends execution of the stream and can also appear
earlier. There are no bytecode procedure calls or backward branches.

Target calculation must not overflow. Every byte of the stream belongs to
a decoded instruction; unknown instructions cannot be skipped. Default
initialization removes the need to track which locals or outputs have been
assigned.

## Symbol sections

Symbols provide declared identities for state variables, procedure
instances, structs, and enums, and source access names for values. All
symbol sections are optional. Ordinary records never reference them.
Symbol tables reference ordinary tables and the `SymbolData` and
`SymbolNames` pools, but do not depend on other symbol tables.

A tool can remove individual symbol tables or entries. It must retain
`SymbolData` and `SymbolNames` while referenced. Unknown symbol sections
may reference either pool, so removing a pool requires understanding all
retained symbol sections or dropping unknown ones.

Tools may append bytes to `SymbolData` or complete records to `SymbolNames`
without understanding unknown sections, provided existing contents,
offsets, and indices retain their meanings. Other changes to either pool,
including compaction or reordering, require updating all retained symbol
sections or dropping unknown ones. Relocating sections preserves their
section-relative references. Stripping retains the original build ID.

### SymbolData

`SymbolData` holds UTF-8 strings and aligned numeric-key arrays. Strings
and arrays can share bytes. Embedded consumers can read names on demand
and stream string ranges without retaining them in RAM.

### SymbolNames

```wirely
// One name in a symbol name list. SymbolNames table.
struct type SymbolName {
  // Declared field or enum-member name in SymbolData.
  Name: StringRef,
}
```

Name lists occupy consecutive ranges. Equal lists can share entries.

### TypeSymbols

```wirely
// Sparse table, sorted by Type.
struct type TypeSymbol {
  // Index into ValueTypes; a struct or enum.
  Type: Ref,
  // Qualified declared name in SymbolData, or none.
  Name: OptionalStringRef,
  // Number of paired names and numeric keys.
  Count: uint32,
  // First entry in SymbolNames; zero when Count is zero.
  Names: Ref,
  // Byte offset into SymbolData of Count numeric keys.
  // 0xffffffff means implicit keys 0 through Count - 1.
  // Zero when Count is zero.
  Numbers: uint32,
}
```

Entries are strictly sorted by type index, without duplicates. Type names
and member names are independently optional. Names are qualified declared
type names, such as `Example.Mode`; member names are unqualified declared
names. JSON names are separate and can differ.

Each name corresponds to a numeric key at the same position. Struct keys
are field indices, encoded as `uint32`; closed and open enum keys are
member numbers, encoded using the underlying integer type's runtime binary
representation. Explicit arrays are aligned for that type and strictly
increasing by numeric value. Struct and closed enum keys fit their member
counts. `Numbers = 0xffffffff` selects implicit keys `0` through
`Count - 1` for any of these type kinds. An empty mapping has `Count`,
`Names`, and `Numbers` zero.

A mapping may omit individual fields or enum values. Missing enum names
fall back to numbers; missing field names fall back to field indices.

### StateVariableSymbols and ProcedureSymbols

```wirely
// StateVariableSymbols and ProcedureSymbols tables.
struct type IndexedSymbol {
  // Index into StateVariables or ProcedureInstances, selected by section.
  Index: Ref,
  // Canonical name in SymbolData.
  Name: StringRef,
}
```

Each table is strictly sorted by target index, without duplicates, and
can omit targets. State names identify declared state variables;
procedure symbols identify instances, including native initializers whose
state may be retained in a transition. Temporaries and fixed values have
no canonical names; source names for their values are aliases.

Ordinary instance paths and their members use dots, such as `a.b.count`
and `a.b.run`. Global members have no owner prefix. Singleton-owned symbols
start with the fully qualified singleton type name and a colon, such as
`Example.Shared:count` or `Example.Shared:child.run`. The singleton owner
itself is `Example.Shared:`. Instance symbols do not include multi-input
counts. Source aliases can also reach singleton-owned values through
ordinary instance paths.

### VariableSymbolAliases

```wirely
// Source-name lookup. VariableSymbolAliases table.
struct type VariableSymbolAlias {
  // Source access name in SymbolData.
  Name: StringRef,
  // Constants, fixed values, temporaries, old state, or new state.
  Bank: VariableBank,
  // Index into the table selected by Bank.
  Variable: Ref,
}
```

Aliases map source access names, including node inputs, outputs, and
forwarding bindings, to the value that supplies them. A default output can
be named by its node path alone. Names are strictly sorted by UTF-8 bytes,
without duplicates; several names can reference the same value. Fixed
aliases can refer to resources as well as runtime values.

Without the required symbols, name-based lookup is unavailable. Execution,
numeric inspection, and execution of an already generated transition need
no symbols. Diagnostics retain numeric procedure, instruction, bank, and
variable coordinates for host-side symbolication with the build ID.

## Verification

A runtime verifies a file before using it. A file is valid when it
follows all rules of this document except the producer requirements
below, so validity depends only on the file's bytes. Native code is not
involved; matching names to native implementations is part of
[linking](#linking). Transition files also follow the rules in
[State transitions](#state-transitions).

Each check concerns one record and records it refers to, or a record and
its predecessor in a sorted table. Apart from a few bytes per value type
and one bit per fixed value, temporary, and state variable, verification
can use the file directly. Branch checking can additionally use one bit
per code byte or decode ahead without that allocation.

A runtime may also reject a valid file that it cannot run:

- The file has a format version the runtime does not support.
- The file exceeds the runtime's limits, such as the file size, a table
  size, the number of sections, or the number of verification steps.
  The runtime may reject it before verifying it completely.
- Linking fails, because the runtime lacks a native implementation,
  function, or opaque struct the program names, its interface does not
  match, or it rejects a value of an opaque struct.

### Structure and references

The header has the expected magic and version, and `FileLength` is the
file length. The directory and all sections lie within the file and start
on 16-byte boundaries. Section types are strictly ascending, and each known
section has its listed `RecordType` and a length that fits that record
type. All ordinary sections are present. Missing symbol tables are empty.
References require their target pools to be present, even for empty
strings. Unknown sections are only checked
to lie within the file.

Bytes not referenced by any record are not checked. This includes bytes of
`ProcedureCode` outside every instruction stream.

Every reference fits its target: `Ref` and `OptionalRef` indices, ranges,
`ByteRange` byte ranges, `StringRef` and present `OptionalStringRef` byte
ranges, whose bytes are valid UTF-8, and named-number arrays, which are
aligned for their type. An absent `OptionalStringRef` has both fields
`0xffffffff`. Enum fields such as `Kind`, `Stage`, and `Bank` hold named
values, and padding is zero.

Range ends, count products, layout sizes, alignment rounding, and branch
targets are calculated without overflow or truncation. Computed ranges
fit their target storage, and sizes and offsets fit their declared fields.

### Types and values

Inner, underlying, and field types precede the types using them, so one
pass over `ValueTypes` finds the storage size, alignment, nesting depth,
and member count of each type. The verifier keeps these per type to tell
runtime, load-time, and const-only types apart. Integer types have a
listed width, open enum underlying types are integers, opaque structs have
a native name, and inner and field types are not resources. Closed enums
have at least one member and an underlying type of `uint8`, `uint16`, or
`uint32` large enough to represent `MemberCount - 1`. Each `JsonTypes`
entry has one name per closed enum member or struct field,
or strictly ascending numbers for an open enum. Its `Numbers` is
`0xffffffff` exactly for types other than open enums. `TypeSymbols` follows
the sparse mapping rules in its section.

`ConstValues` have no resource types. Const slots and list elements
contain valid values in the [const-data representation](#programdata),
aligned for their types and with zero padding. `InitialState` contains
valid values in the runtime binary representation, with zero padding.
Closed enum values are below their member count; open enum values fit
their underlying type. Values of opaque structs are checked during
linking. List elements are checked with the list's inner type, which
precedes the list type, so checks terminate even when lists share bytes.

`FixedValues`, `StateVariables`, and `Temporaries` are sorted by offset,
and each slot starts at or after the end of the previous one. Comparing
each entry with its predecessor therefore shows that slots do not overlap
and gives the buffer size. `FixedValues` have load-time types. The types of
`StateVariables`, `Temporaries`, locals, bytecode value operands of kind
`const` or `immediate`, bytecode procedure inputs and outputs, and native
function parameters and results are runtime types. Aliases have the types
of their selected banks.

### Names and bindings

Sorting turns uniqueness into a comparison with the predecessor. This
applies to ranges into `InterfaceNames`, to `JsonTypes`,
`NativeProcedures`, and `NativeFunctions`, and to aliases. The `Inputs`
and `Outputs` ranges of native procedure calls have the length of the
native procedure's `InputNames` and `OutputNames` ranges. Each value input
binds one source. Canonical symbol tables have strictly increasing target
indices in range.

### Procedure instances and code

The four stage records occur in the order specified by [Stages](#stages).
Their nonempty schedules follow each other without gaps and cover
`ProcedureInstances`; empty schedules have both range fields zero. Each
instance refers to exactly one native or bytecode procedure. Load
procedures read only const values and fixed values and write only fixed
values; runtime procedures write only temporaries and new state. Native
procedures with `Initializer` are used only by load procedures.

One pass over the procedures checks single assignment, using one bit per
fixed value, temporary, and state variable. Each output requires its
destination's bit to be clear and then sets it. Each input from fixed
values, temporaries, or new state requires its variable's bit to be set.
After the pass, every bit is set.

Every bytecode procedure's instruction stream is structurally checked
from its start, including unreferenced procedures. Opcodes, operand kinds,
operand indices, canonical encodings, instruction boundaries, and control
flow are checked without an instance. Branches only go forward, so a
branch target can be confirmed when decoding reaches it, using one bit per
code byte or by decoding ahead.

Full verification checks every bytecode procedure instance, using its
binding ranges and types and its scheduled stage. Immediate values,
operand types, and writable destinations must be valid in that context.
For `FAIL`, the instance is in the `Load` stage, its format index selects
a constant `List[string]` of the required length, and each specification
is supported for the corresponding value type.
All instructions are checked, including unreachable ones. Structural
decoding and compatible contextual checks can be shared across instances;
an unreferenced bytecode procedure has no instance context for full
verification.

### Producer requirements

Verifiers do not check these rules:

- Sections do not overlap each other, the header, or the directory.
- Buffers place variables in decreasing type alignment.
- Structurally equal types other than enums, structs, and resource types
  have one entry.
- The JSON names and the member symbols of one type are distinct.
- Variable, procedure, and present type symbols are distinct within their
  table.
- The build ID matches the unstripped file.

## State transitions

A transition file carries the state of a running program over to a new
program. It uses the same header and section directory as a program and
contains the transition's own `ValueTypes`, `TypeLists`, `NativeNames`,
`ConstValues`, `ProgramData`, `NativeFunctions`, and `ProcedureCode`, and a
`BytecodeProcedures` table with one entry: the transition procedure. It has no
stages, procedure instances, bindings, or variable tables.

The transition procedure reads the old program's state and writes the new
program's state. Its `InputCount` and `OutputCount` are the lengths of the
old and new programs' `StateVariables`. Operand `input.N` reads entry N of
the old program's `StateVariables`, and `output.N` writes entry N of the
new program's `StateVariables`. The procedure can use locals like any
bytecode procedure. Outputs start with their type's default, so the
procedure writes every new state value that differs from it.

| Section | Record type | Layout |
| --- | --- | --- |
| `OldBuildIDSHA512`, `NewBuildIDSHA512` | — | Buffer |
| `OldTypeMap`, `NewTypeMap` | `TypeMapEntry` | Table |
| `NativeStateSources` | `NativeStateSource` | Table |

```wirely
// The transition's equivalent of an old or new program type.
// OldTypeMap and NewTypeMap tables.
struct type TypeMapEntry {
  // Index into the transition's ValueTypes, or no entry.
  Type: OptionalRef,
}
```

Each type map has one entry per `ValueTypes` entry of its program. A mapped
type and its transition type have the same kind, integers the same width
and signedness, closed enums the same member count, and structs the same
opacity and field count. Their inner, underlying, and field types are
mapped to each other's.
Dependencies precede their users, so one forward pass verifies a map.
Operand types of inputs and outputs are looked up through the maps; the
types of used inputs and of all outputs are mapped.

```wirely
// Origin of the native state of one new load procedure.
// NativeStateSources table.
struct type NativeStateSource {
  // Index into the old program's ProcedureInstances whose native state
  // to keep, or no entry to create new native state.
  Old: OptionalRef,
}
```

`NativeStateSources` has one entry per initializer procedure of the new
program, in procedure order. `Old` refers to an initializer procedure of
the old program. Transition rules for native state are not specified yet.
