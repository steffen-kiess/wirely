# Wirely program binary format

This proposal represents `Program` definitions as indexed binary tables
for desktop and embedded runtimes. The current format version is 0.0, an
unstable development format.

A major-version change is breaking. Older consumers can inspect understood
resources in a newer minor version, but may miss new sections containing
critical information or encounter unknown types and opcodes. Partial
inspection does not establish full parsing or execution support.

## Representation and references

Declarations use Wirely structs and the
[runtime binary representation](value-types.md#runtime-binary-representation).
Record arrays use each struct's storage size as their stride.

Table indices are numbered from zero and local to the file. Valid indices
range from zero through `0xfffffffe`. Lengths, counts, and byte offsets use
the full `uint32` range; files fit within that address space.

```wirely
// An entry in the table specified by the referring field's comment.
struct type Ref {
  Value: uint32,
}

// An entry in the table specified at use, or 0xffffffff for no entry.
struct type OptionalRef {
  Value: uint32,
}
```

```wirely
// A consecutive slice of a fixed-size table.
// Target table is specified at each use.
struct type Range {
  // First target-table entry; zero when Count is zero.
  First: Ref,
  // Number of consecutive target entries.
  Count: uint32,
}
```

Table ranges fit their targets.

```wirely
// UTF-8 bytes within the byte section specified by the referring field.
struct type StringRef {
  // Byte offset into the associated byte section.
  Offset: uint32,
  // Number of UTF-8 bytes starting at Offset.
  LengthBytes: uint32,
}
```

A string occupies exactly `[Offset, Offset + LengthBytes)` in its buffer.
An empty string has both fields zero. Equal strings can share bytes.

## Header and sections

`Magic`, `VersionMajor`, and `VersionMinor` are a permanent header prefix:
`uint32`, `uint16`, and `uint16`, respectively, always little-endian. They
occupy the first eight bytes of the file in that order. For an unknown major
version, only these fields can be interpreted. The remaining header fields may
be absent or have different layouts or endianness. The rest of this proposal
describes version 0.0.

```wirely
// Identifies the image and locates its section directory.
// File offset 0.
struct type Header {
  // 0x594c5257; bytes 57 52 4c 59 ("WRLY").
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
  // Wire layout of the section's records or byte data.
  RecordType: uint32,
}
```

The directory contains `header.SectionCount` consecutive entries.
Sections occupy disjoint byte ranges starting on 8-byte boundaries, with
one section per section type. Their offsets locate bytes independently of
directory order. Numeric section-type and record-type assignments will be
specified when the format is finalized.

Consumers ignore unknown section types. A known `RecordType` lets
inspection tools such as objdump display records even when the section's
purpose is unknown. Resolving section-specific references requires
knowledge of that section's semantics.

| Section | Struct | Layout |
| --- | --- | --- |
| Build ID | `BuildIDSHA512` | Single |
| Value types | `TypeRecord` | Fixed |
| Static values | `Variable` | Fixed |
| Native node states | `NativeNodeState` | Fixed |
| State variables | `Variable` | Fixed |
| Temporaries | `Variable` | Fixed |
| Procedures | `Procedure` | Fixed |
| Procedure bodies | `ProcedureBody` | Fixed |
| Local variables | `LocalVariable` | Fixed |
| Stages | `Stage` | Fixed |
| Static bindings | `StaticBinding` | Fixed |
| Value inputs | `ValueInput` | Fixed |
| Input sources | `InputSource` | Fixed |
| Resource inputs | `ResourceInput` | Fixed |
| Resource providers | `ResourceProvider` | Fixed |
| Results | `Result` | Fixed |
| Initial state | — | Bytes |
| Program data | — | Bytes |
| Procedure code | — | Bytes |
| Node types | `NodeType` | Fixed |
| Native methods | `NativeMethod` | Fixed |
| Native functions | `NativeFunction` | Fixed |
| Function parameters | `FunctionParameter` | Fixed |
| Interface names | `NameRef` | Fixed |
| Name components | `NameRef` | Fixed |
| Enum definitions | `EnumDefinition` | Fixed |
| Enum members | `EnumMember` | Fixed |
| Struct definitions | `StructDefinition` | Fixed |
| Struct fields | `StructField` | Fixed |
| Names | — | Bytes |
| Debug metadata | `DebugMetadata` | Single |
| Node names | `DebugName` | Fixed |
| Procedure names | `ProcedureDebugName` | Fixed |
| State variable names | `DebugName` | Fixed |
| Temporary names | `DebugName` | Fixed |
| Node aliases | `NodeAlias` | Fixed |
| Singleton aliases | `NodeAlias` | Fixed |
| Variable aliases | `VariableAlias` | Fixed |
| Debug data | — | Bytes |

Fixed sections contain `Length / record_storage_size` consecutive records, where
`RecordType` determines the storage size. Their length is an exact multiple of
that size. Single sections contain one record with its storage size. Byte
sections contain a buffer of `Length` bytes, described by their byte-data record
type. Empty tables and buffers have length zero. Padding between sections is
outside their lengths. All ordinary program sections are present; debug sections
are optional as a group.

Structured records are always stored in fixed or single sections, so a
known `RecordType` suffices to decode them. Byte sections contain only
values, strings, and instruction streams. Program data holds values read
during execution, Names holds names for linking and type metadata, and
Debug data holds display names.

```wirely
// Identity of the original unstripped file.
// Build ID section.
struct type BuildIDSHA512 {
  // Digest bytes 0 through 7, little-endian.
  Word0: uint64,
  // Digest bytes 8 through 15, little-endian.
  Word1: uint64,
  // Digest bytes 16 through 23, little-endian.
  Word2: uint64,
  // Digest bytes 24 through 31, little-endian.
  Word3: uint64,
  // Digest bytes 32 through 39, little-endian.
  Word4: uint64,
  // Digest bytes 40 through 47, little-endian.
  Word5: uint64,
  // Digest bytes 48 through 55, little-endian.
  Word6: uint64,
  // Digest bytes 56 through 63, little-endian.
  Word7: uint64,
}
```

The words preserve standard SHA-512 digest-byte order: each word is its
eight consecutive digest bytes interpreted as a little-endian integer.

Compute SHA-512 over the entire unstripped file, including its header,
directory, debug sections, and padding, with the 64 Build ID bytes set to
zero. Store the resulting digest in this section. The Build ID section has
length 64.

Stripping removes debug sections and updates the directory, section
locations, and file length while retaining this original build ID. It
associates the stripped file with the original file and its debug data.
The digest is verified against the unstripped file with the ID bytes
zeroed.

## Program tables

```wirely
// A native state object and its initializer bindings.
// Native node states table.
struct type NativeNodeState {
  // Index into Node types.
  NodeType: Ref,
  // Range into Static bindings; initializer uses bindings.
  Values: Range,
  // Range into Resource inputs; constructor dependencies.
  Resources: Range,
}
```

Each native state object has its own entry in Native node states.
Stateless calls, bytecode procedures, and composed nodes have no entries.
States are numbered and constructed in `native_construction_order`, with
resource providers before consumers. Construction links the node type to
a native constructor and supplies the initializer's resolved `uses`
values and resource inputs. Its static values remain available throughout
the native object's lifetime.

```wirely
// A typed value with a byte offset into its associated storage.
// Static values, state, and temporaries.
struct type Variable {
  // Index into Value types; resolved stored-value type.
  ValueType: Ref,
  // Byte offset into Program data for Static values, the state buffer
  // for State variables, or the temporary buffer for Temporaries.
  Offset: uint32,
}
```

Static values describe resolved `uses` values for native procedures and
initializers, and constants read by procedure bodies. Bindings and
`const` operands reference these descriptors, which can be shared for
equal typed values. Their data and referenced payloads are stored in
Program data. Only values bound by `uses` clauses or read by procedure
bodies are stored; node parameters, constants, and expressions supply
those values.

State and temporary entries are ordered by ascending byte offset,
preserving runtime layout order for equal offsets. Each variable identity
retains its own entry, including zero-sized units sharing an offset.

State and temporary tables use runtime types. Each runtime buffer lays out
variables in decreasing type alignment, preserving variable order within an
alignment group. Each variable occupies its type's storage size. The buffer
alignment is the maximum type alignment, and its size rounds the last slot's end
up to that alignment. Empty buffers have size zero and alignment 1. Slot and
final buffer padding are zero. The runtime allocates state and temporary buffers
using this layout.

```wirely
// A native method invocation or procedure body with its bindings.
// Procedures table.
struct type Procedure {
  // Index into Native node states, or no entry for stateless calls and
  // procedure bodies.
  Node: OptionalRef,
  // Index into Native methods, or no entry for a procedure body.
  Method: OptionalRef,
  // Index into Procedure bodies, or no entry for a native method.
  Body: OptionalRef,
  // Range into Static bindings; procedure uses bindings.
  Values: Range,
  // Range into Value inputs.
  Inputs: Range,
  // Range into Resource inputs.
  Resources: Range,
  // Range into Results.
  Results: Range,
}
```

Procedures are stored in execution order, consecutively within each
stage. Each procedure has either a method or a body.

The method identifies the native implementation and its interface names.
When present, the node supplies the native state object and its type
matches the method's node type. Each native procedure receives its own
resolved `uses` values; omitting `uses` gives an empty binding range.
Triggers and wakeups identify a scheduled native-object procedure by its
node and method references. Stateless procedures retain distinct entries
even when they invoke the same method.

A body procedure executes its instruction stream. Its `Values` and
`Resources` ranges are empty, and each value input has one source.
Operands `input.N` and `result.N` select entry N of its `Inputs` and
`Results` ranges.

```wirely
// An instruction stream and its locals. Procedure bodies table.
struct type ProcedureBody {
  // Byte offset of the instruction stream in Procedure code.
  CodeOffset: uint32,
  // Instruction stream length in bytes.
  CodeLength: uint32,
  // Range into Local variables; local.N selects entry N.
  Locals: Range,
}
```

```wirely
// A procedure-local value. Local variables table.
struct type LocalVariable {
  // Index into Value types; runtime type of the local.
  ValueType: Ref,
}
```

Each instruction stream lies within Procedure code.

```wirely
// An execution stage and its ordered procedure range.
// Stages table.
struct type Stage {
  // 0 = input, 1 = default, 2 = output.
  Stage: uint8,
  // Range into Procedures, in execution order.
  Schedule: Range,
}
```

Stage records occur in execution order, including empty stages. Their
schedule ranges partition the procedure table. Empty stages have a
zero-count range.

```wirely
// Binds a stored value to one uses name. Static bindings table.
struct type StaticBinding {
  // Index into Interface names; uses binding name.
  Name: Ref,
  // Index into Static values.
  Value: Ref,
}
```

Names belong to the native method's `ValueNames` range for procedures or
the node type's `InitializerValueNames` range for initializers. Optional
parameter presence requested by `parameter_option` is represented by the
bound option value.

```wirely
// Binds variable sources to one named procedure input.
// Value inputs table.
struct type ValueInput {
  // Index into Interface names; input receiving sources. No entry for
  // procedure bodies.
  Name: OptionalRef,
  // 0 = one connection, 1 = ordered connection list.
  IsList: uint8,
  // Range into Input sources.
  Sources: Range,
}
```

Native input names belong to the method's `ValueInputNames` range.
`IsList = 0` selects one connection; `IsList = 1` selects an ordered
sequence, including an empty sequence.

```wirely
// One variable supplying a procedure input.
// Input sources table.
struct type InputSource {
  // 0 = old state, 1 = temporaries.
  Bank: uint8,
  // Index into State variables or Temporaries, selected by Bank.
  Variable: Ref,
}
```

A bank-0 input reads the step's old-state snapshot. A bank-1 input reads a
temporary computed during the step, including newly produced state values
exposed through `StateNew` temporaries.

State inputs compare the step's old-state snapshot with the snapshot read
by the previous step, using the variable's value-type equality. Temporary
inputs compare this step's values with the previous step's cached
values. A difference requires procedure execution. On the first step,
procedures execute without previous values. Triggers and wakeups also
require execution. A skipped procedure preserves its state and temporary
results.

```wirely
// Binds native state providers to one named resource input.
// Resource inputs table.
struct type ResourceInput {
  // Index into Interface names; resource input receiving providers.
  Name: Ref,
  // 0 = one provider, 1 = ordered provider list.
  IsList: uint8,
  // Range into Resource providers.
  Providers: Range,
}
```

Resource names belong to the node type's `InitializerResourceNames`
range for construction or the native method's `ResourceInputNames` range
for procedure calls.
`IsList = 0` selects one provider; `IsList = 1` selects an ordered
sequence, including an empty sequence.

```wirely
// One node supplying its native state as a resource.
// Resource providers sequence.
struct type ResourceProvider {
  // Index into Native node states; supplies a native state object.
  Node: Ref,
}
```

```wirely
// Binds one named procedure output to a destination variable.
// Results table.
struct type Result {
  // Index into Interface names; output supplying value. No entry for
  // procedure bodies.
  Name: OptionalRef,
  // 0 = next state, 1 = temporaries.
  Bank: uint8,
  // Index into State variables or Temporaries, selected by Bank.
  Variable: Ref,
  // Index into Temporaries; new state value, or no entry.
  StateNew: OptionalRef,
}
```

Native output names belong to the method's `OutputNames` range. A bank-0
result writes the next state and its `StateNew` temporary. A bank-1 result
writes a temporary and has no `StateNew` entry.

Initial state has the same byte layout and size as the runtime state
buffer. It stores every state variable's initial data bytes at its declared
offset, with zero slot and final buffer padding. The runtime can copy this
section directly into its state buffer. An empty buffer has section length
zero.

```wirely
// A qualified type name and its native interfaces. Node types table.
struct type NodeType {
  // Range into Name components; qualified name.
  Components: Range,
  // Range into Interface names; initializer uses names.
  InitializerValueNames: Range,
  // Range into Interface names; constructor resource names.
  InitializerResourceNames: Range,
  // Range into Native methods.
  Methods: Range,
}
```

Node types include types defining native methods for stateless calls,
even when no native state entry refers to them.

Each interface-name range contains unique names in UTF-8 bytewise
lexical order. Bindings use absolute indices into Interface names.

The reader matches qualified node type and method names to native code and binds
interface names to static values, inputs, outputs, and resources. Embedded
adapters can retain numeric bindings and function pointers after linking. Native
trigger names resolve to the same local method indices. These names remain
in Names for linking after a restart.

```wirely
// A named native procedure and its interfaces. Native methods table.
struct type NativeMethod {
  // Index into Node types; defines the method.
  NodeType: Ref,
  // Method name in Names.
  Name: StringRef,
  // Range into Interface names; procedure uses names.
  ValueNames: Range,
  // Range into Interface names.
  ValueInputNames: Range,
  // Range into Interface names.
  ResourceInputNames: Range,
  // Range into Interface names.
  OutputNames: Range,
}
```

Method entries are contiguous per node type, ordered by method name.
Procedure method references are absolute within Native methods.

```wirely
// A name in Names. Interface names and Name components tables.
struct type NameRef {
  // UTF-8 name in Names.
  Name: StringRef,
}
```

Interface names contain static binding, resource, input, and output
names. Name components store each qualified name as consecutive parts,
such as `("ABB", "JRA", "Shutter")`; records refer to a qualified name
by a range into this table.

```wirely
// A native function called by procedure bodies.
// Native functions table.
struct type NativeFunction {
  // Range into Name components; qualified name.
  Components: Range,
  // Range into Function parameters, in argument order.
  Parameters: Range,
  // Index into Value types; runtime result type.
  ReturnType: Ref,
}
```

```wirely
// One parameter of a native function. Function parameters table.
struct type FunctionParameter {
  // Parameter name in Names.
  Name: StringRef,
  // Index into Value types; runtime argument type.
  ValueType: Ref,
}
```

The runtime resolves each function by its qualified name before
execution. Parameter names and types describe the native
implementation's signature; parameter names are distinct.

```wirely
// A named enumeration associated with its runtime value type.
// Enum definitions table.
struct type EnumDefinition {
  // Range into Name components; qualified name.
  Components: Range,
  // EnumType index in Value types.
  ValueType: Ref,
  // Range into Enum members.
  Members: Range,
}
```

`ValueType` references the `EnumType` whose `Definition` points to this
entry. Its `UnderlyingType` supplies the runtime integer encoding. Names
and member numbers support native enum reconstruction and conversions
such as JSON. Members are ordered by numeric value.

```wirely
// One named enumeration member. Enum members table.
struct type EnumMember {
  // Member number; signed values use sign-extended bits.
  Number: uint64,
  // Member name in Names.
  Name: StringRef,
}
```

Member numbers use a 64-bit representation: signed underlying integers
are sign-extended in two's complement; unsigned integers are zero-extended.
Interpretation follows the enum's underlying integer type, and each value
fits that type. Runtime enum values use their underlying integer's width.

```wirely
// A named struct associated with its value type.
// Struct definitions table.
struct type StructDefinition {
  // Range into Name components; qualified name.
  Components: Range,
  // StructType index in Value types.
  ValueType: Ref,
  // Range into Struct fields, in declaration order.
  Fields: Range,
}
```

```wirely
// One named struct field. Struct fields table.
struct type StructField {
  // Field name in Names.
  Name: StringRef,
  // Index into Value types.
  ValueType: Ref,
}
```

`ValueType` references the `StructType` whose `Definition` points to this
entry. Field offsets follow the
[runtime binary representation](value-types.md#runtime-binary-representation).
Field index N in `GET_FIELD` and `SET_FIELD` selects entry N of `Fields`.
Names support native struct reconstruction and display.

## Value types and program data

Value type entries use the following equal-storage-size layouts.
Runtime value sizes and alignments are determined from this table.

`TypeRecord` is a `SimpleType`, `EnumType`, `CompositeType`, or
`StructType`, selected by its first byte (`Kind`).

```wirely
// Identifies a scalar value type.
// Primitive and static scalar type records.
struct type SimpleType {
  // Primitive or static scalar kind.
  Kind: uint8,
  // Reserved; zero.
  Reserved32: uint32,
  // Zero.
  Reserved64: uint64,
}
```

```wirely
// Identifies an enum's runtime integer representation and named definition.
struct type EnumType {
  // Enum kind.
  Kind: uint8,
  // Index into Value types; underlying integer type.
  UnderlyingType: Ref,
  // Index into Enum definitions.
  Definition: Ref,
  // Zero.
  Reserved: uint32,
}
```

```wirely
// Identifies an option or list type and its contained value type.
struct type CompositeType {
  // Option kind, or List kind for static values.
  Kind: uint8,
  // Index into Value types.
  InnerType: Ref,
  // Zero.
  Reserved: uint64,
}
```

```wirely
// Identifies a struct type by its named definition.
struct type StructType {
  // Struct kind.
  Kind: uint8,
  // Index into Struct definitions.
  Definition: Ref,
  // Zero.
  Reserved: uint64,
}
```

`Kind` identifies the record layout and represented type. Kind numbers
will be assigned when the format is finalized.

| Kind | Record |
| --- | --- |
| `Unit`, `Boolean` | `SimpleType` |
| `Int8`, `UInt8`, `Int16`, `UInt16`, `Int32`, `UInt32`, `Int64`, `UInt64` | `SimpleType` |
| `Duration`, `TimeOfDay`, `MonotonicTime` | `SimpleType` |
| `String`, `ValueType` | `SimpleType` |
| `Enum` | `EnumType` |
| `Struct` | `StructType` |
| `Option`, `List` | `CompositeType` |

Intern structurally equal types; enums and structs have one entry per
declaration. Inner types occur before their composite types, underlying
integers before their enum types, and field types before their struct
types, making size calculation possible in one forward pass. `String`,
`ValueType`, and lists, including composites and structs containing them,
are only used for static values in this format.

### Program data

Program data stores static value slots, list arrays, and string payloads.
Payloads use their type's storage layout. Equal values or payloads can
share storage; nested references form an acyclic structure.

Static values describe the resolved type and offset of each top-level
binding value or constant. Runtime types use the same representation as
initial-state data. Static `String` values use `StringRef` into Program
data. Options apply the runtime option layout to the representations
below, including strings and lists.

```wirely
// A static value identifying another value type.
struct type ValueTypeValue {
  // Index into Value types.
  ValueType: Ref,
}
```

```wirely
// A static list identifying element slots in Program data.
struct type ListValue {
  // Byte offset into Program data; first element.
  Offset: uint32,
  // Number of consecutive elements, including units.
  ElementCount: uint32,
}
```

For `List[T]`, `CompositeType.InnerType` selects `T` from Value types.
The list occupies `[Offset, Offset + ElementCount * storage_size(T))`
in Program data, using `T`'s storage layout. An empty list has both fields
zero. Zero-sized units retain their element count.
Nested list elements contain `ListValue` offset/count pairs into the same
buffer.

### Names

Names contains UTF-8 bytes for qualified-name components and for method,
interface, function parameter, enum member, and struct field names. A
runtime that links native code numerically and needs no names during
execution, such as for JSON enum conversion, can discard Names after
linking.

## Procedure instruction byte stream

A procedure body executes a byte stream of instructions. Each instruction starts
with one `uint8` opcode, followed by its operands in the order listed below.
The opcode determines the operand count and instruction length. Opcode and
operand-kind numbers remain to be assigned. Multi-byte integers are little
endian; instructions and operands require no alignment.

### Value operands

The written form uses lowercase operand kinds and zero-based indices, for
example `COPY local.0, input.1`.

| Operand kind | Index selects | Access |
| --- | --- | --- |
| `input` | Entry in the procedure's `Inputs` range | Read |
| `result` | Entry in the procedure's `Results` range | Read/write |
| `local` | Entry in the body's `Locals` range | Read/write |
| `const` | Entry in Static values | Read |

Operands identify whole typed values. There are no references. Whether to
add an immediate-value operand kind has not yet been decided. Field indices
and branch displacements are integer parameters, encoded separately from
value operands.

A value operand starts with a descriptor byte. Bits 7–5 identify the operand
kind; bits 4–0 select the index encoding:

| Selector | Index encoding | Total bytes |
| --- | --- | --- |
| 0–28 | Selector is the index | 1 |
| 29 | Following `uint8` | 2 |
| 30 | Following `uint16` | 3 |
| 31 | Following `uint32` | 5 |

The verifier requires the shortest form: inline for 0–28, `uint8` for
29–255, `uint16` for 256–65535, and `uint32` for larger indices. Every index
must also be within the selected range or table.

### Integer parameters

Field indices, branch displacements, function indices, and argument counts
share this unsigned encoding:

| First byte | Value encoding | Total bytes |
| --- | --- | --- |
| 0–253 | First byte is the value | 1 |
| 254 | Following `uint16` | 3 |
| 255 | Following `uint32` | 5 |

There is no extended `uint8` form. The verifier requires the shortest form:
one byte for 0–253, `uint16` for 254–65535, and `uint32` for larger values.
Field indices select struct fields in declaration order, starting at zero.

### Instructions

Mnemonics are uppercase. Instructions with a destination place it first.
Each name below denotes a value operand except `field`, `offset`,
`function`, and `count`, which denote integer parameters.

| Instruction | Behavior |
| --- | --- |
| `COPY destination, source` | Copy the whole value, including structs and options. Both operands have the same type. |
| `SET_DEFAULT destination` | Assign the destination type's default value. |
| `GET_FIELD destination, source, field` | Copy a struct field into a destination of the field's type. |
| `SET_FIELD destination, source, field` | Copy the source into a struct destination's field of the same type, preserving other fields. |
| `MAKE_SOME destination, source` | Copy a value of type `T` into a destination of type `Option[T]` as `Some`. |
| `GET_OPTION_VALUE destination, source` | Copy the value of `Some`, or assign the contained type's default for `None`. The destination has that contained type. |
| `IS_SOME destination, source` | Assign a boolean indicating whether the option is `Some`. |
| `NOT destination, source` | Negate a boolean. |
| `AND destination, left, right` | Compute boolean conjunction. |
| `OR destination, left, right` | Compute boolean disjunction. |
| `EQUAL destination, left, right` | Compare same-type values for equality, assigning a boolean. |
| `LESS destination, left, right` | Assign whether `left` orders before `right`. Both have the same integer, `Duration`, or `TimeOfDay` type. |
| `ADD_WRAPPING`, `SUB_WRAPPING`, `MUL_WRAPPING` `destination, left, right` | Compute the integer result modulo 2^bits within the operand type's range. |
| `ADD_SATURATING`, `SUB_SATURATING`, `MUL_SATURATING` `destination, left, right` | Compute the integer result clamped to the operand type's range. |
| `ADD_CHECKED`, `SUB_CHECKED`, `MUL_CHECKED`, `DIV_CHECKED`, `REM_CHECKED` `destination, left, right` | Assign `Some` with the integer result, or `None` when it is outside the operand type's range or the divisor is zero. |
| `CONVERT_WRAPPING destination, source` | Convert the source number modulo the destination type's range. |
| `CONVERT_SATURATING destination, source` | Convert the source number clamped to the destination type's range. |
| `CONVERT_CHECKED destination, source` | Assign `Some` with the source number, or `None` when it is outside the destination type's range. |
| `JUMP offset` | Continue at the branch target. |
| `JUMP_IF_TRUE condition, offset` | Branch when the boolean condition is true; otherwise continue with the next instruction. |
| `JUMP_IF_FALSE condition, offset` | Branch when the boolean condition is false; otherwise continue with the next instruction. |
| `CALL destination, function, count, arguments…` | Call a native function with `count` argument operands and assign its result. |
| `RETURN` | End the procedure with its current result values. |

`NOT`, `AND`, and `OR` require boolean operands and destinations.
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

`CALL` selects an entry of Native functions. `count` repeats the entry's
parameter count so that instructions can be decoded without the table; the
verifier checks that they match and that the argument and destination
types match the entry. Functions are pure and return a value for every
argument.

Each invocation initializes every local and result to its type's default.
For unit this is the unit value, for boolean false, for integers zero, for
options `None`, for enums the first declared member, for durations and
times of day zero, for monotonic times generation 0 with timestamp 0, and
for structs the defaults of their fields. Generation 0 is never current,
so the monotonic-time default compares to no current time. Defaults need not have an all-zero representation.
Values remain valid and canonical, including padding and unused option
payloads, as specified by the runtime binary representation.

Instructions read all source values before modifying their destination,
allowing a destination also to be a source. `COPY` preserves its source
unless it is also the destination. Struct and option copies are whole-value
operations subject to the runtime type size and nesting limits.

### Control flow and verification

A branch target is the byte offset immediately after the complete branch
instruction plus its unsigned displacement. A zero displacement targets
the next instruction; every branch moves forward from its own instruction.
The target must be an instruction start inside the stream. The position
immediately after the stream is not a target: for a 120-byte stream, offset
119 is valid only if an instruction starts there, and offset 120 is invalid.

The stream is nonempty and its final instruction is `RETURN`. Earlier
returns are allowed. There are no calls or backward branches.

Before execution, the verifier checks the entire stream for known opcodes
and operand kinds, complete operands, shortest encodings, index bounds,
operand types, runtime-typed constants, writable destinations, and valid
branch targets. Target
calculation must not overflow. Every byte belongs to a decoded instruction;
unknown instructions cannot be skipped. Default initialization removes the
need to track which locals or results have been assigned.

## Debugging and lazy reads

```wirely
// Display names for the program and its variable blocks.
// Debug metadata section.
struct type DebugMetadata {
  // Program name in Debug data.
  ProgramName: StringRef,
  // State block name in Debug data.
  StateBlockName: StringRef,
  // Temporary block name in Debug data.
  TempBlockName: StringRef,
}
```

```wirely
// A display name referring to a node or variable.
// Node/State variable/Temporary names tables.
struct type DebugName {
  // Index into Native node states, State variables, or Temporaries,
  // selected by the names table.
  Target: Ref,
  // Display name in Debug data.
  Name: StringRef,
}
```

```wirely
// Display names of a procedure. Procedure names table.
struct type ProcedureDebugName {
  // Index into Procedures.
  Target: Ref,
  // Owning instance's display name in Debug data.
  NodeName: StringRef,
  // Procedure or method name in Debug data.
  ProcedureName: StringRef,
}
```

Node names, Procedure names, State variable names, and Temporary names
contain one entry per target, ordered by target index. Counts match their
target tables. Variable block membership is defined by the variable tables.

Node names preserve `NativeInstance.name` for retained native objects.
Procedure names preserve the owning instance's display name, including
for stateless calls and procedure bodies, and the procedure's name within
that instance.

```wirely
// An alias referring to a node instance.
// Node aliases and Singleton aliases tables.
struct type NodeAlias {
  // Declared path or type name in Debug data.
  Name: StringRef,
  // Index into Native node states.
  Node: Ref,
}
```

Node and singleton aliases refer only to retained native objects.
Aliases are sorted by UTF-8 name bytes. Multiple aliases can point to
one identity.

```wirely
// An alias referring to a state or temporary variable.
// Variable aliases table.
struct type VariableAlias {
  // Alias name in Debug data.
  Name: StringRef,
  // 0 = state, 1 = temporaries.
  Bank: uint8,
  // Index into State variables or Temporaries, selected by Bank.
  Variable: Ref,
}
```

Variable aliases are also sorted by UTF-8 name bytes.

Debug data contains UTF-8 bytes referenced by debug records. All debug
references stay within the debug sections or identify ordinary program
table entries. Ordinary program records reference only ordinary sections.
An external tool can strip the debug sections as a group and retain the
Build ID section.

An embedded reader reads the header and directory and links native code
and functions using Names.
Debug records and their data can be read on demand. To print a node name,
read its Node names entry, then stream the referenced Debug data byte range
over UART. Numeric node, procedure, and variable indices also serve as
diagnostic identifiers.

Static value descriptors locate value slots in Program data. Strings and
lists identify payload ranges for deferred reads. Schedules and bindings
can be retained in RAM or accessed directly from aligned flash. Lazy reads
use seekable storage, a retained image, or a reopened source. Section byte
ranges support independent reads.

## Mapping from Program

| Program data | File representation |
| --- | --- |
| `name` | Debug metadata |
| `state_variables`, `temporaries` | Variable types, offsets, and debug names |
| `initial_state` | Initial-state buffer |
| Native-state `instances` | Native node states and debug names |
| `instances[*].initializer_values` | Initializer static bindings and data |
| `native_construction_order` | Native state numbering and construction order |
| `procs` | Stages, procedures, methods, bindings, and debug names |
| `procs[*].values` | Procedure static bindings and data |
| `procs[*].body` | Procedure bodies, local variables, and Procedure code |
| `constants` | Static values and data |
| `functions` | Native functions and function parameters |
| `node_aliases`, `singleton_aliases` | Aliases targeting retained nodes |
| `variables` | Variable aliases |

Program tables preserve native-object, procedure, and variable identities
and the names needed to resolve native implementations and functions.
Debug sections preserve display names and aliases.

## State transitions

A transition file carries the state of a running program over to a new
program. It uses the same header and section directory as a program and
contains the transition's own Value types, their definitions, Names,
Static values, Program data, Native functions, Function parameters, and
one body procedure with its tables. It has no stages or temporaries.
The procedure's input sources read the old program's State variables, and
its results write the new program's State variables.

| Section | Struct | Layout |
| --- | --- | --- |
| Old build ID, New build ID | `BuildIDSHA512` | Single |
| Old type map, New type map | `TypeMapEntry` | Fixed |
| State copies | `StateCopy` | Fixed |
| Native state sources | `NativeStateSource` | Fixed |

```wirely
// The transition's equivalent of an old or new program type.
// Old type map and New type map tables.
struct type TypeMapEntry {
  // Index into the transition's Value types, or no entry.
  Type: OptionalRef,
}
```

Each type map has one entry per Value types entry of its program. A mapped
type and its transition type have the same kind; their inner, underlying,
and field types are mapped to each other's; and enums and structs have
equal qualified names, members, and field names. Dependencies precede
their users, so one forward pass verifies a map. Operand types of inputs
and results are looked up through the maps.

```wirely
// Copies an unchanged state variable. State copies table.
struct type StateCopy {
  // Index into the old program's State variables.
  Old: Ref,
  // Index into the new program's State variables.
  New: Ref,
}
```

Both variables' types map to the same transition type.

```wirely
// Origin of one new native state object. Native state sources table.
struct type NativeStateSource {
  // Index into the old program's Native node states to keep, or no entry
  // to construct a new object.
  Old: OptionalRef,
}
```

Native state sources have one entry per new Native node states entry.
New state variables that are neither copied nor written by the procedure
keep their Initial state.
