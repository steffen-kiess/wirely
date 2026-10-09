# Wirely program binary format

This proposal represents `Program` definitions as indexed binary tables
for desktop and embedded runtimes. The current format version is 0.0, an
unstable development format.

## File identification

The first eight bytes of a file are `Magic` (`uint32`), `VersionMajor`
(`uint16`), and `VersionMinor` (`uint16`), in that order and always
little-endian. `Magic` is `0x594c5257`, the bytes `57 52 4c 59` ("WRLY").
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
  // Layout of the section's records or bytes.
  RecordType: uint32,
}
```

The directory contains `header.SectionCount` consecutive entries with
strictly ascending `SectionType`, so each section type occurs at most once.
Sections occupy disjoint byte ranges starting on 8-byte boundaries. Their
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
| [`Names`](#names) | — | Buffer |
| [`ValueTypes`](#valuetypes) | `TypeRecord` | Table |
| [`EnumMembers`](#enummembers) | `EnumMember` | Table |
| [`StructFields`](#structfields) | `StructField` | Table |
| [`TypeLists`](#typelists) | `TypeEntry` | Table |
| [`StaticValues`](#staticvalues) | `Variable` | Table |
| [`ProgramData`](#programdata) | — | Buffer |
| [`StateVariables`](#statevariables-and-temporaries) | `Variable` | Table |
| [`Temporaries`](#statevariables-and-temporaries) | `Variable` | Table |
| [`InitialState`](#initialstate) | — | Buffer |
| [`NativeStates`](#nativestates) | `NativeState` | Table |
| [`NativeStateTypes`](#nativestatetypes) | `NativeStateType` | Table |
| [`InterfaceNames`](#interfacenames) | `NameRef` | Table |
| [`ResourceInterfaces`](#resourceinterfaces) | `ResourceInterface` | Table |
| [`StaticBindings`](#staticbindings) | `StaticBinding` | Table |
| [`ResourceInputs`](#resourceinputs) | `ResourceInput` | Table |
| [`ResourceProviders`](#resourceproviders) | `ResourceProvider` | Table |
| [`Stages`](#stages) | `Stage` | Table |
| [`Procedures`](#procedures) | `Procedure` | Table |
| [`NativeProcedures`](#nativeprocedures) | `NativeProcedure` | Table |
| [`InputInterfaces`](#inputinterfaces) | `InputInterface` | Table |
| [`ValueInputs`](#valueinputs) | `ValueInput` | Table |
| [`InputSources`](#inputsources) | `InputSource` | Table |
| [`Outputs`](#outputs) | `Output` | Table |
| [`ProcedureBodies`](#procedurebodies) | `ProcedureBody` | Table |
| [`NativeFunctions`](#nativefunctions) | `NativeFunction` | Table |
| [`ProcedureCode`](#procedurecode) | — | Buffer |
| [`SymbolMetadata`](#symbolmetadata) | `SymbolMetadata` | Record |
| [`NodeSymbols`](#symbol-tables) | `Symbol` | Table |
| [`StateVariableSymbols`](#symbol-tables) | `Symbol` | Table |
| [`TemporarySymbols`](#symbol-tables) | `Symbol` | Table |
| [`ProcedureSymbols`](#symbol-tables) | `Symbol` | Table |
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

### StringRef

```wirely
// UTF-8 bytes in the buffer named by the referring field's comment.
struct type StringRef {
  // Byte offset into the buffer.
  Offset: uint32,
  // Number of UTF-8 bytes starting at Offset.
  LengthBytes: uint32,
}
```

Strings are stored in three buffers: `Names` holds names for linking and
type metadata, `ProgramData` holds static string values, and `SymbolData`
holds symbols and aliases. A string occupies exactly
`[Offset, Offset + LengthBytes)` in its buffer and is valid UTF-8
(RFC 3629). An empty string has both fields zero. Equal strings can share
bytes.

A qualified name is one string whose components are joined by `.`, such as
`ABB.JRA.Shutter`.

## Types

### Names

`Names` contains UTF-8 qualified names and the names of interfaces, enum
members, and struct fields.

### ValueTypes

Each `ValueTypes` entry is a `TypeRecord`: one of the records below,
selected by its first byte (`Kind`). Every entry occupies 32 bytes; the
bytes after the record's fields are zero. Runtime value sizes and
alignments are determined from this table.

```wirely
// Kinds Unit, Boolean, String, and ValueType.
struct type SimpleType {
  // Type kind.
  Kind: uint8,
}
```

```wirely
// Kind Integer.
struct type IntegerType {
  // Integer kind.
  Kind: uint8,
  // Width: 8, 16, 32, or 64.
  Bits: uint8,
  // Whether values are signed, in two's complement.
  Signed: boolean,
}
```

```wirely
// Kind Builtin: a built-in type defined by this format.
struct type BuiltinType {
  // Builtin kind.
  Kind: uint8,
  // Built-in type identifier.
  Builtin: uint32,
}
```

```wirely
// Kind Enum: a named enumeration.
struct type EnumType {
  // Enum kind.
  Kind: uint8,
  // Index into ValueTypes; underlying integer type.
  UnderlyingType: Ref,
  // Qualified name in Names.
  Name: StringRef,
  // Range into EnumMembers, ordered by number.
  Members: Range,
  // Index within Members of the default member.
  Default: uint32,
}
```

```wirely
// Kind Struct: a named struct.
struct type StructType {
  // Struct kind.
  Kind: uint8,
  // Qualified name in Names.
  Name: StringRef,
  // Range into StructFields, in declaration order.
  Fields: Range,
}
```

```wirely
// Kinds Option and List: a type containing values of another type.
struct type CompositeType {
  // Option kind, or List kind for static values.
  Kind: uint8,
  // Index into ValueTypes.
  InnerType: Ref,
}
```

`Kind` identifies the record layout and represented type. Kind numbers
will be assigned when the format is finalized.

| Kind | Record |
| --- | --- |
| `Unit`, `Boolean`, `String`, `ValueType` | `SimpleType` |
| `Integer` | `IntegerType` |
| `Builtin` | `BuiltinType` |
| `Enum` | `EnumType` |
| `Struct` | `StructType` |
| `Option`, `List` | `CompositeType` |

Built-in types are `Duration`, `TimeOfDay`, and `MonotonicTime`. Their
identifiers will be assigned when the format is finalized. Their values,
defaults, and representation are described in
[Value types](value-types.md); `Duration` and `TimeOfDay` are ordered.
Files contain only `MonotonicTime` values of generation 0.

Enum and struct names support native type reconstruction, display, and
conversions such as JSON. An enum's underlying type supplies its runtime
integer encoding, and its default member is the first declared member.

Types are equal exactly when their indices are equal. Producers intern
structurally equal types; enums and structs have one entry per declaration.
Inner, underlying, and field types occur before the types using them,
making size calculation possible in one forward pass. `String`,
`ValueType`, and lists, including composites and structs containing them,
are only used for static values in this format.

### EnumMembers

```wirely
// One named enumeration member. EnumMembers table.
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

