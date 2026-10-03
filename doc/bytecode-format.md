# Wirely program binary format

This proposal represents `Program` definitions as indexed binary tables
for desktop and embedded runtimes. The current format version is 0, an
unstable development format.

## Numbers, alignment, and references

All multibyte numbers are little-endian. Unsigned integers use ordinary
binary representation; signed integers use two's complement. Integer
widths are explicit and independent of the reader's C ABI.

Declarations use Rust-style notation for wire bytes. Fields occur in
order, with explicit zero padding. Every declared struct has a constant
size. Arrays contain consecutive entries using that size as their stride.

An integer of width 1, 2, 4, or 8 bytes is aligned to that many bytes from
the start of the file. Fixed records align as stated; their size includes
tail padding. Byte payloads have alignment 1. For direct mapped reads, the
file base address must be aligned to 8 bytes.

Let `align(n, a) = (n + a - 1) & ~(a - 1)`. Runtime value data sizes,
storage sizes, and alignments follow the
[runtime binary representation](value-types.md#runtime-binary-representation).

All table indices are `u32`, aligned to 4 bytes and numbered from zero.
`NONE = 0xffffffff` denotes an absent reference. Valid indices range from
zero through `0xfffffffe`. IDs are local to the file. Lengths, counts, and
byte offsets use the full `u32` range; files fit within that address space.

```rust
// A consecutive slice of a fixed-size table.
// Size 8, alignment 4. Target table is specified at each use.
struct Range {
    first: u32, // First target-table entry; zero when count is zero.
    count: u32, // Number of consecutive target entries.
}
```

```rust
// A consecutive array within a byte-data section.
// Size 8, alignment 4. Target buffer and element type are specified at use.
struct ArrayRange {
    offset: u32, // Byte offset of first element; zero when count is zero.
    count: u32,  // Number of consecutive elements.
}
```

An array starts at its element type's alignment. Its byte range is
`[offset, offset + count * element_size)` within the named buffer. Table
ranges and byte ranges fit their targets.

```rust
// UTF-8 bytes within the data buffer specified by the referring field.
// Size 8, alignment 4.
struct StringRef {
    offset: u32,       // Byte offset into the associated data buffer.
    length_bytes: u32, // Number of UTF-8 bytes starting at offset.
}
```

A string occupies exactly `[offset, offset + length_bytes)` in its buffer.
An empty string has both fields zero. Equal strings can share bytes.

## Header and sections

```rust
// Identifies the image and locates its section directory.
// Size 16, alignment 4. File offset 0.
struct Header {
    magic: [u8; 4],     // 57 52 4c 59 ("WRLY").
    version: u32,       // 0 = unstable development format; current version.
    file_length: u32,   // Total file length in bytes.
    section_count: u32, // Number of immediately following directory entries.
}
```

```rust
// Locates one section and describes its contents.
// Size 16, alignment 4. Directory starts at file offset 16.
struct SectionEntry {
    offset: u32,       // Absolute byte offset of the section from file start.
    length: u32,       // Section byte length, including internal padding.
    section_type: u32, // Section's purpose, identified by name below.
    record_type: u32,  // Wire layout of the section's records or byte data.
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

```rust
// Identity of the original unstripped file.
// Size 64, alignment 1. Build ID section.
struct BuildId {
    sha512: [u8; 64], // SHA-512 digest in standard digest-byte order.
}
```

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

```rust
// A node instance with parameters and native state construction inputs.
// Size 24, alignment 4. Nodes table.
struct Node {
    node_type: u32,       // Index into node types.
    flags: u8,           // Native state and configuration-presence flags.
    _padding: [u8; 3],   // Align ranges to 4 bytes.
    parameters: Range,   // Range into parameters.
    initializers: Range, // Range into resource inputs.
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

```rust
// A typed value with a byte offset into its associated storage.
// Size 8, alignment 4. Parameter values, state, and temporaries.
struct Variable {
    value_type: u32, // Index into value types; resolved stored-value type.
    // Byte offset into Program data for Parameter values, the state buffer
    // for State variables, or the temporary buffer for Temporaries.
    offset: u32,
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

```rust
// A native method invocation with its input and result bindings.
// Size 32, alignment 4. Procedures table.
struct Procedure {
    // Index into nodes; supplies parameters and configuration.
    node: u32,
    method: u32,               // Index into native methods.
    inputs: Range,             // Range into value inputs.
    resources: Range,          // Range into resource inputs.
    results: Range,            // Range into results.
}
```

Procedures are stored in execution order, consecutively within each
stage. A procedure's method belongs to its node's node type. Its node
record supplies parameters, configuration, and native state. Triggers and
wakeups can identify a scheduled procedure by its node and method indices.

```rust
// An execution stage and its ordered procedure range.
// Size 12, alignment 4. Stages table.
struct Stage {
    stage: u8,         // 0 = input, 1 = default, 2 = output.
    _padding: [u8; 3], // Align schedule to 4 bytes.
    schedule: Range,   // Range into procedures, in execution order.
}
```

Stage records occur in execution order, including empty stages. Their
schedule ranges partition the procedure table. Empty stages have a
zero-count range.

```rust
// Binds a stored value to one named node parameter.
// Size 8, alignment 4. Parameters table.
struct Parameter {
    name: u32,  // Interface names index; parameter receiving value.
    value: u32, // Index into parameter values.
}
```

Parameter names belong to the node type's `parameter_names` range.
Optional parameter names can have entries even when their values are
absent.

```rust
// Binds variable sources to one named procedure input.
// Size 16, alignment 4. Value inputs table.
struct ValueInput {
    name: u32,        // Interface names index; input receiving sources.
    is_list: u8,      // 0 = one connection, 1 = ordered connection list.
    _padding: [u8; 3], // Align sources to 4 bytes.
    sources: Range,   // Range into input sources.
}
```

Input names belong to the node type's `value_input_names` range.
`is_list = 0` selects one connection; `is_list = 1` selects an ordered
sequence, including an empty sequence.

```rust
// One variable supplying a procedure input.
// Size 8, alignment 4. Input sources table.
struct InputSource {
    bank: u8,           // 0 = old state, 1 = temporaries.
    _padding: [u8; 3],   // Align indices to 4 bytes.
    variable: u32,      // Index into state variables or temporaries.
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

```rust
// Binds native state providers to one named resource input.
// Size 16, alignment 4. Resource inputs table.
struct ResourceInput {
    // Index into interface names; resource input receiving providers.
    name: u32,
    is_list: u8,      // 0 = one provider, 1 = ordered provider list.
    _padding: [u8; 3], // Align providers to 4 bytes.
    providers: Range, // Range into resource providers.
}
```

Resource names belong to the node type's `initializer_names` range for
construction or `resource_input_names` range for procedure calls.
`is_list = 0` selects one provider; `is_list = 1` selects an ordered
sequence, including an empty sequence.

```rust
// One node supplying its native state as a resource.
// Size 4, alignment 4. Resource providers sequence.
struct ResourceProvider {
    node: u32, // Index into nodes; supplies a native state object.
}
```

```rust
// Binds one named procedure output to a destination variable.
// Size 16, alignment 4. Results table.
struct Result {
    name: u32,         // Interface names index; output supplying value.
    bank: u8,           // 0 = next state, 1 = temporaries.
    _padding: [u8; 3],   // Align indices to 4 bytes.
    variable: u32,      // Index into state variables or temporaries.
    state_new: u32,     // New state value; index into temporaries, or NONE.
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

```rust
// A qualified node type name and its named native interfaces.
// Size 48, alignment 4. Node types table.
struct NodeType {
    components: ArrayRange,      // StringRef array in Program data; name parts.
    parameter_names: Range,      // Range into Interface names.
    initializer_names: Range,    // Range into Interface names.
    value_input_names: Range,    // Range into Interface names.
    resource_input_names: Range, // Range into Interface names.
    methods: Range,              // Range into Native methods.
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

```rust
// A named native procedure and its output names.
// Size 20, alignment 4. Native methods table.
struct NativeMethod {
    node_type: u32,      // Index into Node types; defines the method.
    name: StringRef,    // Method name in Program data.
    output_names: Range, // Range into Interface names.
}
```

Method entries are contiguous per node type, ordered by method name.
Procedure method indices are absolute within Native methods. Methods
can share their node type's input-name ranges.

```rust
// One parameter, initializer, input, or output name.
// Size 8, alignment 4. Interface names table.
struct InterfaceName {
    name: StringRef, // Interface name in Program data.
}
```

```rust
// A named enumeration associated with its runtime value type.
// Size 20, alignment 4. Enum definitions table.
struct EnumDefinition {
    name: StringRef,      // Enum name in Program data.
    value_type: u32,      // EnumType index in Value types.
    members: ArrayRange,  // EnumMember array in Program data.
}
```

`value_type` references the `EnumType` whose `definition` points to this
entry. Its `underlying_type` supplies the runtime integer encoding. Names
and member numbers support native enum reconstruction and conversions
such as JSON. Members are ordered by numeric value.

```rust
// One named enumeration member in an EnumDefinition's member array.
// Size 16, alignment 8.
struct EnumMember {
    number: u64,     // Member number; signed values use sign-extended bits.
    name: StringRef, // Member name in Program data.
}
```

Member numbers use a 64-bit representation: signed underlying integers
are sign-extended in two's complement; unsigned integers are zero-extended.
Interpretation follows the enum's underlying integer type, and each value
fits that type. Runtime enum values use their underlying integer's width.

## Value types and program data

Each entry in value types occupies 16 bytes and uses one of these
layouts. Runtime size and alignment are determined from this table.

```rust
// Variant selected by the first byte (kind); Rust-style union notation.
type TypeRecord = SimpleType | EnumType | CompositeType;
```

```rust
// Identifies a scalar value type.
// Size 16, alignment 8. Primitive and parameter-only scalar type records.
struct SimpleType {
    kind: u8,          // Scalar type code listed below.
    _padding: [u8; 15], // Pad fixed record to 16 bytes.
}
```

```rust
// Identifies an enum's runtime integer representation and named definition.
// Size 16, alignment 8. Type code 13.
struct EnumType {
    kind: u8,                  // 13 = enum.
    _padding: [u8; 3],          // Align indices to 4 bytes.
    underlying_type: u32,       // Integer type index in value types.
    definition: u32,            // Index into enum definitions.
    _trailing_padding: [u8; 4], // Pad fixed record to 16 bytes.
}
```

```rust
// Identifies an option or list type and its contained value type.
// Size 16, alignment 8. Type codes 14 and 17.
struct CompositeType {
    kind: u8,                  // 14 = Option[T], 17 = parameter-only List[T].
    _padding: [u8; 3],          // Align inner_type to 4 bytes.
    inner_type: u32,            // Index into value types.
    _trailing_padding: [u8; 8], // Pad fixed record to 16 bytes.
}
```

| Kind | Type |
| --- | --- |
| 0 | `unit` |
| 1 | `boolean` |
| 2, 3 | `int8`, `uint8` |
| 4, 5 | `int16`, `uint16` |
| 6, 7 | `int32`, `uint32` |
| 8, 9 | `int64`, `uint64` |
| 10 | `Duration` |
| 11 | `TimeOfDay` |
| 12 | `MonotonicTime` |
| 13 | Enum |
| 14 | `Option[T]` |
| 15 | `String` |
| 16 | `ValueType` |
| 17 | Parameter-only `List[T]` |

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

```rust
// A parameter value identifying another value type.
// Size 4, alignment 4.
struct ValueTypeValue {
    value_type: u32, // Index into Value types.
}
```

```rust
// A parameter list identifying aligned element slots in Program data.
// Size 8, alignment 4. Parameter-only.
struct ListValue {
    offset: u32,        // Byte offset into Program data; first element.
    element_count: u32, // Number of consecutive elements, including units.
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

```rust
// Display names for the program and its variable blocks.
// Size 24, alignment 4. Debug metadata section.
struct DebugMetadata {
    program_name: StringRef,     // Program name in Debug data.
    state_block_name: StringRef, // State block name in Debug data.
    temp_block_name: StringRef,  // Temporary block name in Debug data.
}
```

```rust
// A display name referring explicitly to a node or variable.
// Size 12, alignment 4. Node/State variable/Temporary names tables.
struct DebugName {
    // Index into Nodes, State variables, or Temporaries, selected by table.
    target: u32,
    name: StringRef, // Display name in Debug data.
}
```

Node names, State variable names, and Temporary names contain one entry
per target, ordered by target index. Counts match their target tables.
Variable block membership is defined by the variable tables.

Node names preserve `NativeInstance.name` and the string keys of
`Program.instances`, which coincide in compiler output. Images from other
sources use the same invariant.

```rust
// An alias referring to a node instance.
// Size 12, alignment 4. Node aliases and Singleton aliases tables.
struct NodeAlias {
    name: StringRef, // Declared path or type name in Debug data.
    node: u32,       // Index into Nodes.
}
```

Aliases are sorted by UTF-8 name bytes. Multiple aliases can point to
one identity.

```rust
// An alias referring to a state or temporary variable.
// Size 16, alignment 4. Variable aliases table.
struct VariableAlias {
    name: StringRef,   // Alias name in Debug data.
    bank: u8,          // 0 = state, 1 = temporaries.
    _padding: [u8; 3], // Align variable to 4 bytes.
    variable: u32,     // Index into State variables or Temporaries.
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
