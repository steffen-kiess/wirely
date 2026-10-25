# The Wirely language

Wirely is a declarative language for connecting typed nodes. A `.wrl` file
instantiates node types and connects their outputs to inputs. A node can
compute a value, emit an event, keep state, or interact with an external
service. Several `.wrl` files passed to the compiler form one program.
`.wrl` files are UTF-8 encoded.

## A first program

```wirely
node source: Std.Constant { Type: boolean, Value: true }
node printer: Std.DebugValuePrinter { Type: boolean }
connect source -> printer;
```

`source` has a default `value boolean` output, and `printer` has a default
`value boolean` input. The connection feeds the output to the input. Save this
as `hello.wrl` and check it from the repository root with:

```sh
python3 -m wirely compile hello.wrl
```

`compile` builds the graph and checks its connections. To start the service,
use `python3 -m wirely run-service hello.wrl`; add `-i` to control it from
the terminal. Runtime service dependencies are described in
[the library reference](libraries.md). To inspect or pause a running service,
see [local control sockets](control-protocol.md#local-sockets).

## Nodes, ports, and connections

A program, like each node type body, is a graph of nodes joined by
connections. There are four kinds of nodes:

- An instance, `node name: Type { … }`, creates a node of a node type.
- A binding names a value, event, or fixed value supplied elsewhere in the
  graph.
- State keeps a value from one step to the next.
- A procedure runs code that reads its inputs and writes its outputs.

[State and execution](#state-and-execution) describes the last three.

Nodes have input and output ports. Each port has a kind, `value`, `event`,
or `fixed`, and a type, as in `value boolean`. An instance has the ports
declared by its node type. A binding has a default input and a default
output. State has a default input and the outputs `old` and `new`. A
procedure has the ports it declares. Write `name.Port` for a named port and
just `name` for a default port.

A connection joins an output port, its source, to an input port, its target.
`connect` statements and procedure reference ports create connections.
Inside a node type, its own input ports are sources and its output ports are
targets.

Parameters and constants configure nodes during compilation. Runtime values
flow through ports in each step.

## Attributes

Outer attributes `#[...]` attach to the following declaration, struct field,
enum member, or statement in a block. Inner attributes `#![...]` at the
beginning of a file, namespace, node instance, node type, struct, or enum
body attach to that item.
Except for `Allow`, they do not inherit onto its contents. Repeated
namespace declarations combine their attributes.

Attribute metadata supports dot-separated names, flags, `name = literal`,
and nested comma-separated argument lists with optional trailing commas.
The compiler defines the available attributes and their permitted targets.
Attribute names, their namespaces, and their arguments use `PascalCase` and are
case-sensitive.
`Diagram.*` attributes such as `Diagram.Position(X = 12, Y = -4)` store the
layout of diagrams and do not affect compilation; see
[diagram layout](diagram-layout.md).

Tools can add, change, rename, and remove declarations, connect ports, and
set attributes in source text with `wirely.compiler.editing`. Comments and
formatting are preserved, and only the edited files need to parse.
`wirely.compiler.outline` reads the declarations, ports, and connections of
such files; references to declarations in other files stay unresolved.

`#[Internal]` marks an implementation detail. It takes no arguments and can
appear once on an instance, node type, function, namespace, value or
resource type, constant, parameter, `input` or `output` declaration, state,
binding, struct field, or enum member. Eligible bodies also accept
`#![Internal]`. References to internal declarations remain unrestricted.
Internal declarations and contents of internal namespaces or types are
omitted from the library reference.

`#[Unordered]` on a `multi` input states that the order and number of its
connections have no meaning, as for `Std.And`. The compiler still keeps
their order.

`#[Allow(Name, ...)]` suppresses the named warnings inside the attributed
item, including everything nested in it. It can appear on files with
`#![Allow(...)]`, namespaces, node types, node instances, constants,
parameters, states, bindings, assertions, procedures, and
statements. Warnings are
reported with their name, for example `[CoincidentTriggers]`:

| Name | Reported for |
| --- | --- |
| `CoincidentTriggers` | A nested `on` block whose triggers are all unrelated to the enclosing block's. |
| `RedundantOn` | A nested `on` block that always runs because an enclosing block guarantees one of its triggers. |
| `UnguardedEvent` | An event emission outside an `on` block. |
| `Unused` | A binding that nothing in its node type reads; an output of the node type counts as a reader. |
| `NativeFunction` | A warning from the implementation of a native function. |

Attributes before `connect` apply to every connection in that statement;
attributes before an individual source apply to that source's connection.
Each connection combines statement attributes followed by source
attributes, subject to the attribute's repeatability rules.

## Declarations and names

Declarations may appear at the top level or inside a node type. Node
instantiation uses a brace body with named parameters, or `{}` when empty:

```wirely
node source: Std.Constant { Type: boolean, Value: true }
node sink: Std.DebugValuePrinter { Type: boolean }
connect source -> sink;
```

The type name may be a local name or a namespace-qualified name such as
`Std.Constant`. A program may also declare its own types:

```wirely
node type Forward {
  default input: value boolean;
  default output: value boolean;

  procedure run
    reads default input as input
    writes default output as output
  {
    output = input;
  }
}
```

To make a composite type, connect its external input to child nodes:

```wirely
node type PrintBoolean {
  default input: value boolean;
  node printer: Std.DebugValuePrinter { Type: boolean }
  connect default input -> printer;
}

node source: Std.Constant { Type: boolean, Value: true }
node sink: PrintBoolean {}
connect source -> sink;
```

A `namespace Name { ... }` groups node types under `Name.Type`. Namespaces can
contain other namespaces, and a dotted declaration names the same path:

```wirely
namespace ABB.JRA {
  node type Status {
  }
}
// Equivalent to: namespace ABB { namespace JRA { node type Status { } } }
```

Both forms define `ABB.JRA.Status`. Repeated declarations of the same namespace
path add their contents to one namespace; node type names within it must remain
unique. Namespaces contain node, enum, struct, and resource types, constants,
native functions, or other namespaces, not instances.
A namespace can share its name with a struct or enum type in the same scope
to hold functions for that type, which are called as in
`Std.Duration.FromSecs(5)`. Such a function cannot share its name with a
member of the enum.
Nodes and named ports in one node type, or at the top level, share one
namespace.
A `singleton node type` shares one instance across its uses and cannot declare
parameters. A node type whose implementation owns Python state or resources
declares a [native initializer](#fixed-ports), which creates them.

Resource types, native enums, native structs, and native functions accept
`as "Qualified.Name"` after their name. Native code and `Program` use this
native name instead of the qualified Wirely name, so the declaration can be
renamed or moved without changing its implementation, as in
`native function Parse as "KNX.ParseGroupAddress"(value: string): uint16;`.
A node type's `as "Qualified.Name"` replaces its qualified name in the
native names of its initializer and native procedures, such as
`Qualified.Name:create`; the compiler warns when none of them uses it. Each
native name identifies one declaration of its kind.

Identifiers are case-sensitive. Weak keywords `binding`, `changed`, `closed`,
`constant`, `default`, `emit`, `event`, `fail`, `fixed`, `in`, `initializer`,
`input`, `load`, `multi`, `on`, `opaque`, `open`, `output`, `reads`, `resource`,
`stage`, `state`, `type`, and `value` can also be used as identifiers. Other
keywords require the `r#` prefix when used as names, for example
`node r#node: Std.And {}`. Declarations end with `;`, except node instances
and type and namespace bodies, which end at `}`.
`//` starts a line comment, and `/* ... */` block comments may be nested.

Comma-separated lists allow an optional trailing comma, including arguments,
list literals, connection sources, and procedure ports.

## Parameters, types, and literals

A node type declares parameters with `parameter Name: Type;`. A parameter
may have a default (`= value`) or be marked `optional`. Instantiation passes
named parameters, for example `Schedule.TriggerAt { Time: "08:00" }`. Type
parameters have type `type`; a later parameter or port can refer to one:

```wirely
node type Example {
  parameter Type: type;
  parameter Initial: Type;
  default output: value Type;
  connect Initial -> default output;
}
```

Read an optional parameter as an option with `optional(Name)`.
The unqualified name must refer to an `optional parameter` in the current
node or root scope. For a parameter of type `T`, the result is `Option[T]`:
`None` when omitted and `Some(value)` when supplied. An optional `Option[T]`
supplied with `None` therefore returns `Some(None)`.

Use `Name?: expression` in a node instantiation to pass an `Option[T]` to an
optional or defaulted parameter of type `T`. `Some(value)` supplies the value;
`None` omits the argument and uses the destination's default, if any:

```wirely
node type Forward {
  optional parameter Label: string;
  node child: Child { Label?: optional(Label) }
}
```

Ordinary `Name: expression` bindings require `T`, and reading an omitted
parameter fails compilation. `optional(Name)` is a compile-time expression,
also inside procedure bodies, where it can also read an event; see
[procedure bodies](#procedure-bodies).

Declare a constant in a node type, in a namespace, or at the program root
with `constant Name: Type = expression;`. Constants use the same types as
parameters, including `type`, and cannot be overridden. Each instance
resolves all its constants once, even unused ones. Initializers may refer to
parameters and other constants in the same scope, in any declaration order.

Namespace and root constants have one value per compilation. Like functions,
they are visible in nested scopes, either qualified, as in `Limits.Max`, or
unqualified from inside; a node type's own names shadow them. Constants of a
node type are visible only in that node type. Pass them to children through
ordinary parameter bindings:

```wirely
node type Select {
  parameter Enabled: boolean;
  constant Selected: int16 = {
    let fallback: int16 = 0;
    if Enabled { 12 } else { fallback }
  };
  node child: Std.Constant { Type: int16, Value: Selected }
}
```

Use `assert condition;` or `assert condition, "message";` at the program
root, in a node type, or inside an expression block. Conditions must be
constant Boolean expressions. Block assertions can use compile-time locals;
declaration assertions can use parameters and constants declared later in
the same scope:

```wirely
node type Checked {
  assert Start != End, "Start and end must differ";
  parameter Start: int16;
  parameter End: int16;
}
```

Declaration assertions run in declaration order after constants resolve,
before initializer bindings, once per instance (once for the root and each
instantiated singleton). Block assertions follow expression evaluation order.
A false condition stops compilation. Unused node types have their declaration
assertions checked but do not evaluate them. Assertions generate no runtime
code. Messages may also use raw string literals.

Use `load assert condition;` to check a Boolean condition during loading,
even when the condition is constant. At the program root or in a node
type, it reads compile-time and fixed plain values and runs once per
instance after its dependencies are ready. Independent checks have no
specified order. Unused node types are checked but their assertions do not
run. Inside a `stage load` procedure body, the check runs when reached:

```wirely
load assert minimum <= maximum,
  "Minimum {} exceeds maximum {}", minimum, maximum;
```

An optional ordinary or raw string literal supplies the message. Each `{}`
formats the next diagnostic argument in its type's text format; write `{{`
and `}}` for literal braces. Arguments must have runtime value types and
are evaluated left to right only if the check fails. A failed check stops
loading. The default message is `Load-time assertion failed`.

Inside a load procedure body, use `load fail;` or
`load fail "message", values…;` to fail unconditionally when reached, with
the same message formatting. Its default message is `Load failed`. Failed
paths need not assign outputs or locals used after the branch.

Built-in types are `unit`, `boolean`, signed
`int8`/`int16`/`int32`/`int64`/`int128`, unsigned
`uint8`/`uint16`/`uint32`/`uint64`/`uint128`, and `string`. `type` is
used for type parameters. `Option[T]` and `List[T]` construct types from `T`.
The libraries declare the time types `Std.Duration`, `Schedule.TimeOfDay`,
and `Timer.MonotonicTime`.
See the [value type reference](value-types.md) for allowed values, runtime
status, and serialization support.

For a list value, `items.Length` returns its element count as a compile-time
`uint32` constant.

Declare a struct at top level or inside a namespace:

```wirely
struct type Reading {
  temperature: int16,
  valid: boolean,
}
node reading: Std.Constant {
  Type: Reading,
  Value: Reading { valid: true, temperature: 215 },
}
```

Fields and literal entries are comma-separated, with an optional trailing
comma. Literals name their type and supply every field exactly once, in any
order. Structs are distinct named types; fields may use other structs,
enums, options, or lists. Struct definitions cannot be recursive.

Declare a struct `native` when native code reads or builds its values, as
in `native struct type Credentials as "TwoN.Credentials" { ... }`. A
program using a native struct runs only on a runtime that knows its native
name with the same field types in the same order; field names are not
compared.
Declare a struct `opaque` when native code implements its values, as for
the library time types. Opaque structs have no struct literals or field
access; their implementation may accept string literals, such as `"PT1S"`.
A program using an opaque struct runs only on a runtime that implements its
native name with the same field types in the same order, and accepts its
static values. As for native structs, `as` gives the native name, and field
names are not compared.
Read a field with `value.field`, for example `reading.temperature`, also in
procedure bodies. Field access needs a struct type known where the code is
written, not a `type` parameter.

Declare a closed or open enum at top level or inside a namespace:

```wirely
closed enum type State {
  Idle,
  Running,
}

open enum type Protocol: uint8 {
  TCP = 1,
  UDP = 2,
}
```

A closed enum has exactly its named members and requires at least one.

Open enums specify an underlying integer type and accept every value in
its range. Named members have unique integer literal values within that
range; an open enum may have no named members. Both kinds default to zero.

Separate members with commas; a trailing comma is optional. Use
`State.Running` as a value, or `Example.State.Running` for a namespaced
enum. The Matter library declares the open `ThermostatSystemModeEnum` type.

Declare an enum `native` when native code relies on its numbers, as in
`native open enum type Protocol as "KNX.Protocol": uint8 { ... }`. A program
using a native enum runs only on a runtime that knows its native name, with
the same kind, underlying type, and, for a closed enum, member count. Use
closed native enums only for member lists that cannot change.

Use `()` for unit, `true` and `false` for booleans, decimal or `0x`/`0o`
integers for integer parameters, quoted strings for `string`, and `[a, b]`
for list values. Negative integers use a leading minus sign, such as `-42`,
and require a signed integer type. Raw strings (`r"..."` and `r#"..."#`)
are also accepted.
Durations and times of day are passed as strings, for example `"PT1S"` and
`"08:00"`. `None` is an absent `Option[T]` value, while `Some(x)` contains a
value of type `T`. Without an expected type, `Some(x)` takes `T` from `x`.
Monotonic times have no literals; use `Option[Timer.MonotonicTime]` with
`None` when a time may be absent.

Compile-time expressions support literals, parameter and constant
references, lists, generic types, expression blocks, conditionals, Boolean
operators, comparisons, integer arithmetic, and declared native functions.

## Native functions

Declare native functions at the root or inside a namespace. The library
supplies a fixed signature; a registered Python implementation supplies
its result. A `native function` can be called in procedure bodies and in
compile-time expressions. A `native constant function` is compile-time
only; use it when the function can fail or uses parameter-only types such
as `string`:

```wirely
namespace Example {
  native constant function Parse(value: string): uint16;
  native function Clamp(value: int16, low: int16, high: int16): int16;
}

constant Address: uint16 = Example.Parse("1/2/3");
```

Calls take positional arguments and may appear in constants, parameter
values and defaults, assertions, and procedure bodies. Constant functions
require compile-time arguments. Within a namespace, an unqualified name
searches enclosing scopes. Declarations may follow their calls.
A `type` result can initialize a type-valued constant or parameter.
See [native Python implementations](native-python.md#native-functions)
to supply an implementation.

## Ports and connections

An `input` or `output` declares a named port of a node type. `default input`
and `default output` declare the ports used when a connection names just the
node. For example:

```wirely
node bus: KNX.Connection { UnixPath: "/run/knxnet" }
node receiver: KNX.ValueReceiver { Type: boolean, GroupAddress: "0/4/5" }
connect bus -> receiver.Bus;
```

The connection uses `bus`'s default output and the named `Bus` input. For
a named output, write `node_name.OutputName`. Inside a type body, write
`default input` and `default output` for the type's own default ports.

A `value boolean` port carries the current value, while an `event boolean`
port carries an occurrence with a boolean payload.
An event may be absent in a step. Source and target must have the same kind
and type; Wirely does not automatically convert them. Ordinary inputs,
including fixed inputs, and the outputs of a node type need exactly one
source. A `multi value T` or `multi event T` input accepts any number of
sources; `connect a, b -> target;` connects both. `Std.And` is an example
of a multi input. A node type can connect its own multi input to a child's
multi input, which then receives all of its sources at that place, in
order; a multi input cannot be connected to other targets. Connections to
and from bindings, state, and procedures are described in
[State and execution](#state-and-execution).

Use `InputName.Length` or `(default input).Length` for an enclosing multi
input's number of sources, including forwarded sources. Inside procedures,
an input alias also supports `.Length`; reading an enclosing input's length
does not require a `reads` entry. The result is a compile-time `uint32`
constant for each instance, with zero for an empty input. Event inputs count
sources regardless of event presence. Input lengths are available when
instances specialize; they cannot select types needed earlier. The default
input can appear in expressions only as the receiver of `.Length`.

A connection source can also name a parameter or constant of the same
scope. It supplies a `value` of the declared type and cannot be a target:

```wirely
node type Fixed {
  parameter Level: uint8;
  default output: value uint8;
  connect Level -> default output;
}
```

Within a scope, parameters, constants, nodes, ports, state, bindings,
procedures, and initializers need distinct names.

### Fixed ports

Fixed values are set when a program is loaded and do not change while it
runs. Use `fixed T` for a runtime value type or a resource type. Resources
are handles to native objects, such as a KNX connection. Libraries declare
resource types for connections between service nodes and their clients:

```wirely
namespace Example {
  resource type Session;

  node type Connection {
    default output: fixed Session;
    native initializer create writes default output as "session";
  }

  node type Client {
    input Service: fixed Session;
    native initializer create reads Service;
  }
}
```

`resource type Name;` is allowed at global scope and inside namespaces,
including reopened namespaces, but not inside node types. Node and resource
types cannot share a name in the same scope. Resource names resolve through
enclosing scopes; use a qualified name such as `Example.Session` outside
that namespace. A declaration may appear after its uses.

A fixed input accepts a compile-time or fixed source of the same type.
A runtime `value` input also accepts a fixed plain value. Events are never
fixed, and fixed inputs take one source.

Resources refer to the same resource declaration to be compatible. They
can be passed as `type` arguments for generic fixed ports, but cannot be
stored in state, value or event ports, parameters, structs, `Option[T]`, or
`List[T]`. Composite nodes can forward fixed values through their inputs
and outputs.

A `native initializer name` creates the native objects of a node type, as
described in
[native objects](native-python.md#native-objects-resources-and-tasks). It
runs once when the program is loaded and can read
fixed values and compile-time values and write the fixed outputs and
bindings of its node type. A node type can have several named initializers,
each owning its own native state. An initializer's native name defaults
to `Type:name`, as for
[native procedures](#state-and-execution). Each fixed output of a node type
with an initializer has one source: a load procedure, initializer, or
connection. Load procedures and initializers run after their inputs have
been produced; their dependencies must be acyclic.

`binding name: fixed Type;` names a fixed value in a node type or at the
top level. Its source is a connection, initializer, or load procedure.
Connections, initializers, and procedures can use it as a source. Native
procedures access their node's native state through such a binding:

```wirely
node type Receiver {
  binding state: fixed ReceiverState;
  native initializer create writes state as "state";
  native procedure get_value reads state as "state";
}
```

The bundled libraries declare `KNX.Bus`, `MQTT.Broker`, and `TwoN.Device`.

## State and execution

Inside a node type, `state name: Type = value;` declares a persistent value
with an initial value. `binding name: value Type;` names a value calculated
within a step. A binding requires an explicit `value`, `event`, or
`fixed` kind.
A procedure reads its input ports, declared with `reads`, and writes its
output ports, declared with `writes`.
Procedures run in dependency order; dependencies must be acyclic.

A native procedure's native name has the form `Type:procedure`. It
defaults to the node type's `as` name, or its qualified name, followed by
`:` and the procedure name. Set it with
`as "Type:procedure"` after the procedure name, such as
`native procedure Run as "Std.Constant:run";`, to rename the procedure or
its node type without changing the implementation.

Native procedures and initializers can also read compile-time values:
parameters and constants by name, and other compile-time expressions with
`expression as name` or `expression as "key"`. See
[native compile-time values](native-python.md#compile-time-values-and-prepared-data)
for Python access. Procedures with Wirely bodies refer to parameters and
constants directly.

A procedure port is either a reference port or a typed port. A reference
port, `reference as name`, is connected to the reference: from a connection
source in `reads`, to a connection target in `writes`. An unaliased
simple identifier uses its own name. Default ports and complex references
require aliases.
Native procedures also accept `as "key"` for Python keys. For example:

```wirely
procedure run
  reads default input as current, last_value.old as previous
  writes default output as output, last_value as next
{
  output = previous;
  next = current;
}
```

A typed port, `name: value Type`, `name: event Type`, or
`name: fixed Type`, is wired with `connect`. Value and event input ports
can be `multi`; each fixed input takes one source. Native procedures
also accept `name as "key": Type`; Python uses the key, so `name` can change
without changing the native code.
Refer to a typed port as `procedure.port`, in `connect` or in a reference
port. Reference ports are not addressable; to use a produced value in
several places, produce it into a value binding:

```wirely
procedure invert
  reads value: value boolean
  writes inverted: value boolean
{
  inverted = !value;
}
procedure forward
  reads invert.inverted as value
  writes default output as output
{
  output = value;
}
connect default input -> invert.value;
```

Port names must be unique within each clause. Each input port needs exactly
one source unless it is `multi`. A binding takes its value, event, or fixed
value from its one source, and state takes its new value from its one
source. Output ports may have any number of targets.

`last_value.old` reads the previous step's state without depending on this
step's source.

Use `connect last_value.old -> target;` to feed local state into a
`value Type` input or output. It reads the initial value on the first step
and the previous step's state thereafter. Similarly, `connect name -> target;`
feeds the value of a binding calculated this step.

`last_value.new` reads the value produced this step. Use it as a
connection source or reference port in `reads`. The procedure
producing the state's source must run in the same or an earlier stage, and
the reader depends on that procedure. If the procedure is skipped because
its inputs are unchanged, it supplies its retained value.

`stage load` runs once before the first step, interleaved with initializers
in dependency order. It reads compile-time and fixed values and writes
only fixed values. Wirely bodies in this stage use runtime value types,
without state or events; native load procedures may also handle resources.
Fixed values cannot replace compile-time values in type arguments,
parameter configurations, compile-time assertions, or other compile-time
expressions.

Each step runs three stages: `stage input` reads external sources, the
default stage calculates values and updates state, and `stage output` sends
results to external systems. State updates become visible on the next step.
Connecting a value to state and reading `.old` delays it by one step; use
this to break dependency cycles.

### Procedure bodies

Write a procedure body in `.wrl` to calculate outputs from declared inputs
and node parameters:

```wirely
node type Select {
  parameter Fallback: boolean = false;
  input Enabled: value boolean;
  default input: value boolean;
  default output: value boolean;

  procedure run
    reads Enabled, default input as input
    writes default output as output
  {
    let selected = if Enabled { input } else { Fallback };
    output = selected;
  }
}
```

Inside the body, use input port names, parameters, and constants as values.
Assign each value or state output exactly once on every path through the
body. These outputs can be read after assignment on every path reaching the
read. Emit event outputs as described below. Port, parameter, and constant
names must be distinct. Use separate ports to read and write state.

`let` creates an immutable local; `let mut` permits reassignment. A later
`let` may reuse a local name, with its own type and mutability. Its
initializer sees the previous binding. In procedures, locals cannot shadow
inputs, outputs, parameters, or constants. A block's locals are visible
only inside that block; it can update mutable locals declared outside it.

Local types are inferred from their initializers, or supplied by an
annotation. Integer literals default to `int32` without an expected type.
Annotations also select literal conversions, which happen during
compilation:

```wirely
let enabled = true;
let count: uint8 = 3;
let time: Schedule.TimeOfDay = "08:00";
```

An uninitialized local requires a type annotation. Assign it on every path
before reading it; an immutable local can be assigned only once per path:

```wirely
let chosen: boolean;
if enabled { chosen = true; } else { chosen = false; }
let mut current = chosen;
current = !current;
```

An expression is runtime when it reads a procedure input, output, state
dependency, or runtime local, or assigns a runtime binding. Other
expressions evaluate during compilation and can use parameter-only types
such as `string`.

A block's locals are runtime if any statement or branch in that block has
a runtime dependency. Runtime locals must have runtime types, even when
initialized with constants. Independent nested blocks can use
compile-time locals, including inside runtime-controlled branches:

```wirely
output = {
  let text: string = "hello";
  text == "hello"
};
```

Procedure bodies follow the same block rules. An unused input does not
make a body runtime. Constant expressions inside runtime expressions
evaluate during compilation even if runtime control skips them. Wholly
constant conditionals and short-circuit expressions evaluate only the
selected branches or operands.

Expressions support parentheses, `!`, short-circuit `&&` and `||`, and
`==` and `!=` for matching value types. Options compare presence and payload;
lists compare ordered contents and length; structs compare fields and must
have the same declared type. Durations compare lengths; monotonic times
compare generation and timestamp. `!=` is the complement of `==`.
Put a struct literal in parentheses when it appears directly in an `if`
condition.

`<`, `<=`, `>`, and `>=` order integers, `Std.Duration`, and
`Schedule.TimeOfDay` values of the same type. Times of day compare within one
day, so `"23:00"` is after `"01:00"`. Comparisons do not chain: write
`a < b && b < c`.

`&`, `|`, and `^` combine same-type integers bit by bit, or booleans
without short-circuiting. `!` complements an integer within its type's
width, or negates a boolean. Signed integers use two's-complement bits.
For example, `input & 0x0008 != 0` tests bit 3.

`<<` and `>>` shift an integer, returning the same type. The count is an
integer compile-time expression, from zero through the value's width
minus one. It can use parameters, constants, and compile-time block locals.
Left shifts discard outgoing bits; right shifts extend the sign for signed
integers and fill with zeros for unsigned integers.

Integer arithmetic uses `+`, `-`, `*`, `/`, `%`, and unary `-` on operands
of the same integer type. Precedence follows Rust: unary operators, then
`*`, `/`, and `%`, then `+` and `-`, shifts, `&`, `^`, `|`, comparisons,
`&&`, and `||`. Binary operators associate left to right, except assignment
which associates right to left. Division truncates toward zero, and the
remainder has the dividend's sign. Arithmetic operators are compile-time
only; each operation must produce a value of its type, so
`200 + 100 - 150` fails as `uint8`, as does division by zero. `+` and `-`
also combine two durations; see [Duration](value-types.md#duration) for
runtime arithmetic functions.

Runtime arithmetic uses members of integer types instead.
Write a type, a dot, and the member name, as in `uint8.WrappingAdd(a, b)`
or `Type.CheckedDiv(a, b)` for a `type` parameter. Both arguments have
that type:

| Member | Result |
| --- | --- |
| `WrappingAdd`, `WrappingSub`, `WrappingMul` | Wrap around the type's range. |
| `SaturatingAdd`, `SaturatingSub`, `SaturatingMul` | Clamp to the type's minimum or maximum. |
| `CheckedAdd`, `CheckedSub`, `CheckedMul`, `CheckedDiv`, `CheckedRem` | `Some(result)`, or `None` on overflow or division by zero. |

Negate with subtraction from zero, for example
`int16.SaturatingSub(0, value)`. Members also work in compile-time
expressions.

Convert an integer or open enum value to an integer type `T` with these
members:

| Member | Result |
| --- | --- |
| `From` | The same value; every value of the argument's type must fit `T`. |
| `WrappingFrom` | The value wrapped around `T`'s range. |
| `SaturatingFrom` | The value clamped to `T`'s minimum or maximum. |
| `CheckedFrom` | `Some(value)`, or `None` when it does not fit `T`. |

An open enum converts through its underlying number. `From` accepts it when
every value of its underlying integer type fits `T`, for example
`uint16.From(mode)` for an enum with underlying type `uint8`. A literal
argument has type `T` for `From`, as in `int8.From(5)`, and `int32`
otherwise.

All concrete value types support equality at compile time. Runtime
expressions can compare times, options, and structs; strings,
lists, and aggregates containing parameter-only types are compile-time-only.
For `type` values, built-in types compare by type, `Option` and `List`
compare their inner types, and structs and enums compare by declaration.
For example, `OutputType == unit` or `Option[int16] == Option[int16]`.
Blocks contain local declarations and expression statements. A statement
ending in `;` discards its expression's value. A final expression without
`;` supplies the block's value; otherwise the block yields `()`.
Assignment is an expression yielding `()` and assigns a whole local or
output. Operands evaluate left to right; assignment evaluates its
right-hand side before storing. Blocks and conditionals used as statements
can omit `;` when they yield unit. Procedure bodies yield unit. These rules
also apply to blocks in parameter values and other compile-time expressions.

`if` requires a Boolean condition; both branches yield the same type.
Omit `else` when the branch yields unit. Runtime calls require native
functions that are not declared `constant`.

For `Option[T]` values, `.IsSome` is true when present and `.IsNone` is true
when absent. `value.UnwrapOr(fallback)` returns the contained value, or
`fallback` of type `T` when absent. As in Rust, the fallback is evaluated
even when unused, so a compile-time error in it fails compilation.
`value.Unwrap()` returns the contained value and fails compilation when
the value is `None`. It is compile-time only, for example
`constant Small: int8 = int8.CheckedFrom(Large).Unwrap();`; runtime values
use `UnwrapOr` or `if let`.

`if let Some(name) = value { … } else { … }` binds the contained value to
`name` within the first block and evaluates the `else` block when absent:

```wirely
let level = if let Some(sum) = int16.CheckedAdd(input, 1) { sum } else { 0 };
```

`Some(_)` matches without binding. Omit `else` when the branch yields unit;
`else if let` chains further patterns. The binding follows the rules for
`let`. Other patterns are not supported, and the value cannot contain `&&`
or `||` without parentheses.

Procedures can copy any runtime value, including options and structs, read
struct fields, and build options and structs with `Some(x)` and struct
literals. List literals are compile-time only.
Their ports support single and multi `value` and `event` inputs and single
`value` and `event` outputs. State reads must be listed in
`reads`, using `.old` or `.new`.

Procedure bodies are pure: they read declared values and write declared
outputs. They cannot access resources, clocks, or deadlines.
The engine runs them initially and when dependencies change, retaining
outputs while dependencies are unchanged. Local mutation is confined to
one invocation.

Iterate over a multi value or event dependency with `for`:

```wirely
let mut result = true;
for value in inputs {
  result = result && value;
}
output = result;
```

The source directly names a multi dependency, declared as a typed
port or referenced through `reads`. Multi inputs support iteration
only. Iteration follows connection order, and an empty input executes no
iterations. The loop variable is immutable and scoped to the unit-valued
body. Loops yield unit and can omit a trailing semicolon.

Loops are available only in procedure bodies and cannot nest. Initialize
outer locals before reading them in a loop, and use mutable locals for
accumulation. Initialization after a loop is checked as if the loop could
be empty, regardless of its connected sources. Assign outputs after the
loop; outer immutable bindings cannot be assigned inside it.

Loops are expanded during compilation; bodies have no runtime loops or
recursion. This keeps an unrelated computation
from holding up actions such as switching on a light. The goal is prompt,
predictable home automation, rather than a hard real-time guarantee;
execution cost still depends on the body and the size of its values.

An `on` block runs when at least one of its comma-separated triggers holds
in this step. An event trigger directly names an event dependency or the
loop variable over a multi event dependency; a multi event dependency is
present when any of its sources is. State-change triggers are described
below. Inside a block whose only trigger is an
`event T` dependency, its name reads the payload as `T`, also in nested
blocks; elsewhere, reading the name fails compilation. `optional(e)` reads
an event dependency or a loop variable over a multi event dependency
anywhere in the body as `Option[T]`: `None` when absent and
`Some(payload)` when present.

```wirely
let mut next = previous;
on press { next = !previous; }
on reset { next = reset; }
toggled = next;
last_reset = optional(reset).UnwrapOr(previous_reset);
```

When several events are present in the same step, each of their blocks
runs, in order. A block nested in another with only unrelated triggers runs
only when those triggers hold in the same step, and causes a
`CoincidentTriggers` warning; a nested block that always runs causes a
`RedundantOn` warning.
`on` blocks yield unit, have no `else`, and are available only in procedure
bodies. Store an event's effect in state to keep it after the event ends.

Emit an `event T` output with `emit output(value)`, where `value` has type
`T`. For `event unit`, use `emit output` or `emit output(())`.
`emit? output(option)` accepts `Option[T]`: `Some(value)` emits the payload,
and `None` emits nothing. Both forms yield unit and can end a block.
An output not emitted during an invocation is absent. For an
`event Option[T]` output, `emit output(None)` emits an absent payload;
`emit? output(None)` emits no event.

Use an event binding to share an event between procedures or connections:

```wirely
binding forwarded: event uint8;
procedure forward reads input writes forwarded {
  on input { emit forwarded(input); }
}
connect forwarded -> target;
```

Consumers declare `forwarded` in `reads` and read it with `on` or
`optional(...)`, like an event input. A connected event source can also
supply the binding directly.

Event outputs cannot be read or assigned with `=`. Emit each output at most
once on every path; `emit?` counts even when its option is `None`. Mutually
exclusive branches may each emit to the same output. To combine events in
a loop, collect a payload locally and emit once afterward:

```wirely
on inputs {
  let mut result: Option[Command] = None;
  for item in inputs {
    on item { result = Some(item); }
  }
  emit? output(result);
}
```

This example selects the last present input in connection order.
Emissions outside an `on` block cause an `UnguardedEvent` warning: an event
that stays present can repeatedly cause external actions. Any enclosing
`on` block guards an emission; an ordinary `if` does not. Use
`#[Allow(UnguardedEvent)]` on a statement, procedure, or enclosing item for
intentional unguarded emissions.

A state-change trigger, `on changed name`, runs when the state's value in
this step differs from its previous value. Name an explicit `.new`
read or an explicit state reference in `writes`, using its
procedure-local alias. For a state output, assign it on every path reaching
the block:

```wirely
procedure update reads input writes saved as next, output {
  next = input;
  on changed next { emit output(next); }
}
```

The first step compares against the state's declared initial value. The
comparison implicitly reads `.old`; reading the old value in the body
still requires an explicit dependency. Events emitted by the block
disappear when the state settles.

Combine triggers with commas, as in `on input, changed next`. The block
runs when any trigger holds; this does not guarantee that `input` is
present. Aliases of the same state denote the same change condition for
nesting warnings. State-change blocks also guard emissions. Unguarded
emission warnings suggest available state-change targets.

Use native procedures for external effects or operations outside this
expression subset. Their `.wrl` declarations specify the interface and
stage; Python supplies the implementation. See the
[native Python implementation guide](native-python.md).

Node types can also combine child nodes and connections in `.wrl`, as
`KNX.ValueProvider` does. See the
[library reference](libraries.md) for available node types and their ports.