### StructFields

```wirely
// One named struct field. StructFields table.
struct type StructField {
  // Field name in Names.
  Name: StringRef,
  // Index into ValueTypes.
  ValueType: Ref,
}
```

Field offsets follow the
[runtime binary representation](value-types.md#runtime-binary-representation).
Field index N selects entry N of the struct's `Fields` range. Field names
support native struct reconstruction, display, and JSON conversion.

### TypeLists

```wirely
// One type in a list of types. TypeLists table.
struct type TypeEntry {
  // Index into ValueTypes.
  ValueType: Ref,
}
```

`TypeLists` stores lists of types, such as the parameter types of a native
function or the locals of a procedure body, as ranges of entries. Equal
lists can share entries.

## Static data

### StaticValues

```wirely
// A typed value with a byte offset into its associated storage.
// StaticValues, StateVariables, and Temporaries tables.
struct type Variable {
  // Index into ValueTypes; resolved stored-value type.
  ValueType: Ref,
  // Byte offset into ProgramData for StaticValues, the state buffer for
  // StateVariables, or the temporary buffer for Temporaries.
  Offset: uint32,
}
```

Static values describe the resolved values bound by `uses` clauses of
native procedures and initializers, and constants read by procedure
bodies. Bindings and `const` operands reference these entries, which can
be shared for equal typed values. Their data and referenced payloads are
stored in `ProgramData`. Only values bound by `uses` clauses or read by
procedure bodies are stored; node parameters, constants, and expressions
supply those values.

### ProgramData

`ProgramData` stores static value slots, list arrays, and string payloads.
Payloads use their type's storage layout. Equal values or payloads can
share storage.

Runtime types use their runtime binary representation. Static `String`
values use `StringRef` into `ProgramData`. Options apply the runtime option
layout to the representations below, including strings and lists.

```wirely
// A static value identifying another value type.
struct type ValueTypeValue {
  // Index into ValueTypes.
  ValueType: Ref,
}
```

```wirely
// A static list identifying element slots in ProgramData.
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

## State

### StateVariables and Temporaries

`StateVariables` and `Temporaries` use the `Variable` record. Their offsets
locate values in the runtime's state buffer and temporary buffer.

Entries are ordered by ascending byte offset, preserving runtime layout
order for equal offsets. Each variable identity retains its own entry,
including zero-sized units sharing an offset.

Both tables use runtime types. Each variable occupies its type's storage
size at an offset aligned for its type, starting at or after the end of the
previous slot. The buffer alignment is the maximum type alignment, and its
size rounds the last slot's end up to that alignment. Empty buffers have
size zero and alignment 1. Slot and final buffer padding are zero. The
runtime allocates state and temporary buffers using this layout. Producers
lay out variables in decreasing type alignment, preserving variable order
within an alignment group.

### InitialState

`InitialState` has the same byte layout and size as the runtime state
buffer. It stores every state variable's initial data bytes at its declared
offset, with zero slot and final buffer padding. The runtime can copy this
buffer directly into its state buffer. A program started by a
[state transition](#state-transitions) receives its state from the
transition instead. An empty buffer has length zero.

## Native states

Native states are objects implemented by native code, such as the state of
a KNX connection. They are constructed before execution and can provide
named resources to the initializers of other native states and to
procedures.

### NativeStates

```wirely
// A native state object and its initializer bindings.
// NativeStates table.
struct type NativeState {
  // Index into NativeStateTypes.
  Type: Ref,
  // Range into StaticBindings; values bound by the initializer's `uses`.
  Uses: Range,
  // Range into ResourceInputs; constructor dependencies.
  Resources: Range,
}
```

Each native state object has its own entry in `NativeStates`.
Stateless calls, bytecode procedures, and composed nodes have no entries.
States are numbered and constructed in `native_construction_order`, with
resource providers before consumers. Construction calls the native
constructor of the state's type and supplies the initializer's resolved
`uses` values and resource inputs.

### NativeStateTypes

```wirely
// A native state implementation and its interfaces.
// NativeStateTypes table.
struct type NativeStateType {
  // Qualified name in Names.
  Name: StringRef,
  // Range into InterfaceNames; names bound by the initializer's `uses`.
  UsesNames: Range,
  // Range into ResourceInterfaces; resources the initializer receives.
  ResourceInputInterfaces: Range,
  // Range into ResourceInterfaces; resources this state provides.
  ResourceOutputInterfaces: Range,
}
```

A native state type is the runtime's view of a node type with native
state. Entries are sorted by UTF-8 name bytes, without duplicates.

### InterfaceNames

```wirely
// A name in Names. InterfaceNames table.
struct type NameRef {
  // UTF-8 name in Names.
  Name: StringRef,
}
```

`InterfaceNames` represents lists of names as ranges: the names bound by
`uses` and the output names of native procedures. Each range contains
unique names in UTF-8 bytewise lexical order.

### ResourceInterfaces

```wirely
// One resource a native interface receives or provides.
// ResourceInterfaces table.
struct type ResourceInterface {
  // Interface name in Names.
  Name: StringRef,
  // Qualified resource type name in Names, such as KNX.Bus.
  Type: StringRef,
  // 0 = one provider, 1 = ordered provider list.
  IsList: uint8,
}
```

Each range contains unique names in UTF-8 bytewise lexical order. Resource
outputs have `IsList = 0`. Until resource outputs are named in the
language, a node type's default output is named `default`.

### StaticBindings

```wirely
// Binds a static value to one name of a `uses` clause.
// StaticBindings table.
struct type StaticBinding {
  // Index into InterfaceNames; name bound by `uses`.
  Name: Ref,
  // Index into StaticValues.
  Value: Ref,
}
```

Names belong to the native procedure's `UsesNames` range for procedures
or the native state type's `UsesNames` range for initializers. Optional
parameter presence requested by `parameter_option` is represented by the
bound option value.

### ResourceInputs

```wirely
// Binds native state providers to one resource interface.
// ResourceInputs table.
struct type ResourceInput {
  // Index into ResourceInterfaces; the resource input being bound.
  Interface: Ref,
  // Range into ResourceProviders.
  Providers: Range,
}
```

The interface belongs to the native state type's
`ResourceInputInterfaces` range for construction or the native
procedure's `ResourceInputInterfaces` range for procedure calls. An
interface with `IsList = 0` has one provider; with `IsList = 1`, the
providers form an ordered sequence, which can be empty.

### ResourceProviders

```wirely
// One resource provided by a native state. ResourceProviders table.
struct type ResourceProvider {
  // Index into NativeStates.
  NativeState: Ref,
  // Index within the provider type's ResourceOutputInterfaces range.
  Output: uint32,
}
```

The provided resource has the type of the resource interface it is bound
to.

## Execution

### Stages

```wirely
// An execution stage and its ordered procedure range. Stages table.
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

### Procedures

```wirely
// A native procedure invocation or procedure body with its bindings.
// Procedures table.
struct type Procedure {
  // Index into NativeStates, or no entry for stateless calls and
  // procedure bodies.
  NativeState: OptionalRef,
  // Index into NativeProcedures, or no entry for a procedure body.
  Native: OptionalRef,
  // Index into ProcedureBodies, or no entry for a native procedure.
  Body: OptionalRef,
  // Range into StaticBindings; values bound by `uses`.
  Uses: Range,
  // Range into ResourceInputs.
  Resources: Range,
  // Range into ValueInputs.
  Inputs: Range,
  // Range into Outputs.
  Outputs: Range,
}
```

Procedures are stored in execution order, consecutively within each
stage. Each procedure has either a native procedure or a body.

The native procedure identifies the native implementation and its
interfaces. When present, the native state supplies the native state
object. Each native procedure call receives its own resolved `uses`
values; omitting `uses` gives an empty binding range. Triggers and wakeups
identify a scheduled procedure by its native state and native procedure
references. Stateless procedures retain distinct entries even when they
invoke the same native procedure.

A body procedure executes its instruction stream. Its `Uses` and
`Resources` ranges are empty, and each value input has one source.
Operands `input.N` and `output.N` select entry N of its `Inputs` and
`Outputs` ranges.

### NativeProcedures

```wirely
// A named native procedure and its interfaces. NativeProcedures table.
struct type NativeProcedure {
  // Qualified name in Names.
  Name: StringRef,
  // Range into InterfaceNames; names bound by `uses`.
  UsesNames: Range,
  // Range into ResourceInterfaces.
  ResourceInputInterfaces: Range,
  // Range into InputInterfaces.
  InputInterfaces: Range,
  // Range into InterfaceNames.
  OutputNames: Range,
}
```

The qualified name joins the name of the declaring node type and the
procedure name with `:`, such as `Std.Constant:run` for procedure `run` of
`Std.Constant`. Entries are sorted by UTF-8 name bytes, without
duplicates.

### InputInterfaces

```wirely
// One value input of a native procedure. InputInterfaces table.
struct type InputInterface {
  // Input name in Names.
  Name: StringRef,
  // 0 = one connection, 1 = ordered connection list.
  IsList: uint8,
}
```

Each range contains unique names in UTF-8 bytewise lexical order.

### ValueInputs

```wirely
// Binds variable sources to one procedure input. ValueInputs table.
struct type ValueInput {
  // Index into InputInterfaces; the input being bound. No entry for
  // procedure bodies.
  Interface: OptionalRef,
  // Range into InputSources.
  Sources: Range,
}
```

The interface belongs to the native procedure's `InputInterfaces` range.
An interface with `IsList = 0` has one source; with `IsList = 1`, the
sources form an ordered sequence, which can be empty.

### InputSources

```wirely
// One variable supplying a procedure input. InputSources table.
struct type InputSource {
  // Old state, new state, or temporaries.
  Bank: uint8,
  // Index into StateVariables or Temporaries, selected by Bank.
  Variable: Ref,
}
```

An old-state input reads the step's old-state snapshot. A new-state input
reads the state value written earlier in the step. A temporary input reads
a temporary computed during the step.

Old-state inputs compare the step's old-state snapshot with the snapshot
read by the previous step, using the variable's value-type equality.
New-state inputs compare this step's new state with the step's old state,
which the previous step produced. Temporary inputs compare this step's
values with the previous step's cached values. A difference requires
procedure execution. On the first step, procedures execute without
previous values. Triggers and wakeups also require execution. A skipped
procedure preserves its state and temporary outputs.

### Outputs

```wirely
// Binds one procedure output to a destination variable. Outputs table.
struct type Output {
  // Index into InterfaceNames; output name. No entry for procedure
  // bodies.
  Name: OptionalRef,
  // New state or temporaries.
  Bank: uint8,
  // Index into StateVariables or Temporaries, selected by Bank.
  Variable: Ref,
}
```

Native output names belong to the native procedure's `OutputNames` range.
A new-state output writes the state that becomes the next step's old
state. A temporary output writes a temporary.

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
  // Qualified name in Names.
  Name: StringRef,
  // Range into TypeLists; parameter types in argument order.
  Parameters: Range,
  // Index into ValueTypes; runtime result type.
  ReturnType: Ref,
}
```

Entries are sorted by UTF-8 name bytes, without duplicates.

### Linking

The runtime matches the qualified names of native state types, native
procedures, and native functions to native code, and binds interface names
to static values, inputs, outputs, and resources. Linking also checks that
each procedure's native state has the type its native procedure expects.
Embedded adapters can retain numeric bindings and function pointers after
linking. Native trigger names resolve to the same native procedure indices.

## ProcedureCode

`ProcedureCode` contains the instruction streams of procedure bodies. Each
instruction starts with one `uint8` opcode, followed by its operands in the
order listed below. The opcode determines the operand count and instruction
length. Opcode and operand-kind numbers remain to be assigned. Multi-byte
integers are little endian; instructions and operands require no alignment.

### Value operands

The written form uses lowercase operand kinds and zero-based indices, for
example `COPY local.0, input.1`.

| Operand kind | Index selects | Access |
| --- | --- | --- |
| `input` | Entry in the procedure's `Inputs` range | Read |
| `output` | Entry in the procedure's `Outputs` range | Read/write |
| `local` | Entry in the body's `Locals` range | Read/write |
| `const` | Entry in `StaticValues` | Read |
| `immediate` | The number itself, used as the value | Read |

Operands identify whole typed values. There are no references. Field
indices and branch displacements are integer parameters, encoded
separately from value operands.

A value operand starts with a descriptor byte. Bits 7–5 identify the operand
kind; bits 4–0 select the index encoding:

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
- An enum is the member with index N within the enum's `Members` range.

Integer values fit their type, and enum indices lie within `Members`.
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
| `LESS destination, left, right` | Assign whether `left` orders before `right`. Both have the same integer type, or the same ordered built-in type. |
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
| `RETURN` | End the procedure with its current output values. |

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

`CALL` selects an entry of `NativeFunctions`. `count` repeats the entry's
parameter count so that instructions can be decoded without the table;
they match, and the argument and destination types match the entry.
Functions are pure and return a value for every argument.

Each invocation initializes every local and output to its type's default.
For unit this is the unit value, for boolean false, for integers zero, for
options `None`, for enums their default member, for built-in types their
default, and for structs the defaults of their fields. Defaults need not
have an all-zero representation. The default `MonotonicTime` has generation
0, which is never current, so it compares to no current time. Values remain
valid and canonical, including padding and unused option payloads, as
specified by the runtime binary representation.

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
// The name of a node, variable, or procedure.
// NodeSymbols, StateVariableSymbols, TemporarySymbols, and
// ProcedureSymbols tables.
struct type Symbol {
  // Name in SymbolData.
  Name: StringRef,
}
```

