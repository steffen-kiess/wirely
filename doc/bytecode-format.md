# Wirely program binary format

This proposal represents `Program` definitions as indexed binary tables
for desktop and embedded runtimes. The current format version is 0, an
unstable development format.

## Numbers, alignment, and references

All multibyte numbers are little-endian. Unsigned integers use ordinary
binary representation; signed integers use two's complement. Integer
widths are explicit and independent of the reader's C ABI.

Declarations use Wirely structs and their runtime serialization rules.
Fields occur in declaration order. Alignment padding and tail padding
are filled with zeros.
Every declared struct has a constant storage size. Arrays use that size
as their stride.

An integer of width 1, 2, 4, or 8 bytes is aligned to that many bytes from
the start of the file. Fixed records align as stated; their size includes
tail padding. Byte payloads have alignment 1. For direct mapped reads, the
file base address must be aligned to 8 bytes.

Let `align(n, a) = (n + a - 1) & ~(a - 1)`. Runtime value data sizes,
storage sizes, and alignments follow the
[runtime binary representation](value-types.md#runtime-binary-representation).

All table indices are `uint32`, aligned to 4 bytes and numbered from zero.
`NONE = 0xffffffff` denotes an absent reference. Valid indices range from
zero through `0xfffffffe`. IDs are local to the file. Lengths, counts, and
byte offsets use the full `uint32` range; files fit within that address space.

```wirely
// A consecutive slice of a fixed-size table.
// Size 8, alignment 4. Target table is specified at each use.
struct type Range {
  // First target-table entry; zero when count is zero.
  first : uint32,
  // Number of consecutive target entries.
  count : uint32,
}
```

```wirely
// A consecutive array within a byte-data section.
// Size 8, alignment 4. Target buffer and element type are specified at use.
struct type ArrayRange {
  // Byte offset of first element; zero when count is zero.
  offset : uint32,
  // Number of consecutive elements.
  count : uint32,
}
```

An array starts at its element type's alignment. Its byte range is
`[offset, offset + count * element_size)` within the named buffer. Table
ranges and byte ranges fit their targets.

```wirely
// UTF-8 bytes within the data buffer specified by the referring field.
// Size 8, alignment 4.
struct type StringRef {
  // Byte offset into the associated data buffer.
  offset : uint32,
  // Number of UTF-8 bytes starting at offset.
  length_bytes : uint32,
}
```

A string occupies exactly `[offset, offset + length_bytes)` in its buffer.
An empty string has both fields zero. Equal strings can share bytes.

## Header and sections

```wirely
// Identifies the image and locates its section directory.
// Size 16, alignment 4. File offset 0.
struct type Header {
  // 0x594c5257; bytes 57 52 4c 59 ("WRLY").
  magic : uint32,
  // 0 = unstable development format; current version.
  version : uint32,
  // Total file length in bytes.
  file_length : uint32,
  // Number of immediately following directory entries.
  section_count : uint32,
}
```

```wirely
// Locates one section and describes its contents.
// Size 16, alignment 4. Directory starts at file offset 16.
struct type SectionEntry {
  // Absolute byte offset of the section from file start.
  offset : uint32,
  // Section byte length, including internal padding.
  length : uint32,
  // Section's purpose, identified by name below.
  section_type : uint32,
  // Wire layout of the section's records or byte data.
  record_type : uint32,
}
```

The directory contains `header.section_count` consecutive entries.
Sections occupy disjoint byte ranges starting on 8-byte boundaries, with
one section per section type. Their offsets locate bytes independently of
directory order. Numeric section-type and record-type assignments will be
specified when the format is finalized.

A known `record_type` permits structural decoding of an unknown
`section_type`. Resolving section-specific references requires knowledge
of that section's semantics.

| Section | Struct | Layout |
| --- | --- | --- |
| Build ID | `BuildId` | Single |
| Value types | `TypeRecord` | Fixed |
| Parameter values | `Variable` | Fixed |
| Nodes | `Node` | Fixed |
| State variables | `Variable` | Fixed |
| Temporaries | `Variable` | Fixed |
| Procedures | `Procedure` | Fixed |
| Stages | `Stage` | Fixed |
| Parameters | `Parameter` | Fixed |
| Value inputs | `ValueInput` | Fixed |
| Input sources | `InputSource` | Fixed |
| Resource inputs | `ResourceInput` | Fixed |
| Resource providers | `ResourceProvider` | Fixed |
| Results | `Result` | Fixed |
| Initial state | — | Bytes |
| Program data | — | Bytes |
| Node types | `NodeType` | Fixed |
| Native methods | `NativeMethod` | Fixed |
| Interface names | `InterfaceName` | Fixed |
| Enum definitions | `EnumDefinition` | Fixed |
| Debug metadata | `DebugMetadata` | Single |
| Node names | `DebugName` | Fixed |
| State variable names | `DebugName` | Fixed |
| Temporary names | `DebugName` | Fixed |
| Node aliases | `NodeAlias` | Fixed |
| Singleton aliases | `NodeAlias` | Fixed |
| Variable aliases | `VariableAlias` | Fixed |
| Debug data | — | Bytes |

Fixed sections contain `length / record_size` consecutive records, where
`record_type` determines the record size. Their length is an exact multiple
of that size. Single sections have exactly one record's size. Byte sections
contain a buffer of `length` bytes, described by their byte-data record
type. Empty tables and buffers have length zero. Padding between sections
is outside their lengths. All ordinary program sections are present;
debug sections are optional as a group.

```wirely
// Identity of the original unstripped file.
// Size 64, alignment 8. Build ID section.
struct type BuildId {
  // Digest bytes 0 through 7, little-endian.
  word0 : uint64,
  // Digest bytes 8 through 15, little-endian.
  word1 : uint64,
  // Digest bytes 16 through 23, little-endian.
  word2 : uint64,
  // Digest bytes 24 through 31, little-endian.
  word3 : uint64,
  // Digest bytes 32 through 39, little-endian.
  word4 : uint64,
  // Digest bytes 40 through 47, little-endian.
  word5 : uint64,
  // Digest bytes 48 through 55, little-endian.
  word6 : uint64,
  // Digest bytes 56 through 63, little-endian.
  word7 : uint64,
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
// A node instance with parameters and native state construction inputs.
// Size 24, alignment 4. Nodes table.
struct type Node {
  // Index into node types.
  node_type : uint32,
  // Native state and configuration-presence flags.
  flags : uint8,
  // Three zero alignment bytes. Align ranges to 4 bytes.
  // Range into parameters.
  parameters : Range,
  // Range into resource inputs.
  initializers : Range,
}
```

Node flag bit 0 is `has_native_state`. Bit 1 marks the presence of node
configuration. Other bits are zero.

Nodes are numbered in `native_construction_order`, placing initializer
providers before consumers. All nodes retain their parameters and
procedure context. Every node has a valid `node_type` index: stateless
nodes use it to resolve procedures and interface names; composed nodes
retain it for identity and context. The state flag controls construction
of a native state object. Each node instance retains its own entry.

```wirely
// A typed value with a byte offset into its associated storage.
// Size 8, alignment 4. Parameter values, state, and temporaries.
struct type Variable {
  // Index into value types; resolved stored-value type.
  value_type : uint32,
  // Byte offset into Program data for Parameter values, the state buffer
  // for State variables, or the temporary buffer for Temporaries.
  offset : uint32,
}
```

Parameter values describe top-level node parameter values. Bindings
reference these descriptors, which can be shared for equal typed values.
Their data and referenced payloads are stored in Program data.

State and temporary entries are ordered by ascending byte offset,
preserving runtime layout order for equal offsets. Each variable identity
retains its own entry, including zero-sized units sharing an offset.

State and temporary tables use runtime types. Each runtime buffer lays out
variables in decreasing type alignment, preserving variable order within
an alignment group. Each variable occupies its type's storage size. The
buffer alignment is the maximum type alignment,
and its size rounds the last slot's end up to that alignment. Empty
buffers have size zero and alignment 1. Slot and final buffer padding are
zero. The runtime allocates state and temporary buffers using this layout.

```wirely
// A native method invocation with its input and result bindings.
// Size 32, alignment 4. Procedures table.
struct type Procedure {
  // Index into nodes; supplies parameters and configuration.
  r#node : uint32,
  // Index into native methods.
  method : uint32,
  // Range into value inputs.
  inputs : Range,
  // Range into resource inputs.
  resources : Range,
  // Range into results.
  results : Range,
}
```

Procedures are stored in execution order, consecutively within each
stage. A procedure's method belongs to its node's node type. Its node
record supplies parameters, configuration, and native state. Triggers and
wakeups can identify a scheduled procedure by its node and method indices.

```wirely
// An execution stage and its ordered procedure range.
// Size 12, alignment 4. Stages table.
struct type Stage {
  // 0 = input, 1 = default, 2 = output.
  r#stage : uint8,
  // Three zero alignment bytes. Align schedule to 4 bytes.
  // Range into procedures, in execution order.
  schedule : Range,
}
```

Stage records occur in execution order, including empty stages. Their
schedule ranges partition the procedure table. Empty stages have a
zero-count range.

```wirely
// Binds a stored value to one named node parameter.
// Size 8, alignment 4. Parameters table.
struct type Parameter {
  // Interface names index; parameter receiving value.
  name : uint32,
  // Index into parameter values.
  value : uint32,
}
```

Parameter names belong to the node type's `parameter_names` range.
Optional parameter names can have entries even when their values are
absent.

```wirely
// Binds variable sources to one named procedure input.
// Size 16, alignment 4. Value inputs table.
struct type ValueInput {
  // Interface names index; input receiving sources.
  name : uint32,
  // 0 = one connection, 1 = ordered connection list.
  is_list : uint8,
  // Three zero alignment bytes. Align sources to 4 bytes.
  // Range into input sources.
  sources : Range,
}
```

Input names belong to the node type's `value_input_names` range.
`is_list = 0` selects one connection; `is_list = 1` selects an ordered
sequence, including an empty sequence.

```wirely
// One variable supplying a procedure input.
// Size 8, alignment 4. Input sources table.
struct type InputSource {
  // 0 = old state, 1 = temporaries.
  bank : uint8,
  // Three zero alignment bytes. Align indices to 4 bytes.
  // Index into state variables or temporaries.
  variable : uint32,
}
```

A bank-0 input reads the step's old-state snapshot. A bank-1 input reads a
temporary computed during the step, including newly produced state values
exposed through `state_new` temporaries.

State inputs compare the step's old-state snapshot with the snapshot read
by the previous step, using the variable's value-type equality. Temporary
inputs compare this step's values with the previous step's cached
values. A difference requires procedure execution. On the first step,
procedures execute without previous values. Triggers and wakeups also
require execution. A skipped procedure preserves its state and temporary
results.

```wirely
// Binds native state providers to one named resource input.
// Size 16, alignment 4. Resource inputs table.
struct type ResourceInput {
  // Index into interface names; resource input receiving providers.
  name : uint32,
  // 0 = one provider, 1 = ordered provider list.
  is_list : uint8,
  // Three zero alignment bytes. Align providers to 4 bytes.
  // Range into resource providers.
  providers : Range,
}
```

Resource names belong to the node type's `initializer_names` range for
construction or `resource_input_names` range for procedure calls.
`is_list = 0` selects one provider; `is_list = 1` selects an ordered
sequence, including an empty sequence.

```wirely
// One node supplying its native state as a resource.
// Size 4, alignment 4. Resource providers sequence.
struct type ResourceProvider {
  // Index into nodes; supplies a native state object.
  r#node : uint32,
}
```

```wirely
// Binds one named procedure output to a destination variable.
// Size 16, alignment 4. Results table.
struct type Result {
  // Interface names index; output supplying value.
  name : uint32,
  // 0 = next state, 1 = temporaries.
  bank : uint8,
  // Three zero alignment bytes. Align indices to 4 bytes.
  // Index into state variables or temporaries.
  variable : uint32,
  // New state value; index into temporaries, or NONE.
  state_new : uint32,
}
```

Output names belong to the native method's `output_names` range. A bank-0
result writes the next state and its `state_new` temporary. A bank-1 result
writes a temporary and has `state_new = NONE`.

Initial state has the same byte layout and size as the runtime state
buffer. It stores every state variable's initial data bytes at its declared
offset, with zero slot and final buffer padding. The runtime can copy this
section directly into its state buffer. An empty buffer has section length
zero.

```wirely
// A qualified node type name and its named native interfaces.
// Size 48, alignment 4. Node types table.
struct type NodeType {
  // StringRef array in Program data; name parts.
  components : ArrayRange,
  // Range into Interface names.
  parameter_names : Range,
  // Range into Interface names.
  initializer_names : Range,
  // Range into Interface names.
  value_input_names : Range,
  // Range into Interface names.
  resource_input_names : Range,
  // Range into Native methods.
  methods : Range,
}
```

Each component's `StringRef` points to UTF-8 bytes in Program data.
Components preserve qualified-name boundaries, such as
`("ABB", "JRA", "Shutter")`. Each interface-name range contains unique
names in UTF-8 bytewise lexical order. Bindings use absolute indices into
Interface names.

The reader matches qualified node type and method names to native code and
binds interface names to its inputs, outputs, parameters, and initializer
resources. Embedded adapters can retain numeric bindings and function
pointers after linking. Native trigger names resolve to the same local
method indices. These names remain ordinary program data for linking
after a restart.

```wirely
// A named native procedure and its output names.
// Size 20, alignment 4. Native methods table.
struct type NativeMethod {
  // Index into Node types; defines the method.
  node_type : uint32,
  // Method name in Program data.
  name : StringRef,
  // Range into Interface names.
  output_names : Range,
}
```

Method entries are contiguous per node type, ordered by method name.
Procedure method indices are absolute within Native methods. Methods
can share their node type's input-name ranges.

```wirely
// One parameter, initializer, input, or output name.
// Size 8, alignment 4. Interface names table.
struct type InterfaceName {
  // Interface name in Program data.
  name : StringRef,
}
```

```wirely
// A named enumeration associated with its runtime value type.
// Size 20, alignment 4. Enum definitions table.
struct type EnumDefinition {
  // Enum name in Program data.
  name : StringRef,
  // EnumType index in Value types.
  value_type : uint32,
  // EnumMember array in Program data.
  members : ArrayRange,
}
```

`value_type` references the `EnumType` whose `definition` points to this
entry. Its `underlying_type` supplies the runtime integer encoding. Names
and member numbers support native enum reconstruction and conversions
such as JSON. Members are ordered by numeric value.

```wirely
// One named enumeration member in an EnumDefinition's member array.
// Size 16, alignment 8.
struct type EnumMember {
  // Member number; signed values use sign-extended bits.
  number : uint64,
  // Member name in Program data.
  name : StringRef,
}
```

Member numbers use a 64-bit representation: signed underlying integers
are sign-extended in two's complement; unsigned integers are zero-extended.
Interpretation follows the enum's underlying integer type, and each value
fits that type. Runtime enum values use their underlying integer's width.

## Value types and program data

Each entry in value types occupies 16 bytes and uses one of these
layouts. Runtime size and alignment are determined from this table.

`TypeRecord` is a `SimpleType`, `EnumType`, or `CompositeType`, selected
by its first byte (`kind`).

```wirely
// Identifies a scalar value type.
// Size 16, alignment 8. Primitive and parameter-only scalar type records.
struct type SimpleType {
  // Primitive or parameter-only scalar kind.
  kind : Kind,
  // Three zero alignment bytes.
  // Reserved; zero.
  reserved32 : uint32,
  // Zero.
  reserved64 : uint64,
}
```

```wirely
// Identifies an enum's runtime integer representation and named definition.
// Size 16, alignment 4. Kind.Enum.
struct type EnumType {
  // Kind.Enum.
  kind : Kind,
  // Three zero alignment bytes. Align indices to 4 bytes.
  // Integer type index in value types.
  underlying_type : uint32,
  // Index into enum definitions.
  definition : uint32,
  // Zero.
  reserved : uint32,
}
```

```wirely
// Identifies an option or list type and its contained value type.
// Size 16, alignment 8. Kind.Option or Kind.List.
struct type CompositeType {
  // Kind.Option or parameter-only Kind.List.
  kind : Kind,
  // Three zero alignment bytes. Align inner_type to 4 bytes.
  // Index into value types.
  inner_type : uint32,
  // Zero.
  reserved : uint64,
}
```

```wirely
// Identifies a value type record's layout and represented type.
enum type Kind : uint8 {
  Unit = 0,
  Boolean = 1,
  Int8 = 2,
  UInt8 = 3,
  Int16 = 4,
  UInt16 = 5,
  Int32 = 6,
  UInt32 = 7,
  Int64 = 8,
  UInt64 = 9,
  Duration = 10,
  TimeOfDay = 11,
  MonotonicTime = 12,
  Enum = 13,
  Option = 14,
  String = 15,
  ValueType = 16,
  // Parameter-only list.
  List = 17,
}
```

Intern structurally equal types. Inner types occur before their composite
types, and underlying integers before their enum types, making size
calculation possible in one forward pass. `String`, `ValueType`, and lists,
including composites containing them, are parameter-only for this format.

### Program data

Program data stores parameter value slots, list arrays, string and binary
payloads, node-name component arrays, and enum member arrays. Each payload
starts at its type's alignment. String bytes have alignment 1. Alignment
gaps and slot padding are zero. Equal values or payloads can share storage;
nested references form an acyclic structure.

Parameter values describe the resolved type and offset of each top-level
parameter. Runtime parameter types use exactly the same encoding,
alignment, and zero tail padding as initial-state data. Parameter `String`
values use `StringRef` into Program data: data size 8, alignment 4.
Parameter options apply the documented trailing-presence-byte layout to
the representations below, including strings and lists.

```wirely
// A parameter value identifying another value type.
// Size 4, alignment 4.
struct type ValueTypeValue {
  // Index into Value types.
  value_type : uint32,
}
```

```wirely
// A parameter list identifying aligned element slots in Program data.
// Size 8, alignment 4. Parameter-only.
struct type ListValue {
  // Byte offset into Program data; first element.
  offset : uint32,
  // Number of consecutive elements, including units.
  element_count : uint32,
}
```

For `List[T]`, `CompositeType.inner_type` selects `T` from Value types.
The list occupies `[offset, offset + element_count * storage_size(T))`
in Program data, starting at `T`'s alignment. Each element contains its
data bytes followed by zero padding to its storage size. An empty list
has both fields zero. Zero-sized units retain their element count.
Nested list elements contain `ListValue` offset/count pairs into the same
buffer.

## Debugging and lazy reads

```wirely
// Display names for the program and its variable blocks.
// Size 24, alignment 4. Debug metadata section.
struct type DebugMetadata {
  // Program name in Debug data.
  program_name : StringRef,
  // State block name in Debug data.
  state_block_name : StringRef,
  // Temporary block name in Debug data.
  temp_block_name : StringRef,
}
```

```wirely
// A display name referring explicitly to a node or variable.
// Size 12, alignment 4. Node/State variable/Temporary names tables.
struct type DebugName {
  // Index into Nodes, State variables, or Temporaries, selected by table.
  target : uint32,
  // Display name in Debug data.
  name : StringRef,
}
```

Node names, State variable names, and Temporary names contain one entry
per target, ordered by target index. Counts match their target tables.
Variable block membership is defined by the variable tables.

Node names preserve `NativeInstance.name` and the string keys of
`Program.instances`, which coincide in compiler output. Images from other
sources use the same invariant.

```wirely
// An alias referring to a node instance.
// Size 12, alignment 4. Node aliases and Singleton aliases tables.
struct type NodeAlias {
  // Declared path or type name in Debug data.
  name : StringRef,
  // Index into Nodes.
  r#node : uint32,
}
```

Aliases are sorted by UTF-8 name bytes. Multiple aliases can point to
one identity.

```wirely
// An alias referring to a state or temporary variable.
// Size 16, alignment 4. Variable aliases table.
struct type VariableAlias {
  // Alias name in Debug data.
  name : StringRef,
  // 0 = state, 1 = temporaries.
  bank : uint8,
  // Three zero alignment bytes. Align variable to 4 bytes.
  // Index into State variables or Temporaries.
  variable : uint32,
}
```

Variable aliases are also sorted by UTF-8 name bytes.

Debug data contains UTF-8 bytes referenced by debug records. All debug
references stay within the debug sections or identify ordinary program
table entries. Ordinary program records reference ordinary program data.
An external tool can strip the debug sections as a group and retain the
Build ID section.

An embedded reader reads the header and directory and links native code.
Debug records and their data can be read on demand. To print a node name,
read its Node names entry, then stream the referenced Debug data byte range
over UART. Numeric node, procedure, and variable indices also serve as
diagnostic identifiers.

Parameter descriptors locate value slots in Program data. Strings and
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
| `instances` | Nodes, node types, parameters, Program data, and debug names |
| `native_construction_order` | Node numbering |
| `procs` | Stages, procedures, and binding tables |
| `node_aliases`, `singleton_aliases`, `variables` | Debug alias tables |
| Node configuration presence | Node flag bit 1 |

Program tables preserve node, procedure, and variable identities and the
names needed to resolve native implementations. Debug sections preserve
display names and aliases.
