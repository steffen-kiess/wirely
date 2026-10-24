# Wirely program binary format

This proposal represents `Program` definitions as indexed binary tables
for desktop and embedded runtimes. The current format version is 0.0, an
unstable development format.

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
inspection does not establish full parsing or execution support.

The rest of this proposal describes version 0.0.

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
offsets locate bytes independently of directory order. Numeric
section-type and record-type assignments will be specified when the format
is finalized.

Each section holds a table, a record, or a buffer, depending on its
layout. A table contains consecutive records of its record type, so its
length is an exact multiple of the record size. A record section contains
one record with its storage size. A buffer contains `Length` bytes of
values, strings, or instruction streams. Empty tables and buffers have
length zero. Padding between sections is outside their lengths.

Consumers ignore unknown section types. Structured records are always
stored in tables or record sections, so a known `RecordType` lets
inspection tools display records even when the section's purpose is
unknown. Resolving section-specific references requires knowledge of that
section's semantics.

`wirely objdump -h FILE` lists the sections of a file, and `-s` dumps
their contents.

| Section | Record type | Layout |
| --- | --- | --- |
| [`BuildIDSHA512`](#buildidsha512) | — | Buffer |
| [`NativeNames`](#nativenames) | — | Buffer |
| [`ValueTypes`](#valuetypes) | `TypeRecord` | Table |
| [`TypeLists`](#typelists) | `TypeEntry` | Table |
| [`JsonTypes`](#jsontypes-and-jsonnames) | `JsonType` | Table |
| [`JsonNames`](#jsontypes-and-jsonnames) | `JsonName` | Table |
| [`JsonData`](#jsondata) | — | Buffer |
| [`ConstValues`](#constvalues) | `Variable` | Table |
| [`ProgramData`](#programdata) | — | Buffer |
| [`LoadValues`](#loadvalues-statevariables-and-temporaries) | `Variable` | Table |
| [`StateVariables`](#loadvalues-statevariables-and-temporaries) | `Variable` | Table |
| [`Temporaries`](#loadvalues-statevariables-and-temporaries) | `Variable` | Table |
| [`InitialState`](#initialstate) | — | Buffer |
| [`Stages`](#stages) | `Stage` | Table |
| [`Procedures`](#procedures) | `Procedure` | Table |
| [`NativeProcedures`](#nativeprocedures) | `NativeProcedure` | Table |
| [`InterfaceNames`](#interfacenames) | `NameRef` | Table |
| [`Inputs`](#inputs) | `Input` | Table |
| [`Outputs`](#outputs) | `Output` | Table |
| [`ProcedureBodies`](#procedurebodies) | `ProcedureBody` | Table |
| [`NativeFunctions`](#nativefunctions) | `NativeFunction` | Table |
| [`ProcedureCode`](#procedurecode) | — | Buffer |
| [`SymbolMetadata`](#symbolmetadata) | `SymbolMetadata` | Record |
| [`StateVariableSymbols`](#symbol-tables) | `Symbol` | Table |
| [`TemporarySymbols`](#symbol-tables) | `Symbol` | Table |
| [`ProcedureSymbols`](#symbol-tables) | `Symbol` | Table |
| [`TypeSymbols`](#typesymbols-and-membersymbols) | `TypeSymbol` | Table |
| [`MemberSymbols`](#typesymbols-and-membersymbols) | `Symbol` | Table |
| [`VariableAliases`](#variablealiases) | `VariableAlias` | Table |
| [`SymbolData`](#symboldata) | — | Buffer |

The sections from `SymbolMetadata` on are
[symbol sections](#symbol-sections); each of them is optional. All other
sections are present.

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

### NativeNames

`NativeNames` contains the UTF-8 native names and interface names used for
linking.

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
a struct's `Fields` range. Field offsets follow the
[runtime binary representation](value-types.md#runtime-binary-representation).

Types are equal exactly when their indices are equal. Producers intern
structurally equal types; enums, structs, and resource types have one entry
per declaration. Inner, underlying, and field types occur before the types
using them, making size calculation possible in one forward pass.

A resource is a handle to a native object, such as a KNX connection. It
has storage size zero and alignment 1; the runtime keeps the native object
separately. No option, list, or struct contains a resource. `string`,
`type`, and lists, including composites and structs containing them, are
only used for const values. All other types are runtime types. Runtime
types and resources are load-time types.

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
parameter types of a native function, or the locals of a procedure body, as
ranges of entries. Equal lists can share entries.

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
// ConstValues, LoadValues, StateVariables, and Temporaries tables.
struct type Variable {
  // Index into ValueTypes; resolved stored-value type.
  ValueType: Ref,
  // Byte offset into ProgramData for ConstValues, the load buffer for
  // LoadValues, the state buffer for StateVariables, or the temporary
  // buffer for Temporaries.
  Offset: uint32,
}
```

Const values describe constants read by procedure bodies and the values
bound to procedure inputs, such as parameters, constants, and the resolved
values of `uses` clauses. `const` operands and const inputs reference
these entries, which can be shared for equal typed values. Their data and
referenced payloads are stored in `ProgramData`. Only values used in one of
these ways are stored; node parameters, constants, and expressions supply
those values.

### ProgramData

`ProgramData` stores const value slots, list arrays, and string payloads.
Payloads use their type's storage layout. Equal values or payloads can
share storage.

Runtime types use their runtime binary representation. Const `string`
values use `StringRef` into `ProgramData`. Options apply the runtime option
layout to the representations below, including strings and lists.

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

For `List[T]`, `CompositeType.InnerType` selects `T` from `ValueTypes`.
The list occupies `[Offset, Offset + ElementCount * storage_size(T))`
in `ProgramData`, using `T`'s storage layout. An empty list has both fields
zero. Zero-sized units retain their element count.
Nested list elements contain `ListValue` offset/count pairs into the same
buffer.

## Variables

### LoadValues, StateVariables, and Temporaries

`LoadValues`, `StateVariables`, and `Temporaries` use the `Variable`
record. Their offsets locate values in the runtime's load buffer, state
buffer, and temporary buffer. The load stage writes the load buffer; the
file contains no data for it.

Entries are ordered by ascending byte offset, preserving runtime layout
order for equal offsets. Each variable identity retains its own entry,
including zero-sized units and resources sharing an offset.

`LoadValues` uses load-time types; the other two tables use runtime types.
Each variable occupies its type's storage size at an offset aligned for
its type, starting at or after the end of the previous slot. The buffer
alignment is the maximum type alignment, and its size rounds the last
slot's end up to that alignment. Empty buffers have size zero and
alignment 1. Slot and final buffer padding are zero. The runtime allocates
load, state, and temporary buffers using this layout. Producers lay out
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
  // Range into Procedures, in execution order.
  Schedule: Range,
}
```

Stage records occur in execution order, including empty stages. Their
schedule ranges partition the procedure table. Empty stages have a
zero-count range.

The `Load` stage runs once when a program is loaded, before its first
step. Its procedures, the load procedures, always run. The other stages
run in every step; their procedures are runtime procedures.

Load procedures may fail, which makes loading fail. Loading a new program
leaves the running program unchanged until its load stage succeeds.
Native objects created during loading become fully functional only after
that. Runtime procedures and state transitions cannot fail.

### Procedures

```wirely
// A native procedure invocation or procedure body with its bindings.
// Procedures table.
struct type Procedure {
  // Index into NativeProcedures, or no entry for a procedure body.
  Native: OptionalRef,
  // Index into ProcedureBodies, or no entry for a native procedure.
  Body: OptionalRef,
  // Range into Inputs.
  Inputs: Range,
  // Range into Outputs.
  Outputs: Range,
}
```

Procedures are stored in execution order, consecutively within each
stage. Each procedure has either a native procedure or a body.

The native procedure identifies the native implementation and its
interfaces. Native load procedures can create native objects and output
them as resources. Other procedures reach native objects only through
their resource inputs. Procedures retain distinct entries even when they
invoke the same native procedure.

Native code can request that a runtime native procedure executes, for
example through a wakeup; the mechanism is part of the runtime's native
interface.

A body procedure executes its instruction stream. Its inputs, outputs, and
locals have runtime types. Operands `input.N` and `output.N` select entry
N of its `Inputs` and `Outputs` ranges.

Load procedures read const values and load values and write load values.
Runtime procedures write temporaries and new state. Each load value,
temporary, and state variable is the destination of exactly one output.
An input from load values, temporaries, or new state reads a variable
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
  // Whether the procedure creates native state.
  HasState: boolean,
}
```

Entries are sorted by UTF-8 name bytes, without duplicates.

Native state consists of the native objects created by a load procedure
whose native procedure has `HasState`, such as a KNX connection. A
[state transition](#state-transitions) can keep it. Only load procedures
use native procedures with `HasState`. Runtime native procedures keep no
state between steps beyond what they can rebuild from their inputs.

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

### Inputs

```wirely
// The storage a variable reference selects. 0 is not a valid bank.
open enum type VariableBank: uint8 {
  ConstValues = 1,
  LoadValues = 2,
  Temporaries = 3,
  OldState = 4,
  NewState = 5,
}
```

```wirely
// Binds one variable or const value to a procedure input.
// Inputs table.
struct type Input {
  // Const values, load values, temporaries, old state, or new state.
  Bank: VariableBank,
  // Index into ConstValues, LoadValues, Temporaries, or StateVariables,
  // selected by Bank.
  Variable: Ref,
}
```

For native calls, the `Inputs` range has one entry per name of the native
procedure's `InputNames` range; entry N binds input N. Optional parameter
presence requested by `optional` is represented by the bound option value.
Each value input binds exactly one source.
Procedure body inputs follow dependency declaration order; multi value
inputs expand into scalar inputs in connection order.

An old-state input reads the step's old-state snapshot. A new-state input
reads the state value written earlier in the step. A temporary input reads
a temporary computed during the step. A load input reads a value written
by the load stage. A const input reads a value from `ProgramData`.

Old-state inputs compare the step's old-state snapshot with the snapshot
read by the previous step, using the variable's value-type equality.
New-state inputs compare this step's new state with the step's old state,
which the previous step produced. Temporary inputs compare this step's
values with the previous step's cached values. A difference requires
procedure execution. On the first step, procedures execute without
previous values. Const and load inputs never change and are not compared.
Requests from native code also require execution. A skipped procedure
preserves its state and temporary outputs.

### Outputs

```wirely
// Binds one procedure output to a destination variable. Outputs table.
struct type Output {
  // Load values, temporaries, or new state.
  Bank: VariableBank,
  // Index into LoadValues, Temporaries, or StateVariables, selected by
  // Bank.
  Variable: Ref,
}
```

For native calls, the `Outputs` range has one entry per name of the native
procedure's `OutputNames` range; entry N binds output N.
A load output writes a load value. A new-state output writes the state
that becomes the next step's old state. A temporary output writes a
temporary.

### ProcedureBodies

```wirely
// An instruction stream and its locals. ProcedureBodies table.
struct type ProcedureBody {
  // Byte range in ProcedureCode; the instruction stream.
  Code: ByteRange,
  // Range into TypeLists; local.N has the type of entry N.
  Locals: Range,
}
```

### NativeFunctions

```wirely
// A native function called by procedure bodies.
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

### Linking

The runtime matches the qualified names of native procedures, native
functions, and resource types to native code, and binds interface names
to inputs and outputs. Each native procedure and native function also
links only when the runtime's description of its interface matches the
program's stage, `HasState`, interface names, and value types.

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
dependencies is `Namespace.BlockType:procedure.2.3.4`. The decimal suffixes
are member counts in dependency declaration order, including zero counts;
scalar and resource dependencies contribute no suffix. Native multi
value/event specialization is not currently supported. Procedure debug
symbols remain instance names such as `someblock:procedure`.

Embedded adapters can retain numeric bindings and function pointers after
linking.

## ProcedureCode

`ProcedureCode` contains the instruction streams of procedure bodies. Each
instruction starts with one `uint8` opcode, followed by its operands in the
order listed below. The opcode determines the operand count and instruction
length. Multi-byte integers are little endian; instructions and operands
require no alignment.

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
| `local` | Entry in the body's `Locals` range | Read/write |
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
operand or by the called function. Immediates cannot be used where no
other operand determines the type, such as conversion sources. The type is
an integer, boolean, or enum type, and N is interpreted as follows:

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
except `field`, `offset`, `function`, and `count`, which denote integer
parameters.

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

Ordering comparisons of `Std.Duration` and `Schedule.TimeOfDay` use native
functions `Std.Duration.Less` and `Schedule.TimeOfDay.Less`, respectively.

Each invocation initializes every local and output to its type's default.
For unit this is the unit value, for boolean false, for integers zero, for
options `None`, for enums zero, and for structs, including opaque ones,
the defaults of their fields. The default `Timer.MonotonicTime` has
generation 0, which is never current, so it compares to no current time.
Values remain valid and canonical, including padding and unused option
payloads, as specified by the runtime binary representation.

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

The stream is nonempty and its final instruction is `RETURN`. Earlier
returns are allowed. There are no calls or backward branches.

Target calculation must not overflow. Every byte of the stream belongs to
a decoded instruction; unknown instructions cannot be skipped. Default
initialization removes the need to track which locals or outputs have been
assigned.

## Symbol sections

Symbol sections hold symbols and aliases. Their records reference ordinary
tables and the `SymbolData` buffer; ordinary records never reference
symbol sections. A tool can strip the symbol sections and retain the
`BuildIDSHA512` section.

### SymbolMetadata

```wirely
// Symbols for the whole program. SymbolMetadata record.
struct type SymbolMetadata {
  // Program name in SymbolData.
  ProgramName: StringRef,
}
```

### Symbol tables

```wirely
// The name of a variable, procedure, or member.
// StateVariableSymbols, TemporarySymbols, ProcedureSymbols, and
// MemberSymbols tables.
struct type Symbol {
  // Name in SymbolData.
  Name: StringRef,
}
```

Each symbol table except `MemberSymbols` has one entry per entry of its
target table, in the same order:

| Table | Target table | Name |
| --- | --- | --- |
| `StateVariableSymbols` | `StateVariables` | Variable path, such as `a.b.count` |
| `TemporarySymbols` | `Temporaries` | Procedure symbol and output port joined by `.`, such as `a.b:run.output` |
| `ProcedureSymbols` | `Procedures` | Owning node path and procedure name joined by `:`, such as `a.b:run` |
| `TypeSymbols` | `ValueTypes` | Qualified name of an enum or struct, such as `Example.State`; none for other types |

Symbols identify variables, procedures, and types in diagnostics and when
building [state transitions](#state-transitions). Symbols and aliases for
load values are not specified yet.

### TypeSymbols and MemberSymbols

```wirely
// Symbols of one type. TypeSymbols table.
struct type TypeSymbol {
  // Qualified name in SymbolData, or none for unnamed types.
  Name: OptionalStringRef,
  // Number of member symbols: one per member of a closed enum or field
  // of a struct, or one per named number of an open enum; zero for
  // other types.
  Count: uint32,
  // Index into MemberSymbols of the first of Count consecutive symbols;
  // zero when Count is zero.
  Members: Ref,
  // Byte offset into SymbolData of the Count named numbers of an open
  // enum, in ascending order; zero when Count is zero. 0xffffffff for
  // other types.
  Numbers: uint32,
}
```

Member symbols are the declared member and field names. They correspond to
members, fields, and numbers like [JSON names](#jsontypes-and-jsonnames),
and their numbers use the layout of the numbers in `JsonData`. Debug output
shows these names and falls back to numbers when the symbols are stripped.

### VariableAliases

```wirely
// An alias referring to a state or temporary variable or a const value.
// VariableAliases table.
struct type VariableAlias {
  // Alias name in SymbolData.
  Name: StringRef,
  // Const values, temporaries, old state, or new state.
  Bank: VariableBank,
  // Index into ConstValues, Temporaries, or StateVariables, selected by
  // Bank.
  Variable: Ref,
}
```

Aliases map source names, including the inputs and outputs of nodes, to
the variable or const value that supplies their value. They are sorted by
UTF-8 name bytes. Multiple aliases can refer to one variable.

### SymbolData

`SymbolData` contains the UTF-8 bytes and open enum number arrays
referenced by symbol records.

An embedded reader reads the header and directory and links native code
and functions using `NativeNames`. Symbol records and their data can be
read on demand. To print a procedure name, read its `ProcedureSymbols`
entry, then stream the referenced `SymbolData` byte range over UART.
Numeric procedure and variable indices also serve as diagnostic
identifiers.

Const value entries locate value slots in `ProgramData`. Strings and
lists identify payload ranges for deferred reads. Schedules and bindings
can be retained in RAM or accessed directly from aligned flash. Lazy reads
use seekable storage, a retained image, or a reopened source. Section byte
ranges support independent reads.

## Mapping from Program

| Program data | File representation |
| --- | --- |
| `name` | `SymbolMetadata` |
| `state_variables`, `temporaries` | `StateVariables`, `Temporaries`, and their symbols |
| `initial_state` | `InitialState` |
| Native-state `instances` | `Load` procedures with `HasState` native procedures, and their `ProcedureSymbols` |
| `instances[*].initializer_values`, `procs[*].values` | `Inputs` from `ConstValues`, and their values |
| `instances[*].initializer_bindings`, `procs[*].resources` | `Inputs` from `LoadValues` |
| Resources provided by `instances` | `Outputs` to `LoadValues` |
| `native_types` | `NativeProcedures` with `HasState`, `InterfaceNames`, and `ResourceType` entries of `ValueTypes` |
| `native_procedures` | `NativeProcedures`, `InterfaceNames`, and `ResourceType` entries of `ValueTypes` |
| `native_construction_order` | Order of the `Load` stage |
| `procs` | `Stages`, `Procedures`, `NativeProcedures`, bindings, and `ProcedureSymbols` |
| `procs[*].body` | `ProcedureBodies`, `TypeLists`, and `ProcedureCode` |
| `constants` | `ConstValues` and `ProgramData` |
| `functions` | `NativeFunctions` and `TypeLists` |
| `variables` | `VariableAliases` |

Program tables preserve native-object, procedure, and variable identities
and the names needed to resolve native implementations and functions.
Symbol sections preserve symbols and variable aliases.

## Verification

A runtime verifies a file before using it. A file is valid when it
follows all rules of this proposal except the producer requirements
below, so validity depends only on the file's bytes. Native code is not
involved; matching names to native implementations is part of
[linking](#linking). Transition files also follow the rules in
[State transitions](#state-transitions).

Each check concerns one record and records it refers to, or a record and
its predecessor in a sorted table. Apart from a few bytes per value type
and one bit per load value, temporary, and state variable, verification
needs no memory beyond the file.

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
type. All ordinary sections are present. A missing symbol section counts
as empty, so references into it fail. Unknown sections are only checked
to lie within the file.

Bytes not referenced by any record are not checked. This includes bytes of
`ProcedureCode` outside every instruction stream.

Every reference fits its target: `Ref` and `OptionalRef` indices, ranges,
`ByteRange` byte ranges, `StringRef` and present `OptionalStringRef` byte
ranges, whose bytes are valid UTF-8, and named-number arrays, which are
aligned for their type. An absent `OptionalStringRef` has both fields
`0xffffffff`. Enum fields such as `Kind`, `Stage`, and `Bank` hold named
values, and padding is zero.

### Types and values

Inner, underlying, and field types precede the types using them, so one
pass over `ValueTypes` finds the storage size, alignment, nesting depth,
and member count of each type. The verifier keeps these per type to tell
runtime, load-time, and const-only types apart. Integer types have a
listed width, open enum underlying types are integers, opaque structs have
a native name, and inner and field types are not resources. Closed enums
have at least one member and an underlying type of `uint8`, `uint16`, or
`uint32` large enough to represent `MemberCount - 1`. Each `JsonTypes` entry and each `TypeSymbols` entry of an enum or struct
has one name per closed enum member or struct field, or strictly ascending
numbers for an open enum. `Numbers` is `0xffffffff` exactly for types
other than open enums.

`ConstValues` have no resource types. They, list elements, and
`InitialState` contain valid values in the runtime binary representation,
with zero padding. Closed enum values are below their member count; open
enum values fit their underlying type. Values of opaque structs are
checked during linking. List elements are checked with the list's inner
type, which precedes the list type, so checks terminate even when lists
share bytes.

`LoadValues`, `StateVariables`, and `Temporaries` are sorted by offset,
and each slot starts at or after the end of the previous one. Comparing
each entry with its predecessor therefore shows that slots do not overlap
and gives the buffer size. `LoadValues` have load-time types. The types of
`StateVariables`, `Temporaries`, locals, const and immediate operands,
procedure body inputs and outputs, aliases, and native function parameters
and results are runtime types.

### Names and bindings

Sorting turns uniqueness into a comparison with the predecessor. This
applies to ranges into `InterfaceNames`, to `JsonTypes`,
`NativeProcedures`, and `NativeFunctions`, and to aliases. The `Inputs`
and `Outputs` ranges of native procedure calls have the length of the
native procedure's `InputNames` and `OutputNames` ranges. Each value input
binds one source. Each symbol table has as many entries as its target
table.

### Procedures and code

Stage schedules follow each other without gaps and cover the procedure
table. Each procedure has a native procedure or a body. Load procedures
read only const values and load values and write only load values;
runtime procedures write only temporaries and new state. Native
procedures with `HasState` are used only by load procedures.

One pass over the procedures checks single assignment, using one bit per
load value, temporary, and state variable. Each output requires its
destination's bit to be clear and then sets it. Each input from load
values, temporaries, or new state requires its variable's bit to be set.
After the pass, every bit is set.

Each instruction stream is decoded once from its start. Opcodes, operand
kinds and encodings, index bounds, immediate values, operand types, and
writable destinations are checked as instructions are decoded. Branches
only go forward, so a branch target is confirmed when decoding reaches it,
using one bit per code byte or by decoding ahead.

### Producer requirements

Verifiers do not check these rules:

- Sections do not overlap each other, the header, or the directory.
- Buffers place variables in decreasing type alignment.
- The JSON names and the member symbols of one type are distinct.
- Variable, procedure, and present type symbols are distinct within their
  table.
- The build ID matches the unstripped file.

## State transitions

A transition file carries the state of a running program over to a new
program. It uses the same header and section directory as a program and
contains the transition's own `ValueTypes`, `TypeLists`, `NativeNames`,
`ConstValues`, `ProgramData`, `NativeFunctions`, and `ProcedureCode`, and a
`ProcedureBodies` table with one entry: the transition procedure. It has no
stages, procedure records, bindings, or variable tables.

The transition procedure reads the old program's state and writes the new
program's state. Operand `input.N` reads entry N of the old program's
`StateVariables`, and `output.N` writes entry N of the new program's
`StateVariables`. The procedure can use locals like any procedure body.
Outputs start with their type's default, so the procedure writes every new
state value that differs from it.

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
  // Index into the old program's Procedures whose native state to keep,
  // or no entry to create new native state.
  Old: OptionalRef,
}
```

`NativeStateSources` has one entry per load procedure of the new program
whose native procedure has `HasState`, in procedure order. `Old` refers to
such a procedure of the old program. Transition rules for native state are
not specified yet.