Each symbol table has one entry per entry of its target table, in the
same order:

| Table | Target table | Name |
| --- | --- | --- |
| `NodeSymbols` | `NativeStates` | Node path, such as `a.b` |
| `StateVariableSymbols` | `StateVariables` | Variable path, such as `a.b.count` |
| `TemporarySymbols` | `Temporaries` | Variable path, or for output storage the procedure symbol and output key joined by `.`, such as `a.b:run.output` |
| `ProcedureSymbols` | `Procedures` | Owning node path and procedure name joined by `:`, such as `a.b:run` |

Symbols identify nodes, variables, and procedures in diagnostics and when
building [state transitions](#state-transitions).

### VariableAliases

```wirely
// An alias referring to a state or temporary variable.
// VariableAliases table.
struct type VariableAlias {
  // Alias name in SymbolData.
  Name: StringRef,
  // Old state, new state, or temporaries.
  Bank: uint8,
  // Index into StateVariables or Temporaries, selected by Bank.
  Variable: Ref,
}
```

Aliases map source names, including the inputs and outputs of nodes, to
the variable that stores their value. They are sorted by UTF-8 name bytes.
Multiple aliases can refer to one variable.

### SymbolData

`SymbolData` contains UTF-8 bytes referenced by symbol records.

An embedded reader reads the header and directory and links native code
and functions using `Names`. Symbol records and their data can be read on
demand. To print a node name, read its `NodeSymbols` entry, then stream the
referenced `SymbolData` byte range over UART. Numeric node, procedure, and
variable indices also serve as diagnostic identifiers.

Static value entries locate value slots in `ProgramData`. Strings and
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
| Native-state `instances` | `NativeStates`, `NativeStateTypes`, and `NodeSymbols` |
| `instances[*].initializer_values` | Initializer `StaticBindings` and their values |
| `native_construction_order` | Order of `NativeStates` |
| `procs` | `Stages`, `Procedures`, `NativeProcedures`, bindings, and `ProcedureSymbols` |
| `procs[*].values` | Procedure `StaticBindings` and their values |
| `procs[*].body` | `ProcedureBodies`, `TypeLists`, and `ProcedureCode` |
| `constants` | `StaticValues` and `ProgramData` |
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
its predecessor in a sorted table. Apart from a few bytes per value type,
verification needs no memory beyond the file.

A runtime may also reject a valid file that it cannot run:

- The file has a format version the runtime does not support.
- The file exceeds the runtime's limits, such as the file size, a table
  size, the number of sections, or the number of verification steps.
  The runtime may reject it before verifying it completely.
- Linking fails, because the runtime lacks a native implementation or
  function the program names, or its interface does not match.

State transitions cannot fail. Native constructors should not fail
either: returning to the previous program would require constructing
its native state again.

### Structure and references

The header has the expected magic and version, and `FileLength` is the
file length. The directory and all sections lie within the file and start
on 8-byte boundaries. Section types are strictly ascending, and each known
section has its listed `RecordType` and a length that fits that record
type. All ordinary sections are present. A missing symbol section counts
as empty, so references into it fail. Unknown sections are only checked
to lie within the file.

Bytes not referenced by any record are not checked. This includes bytes of
`ProcedureCode` outside every instruction stream.

Every reference fits its target: `Ref` and `OptionalRef` indices, ranges,
`ByteRange` byte ranges, and `StringRef` byte ranges, whose bytes are valid
UTF-8. Small enumerated fields such as `Bank` and `IsList` have listed
values, and padding is zero.

### Types and values

Inner, underlying, and field types precede the types using them, so one
pass over `ValueTypes` finds the storage size, alignment, nesting depth,
and member count of each type. The verifier keeps these per type to tell
runtime types from static-only types. Integer types have a listed width,
built-in types a known identifier, and underlying types are integers. Enum
members are in strictly ascending order and fit the underlying type, and
`Default` lies within `Members`.

`StaticValues`, list elements, and `InitialState` contain valid values in
the runtime binary representation, with zero padding. Sorted members let
enum values be found by binary search. List elements are checked with the
list's inner type, which precedes the list type, so checks terminate even
when lists share bytes.

`StateVariables` and `Temporaries` are sorted by offset, and each slot
starts at or after the end of the previous one. Comparing each entry with
its predecessor therefore shows that slots do not overlap and gives the
buffer size. Their types, and those of locals, constant and immediate
operands, and native function parameters and results, are runtime types.

### Names and bindings

Sorting turns uniqueness into a comparison with the predecessor. This
applies to ranges into `InterfaceNames`, `InputInterfaces`, and
`ResourceInterfaces`, to `NativeStateTypes`, `NativeProcedures`, and
`NativeFunctions`, and to aliases. Bindings refer to entries in the
matching range of their native procedure or native state type, and their
source and provider counts match `IsList`. A resource provider's output
lies within its type's `ResourceOutputInterfaces` and has the resource
type of the interface it is bound to. Each symbol table has as many
entries as its target table.

### Procedures and code

Stage schedules follow each other without gaps and cover the procedure
table. Each procedure has a native procedure or a body. Initializer
resource providers have lower native state indices than their consumers.

Each instruction stream is decoded once from its start. Opcodes, operand
kinds and encodings, index bounds, immediate values, operand types, and
writable destinations are checked as instructions are decoded. Branches
only go forward, so a branch target is confirmed when decoding reaches it,
using one bit per code byte or by decoding ahead.

### Producer requirements

Verifiers do not check these rules:

- Sections do not overlap each other, the header, or the directory.
- Buffers place variables in decreasing type alignment.
- Struct field and enum member names are distinct.
- Symbols within a symbol table are distinct.
- The build ID matches the unstripped file.

## State transitions

A transition file carries the state of a running program over to a new
program. It uses the same header and section directory as a program and
contains the transition's own `ValueTypes`, `EnumMembers`, `StructFields`,
`TypeLists`, `Names`, `StaticValues`, `ProgramData`, `NativeFunctions`, and
`ProcedureCode`, and a `ProcedureBodies` table with one entry: the
transition procedure. It has no stages, procedure records, bindings, or
variable tables.

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
type and its transition type have the same kind, and integers the same
width and signedness. Their inner, underlying, and field types are mapped
to each other's. Built-in types have equal identifiers, enums and structs
equal qualified names, enums equal members and defaults, and structs equal
field names.
Dependencies precede their users, so one forward pass verifies a map.
Operand types of inputs and outputs are looked up through the maps; the
types of used inputs and of all outputs are mapped.

```wirely
// Origin of one new native state object. NativeStateSources table.
struct type NativeStateSource {
  // Index into the old program's NativeStates to keep, or no entry to
  // construct a new object.
  Old: OptionalRef,
}
```

`NativeStateSources` has one entry per entry of the new program's
`NativeStates`.
