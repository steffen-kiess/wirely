# The Wirely language

Wirely is a declarative language for connecting typed nodes. A `.wrl` file
instantiates node types and connects their outputs to inputs. A node can
compute a value, emit an event, keep state, or interact with an external
service. Several `.wrl` files passed to the compiler form one program.

## A first program

```wrl
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
use `python3 -m wirely run-service hello.wrl`. Runtime service dependencies
are described in [the library reference](libraries.md).

## Attributes

Outer attributes `#[...]` attach to the following declaration, struct field,
or enum member. Inner attributes `#![...]` at the beginning of a file,
namespace, node instance, node type, struct, or enum body attach to that item.
They do not inherit onto its contents. Repeated namespace declarations
combine their attributes.

Attribute metadata supports dot-separated names, flags, `name = literal`,
and nested comma-separated argument lists with optional trailing commas.
The compiler defines the available attributes and their permitted targets.
Currently, `position` supplies node-instance coordinates:

```wrl
node source: Std.Constant {
  #![position(x = 120, y = -40.5)]
  Type: boolean,
  Value: true,
}
```

`position` requires one finite numeric literal for each of `x` and `y`, and
can appear once per node instance. Coordinates are compiler metadata.

Attributes before `connect` apply to every edge in that statement;
attributes before an individual source apply to that source's edge. Each
edge combines statement attributes followed by source attributes, subject
to the attribute's repeatability rules.

## Declarations and names

Declarations may appear at the top level or inside a node type. Node
instantiation uses a brace body with named parameters, or `{}` when empty:

```wrl
node source: Std.Constant { Type: boolean, Value: true }
node sink: Std.DebugValuePrinter { Type: boolean }
connect source -> sink;
```

The type name may be a local name or a namespace-qualified name such as
`Std.Constant`. A program may also declare its own types:

```wrl
node type Forward {
  default input: value boolean;
  default output: value boolean;

  procedure run
    depends on default input as input
    produces default output as output
  {
    output = input;
  }
}
```

To make a composite type, connect its external input to child nodes:

```wrl
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

```wrl
namespace ABB.JRA {
  node type Status {
  }
}
// Equivalent to: namespace ABB { namespace JRA { node type Status { } } }
```

Both forms define `ABB.JRA.Status`. Repeated declarations of the same namespace
path add their contents to one namespace; node type names within it must remain
unique. Namespaces contain node, enum, struct, and resource types or other
namespaces, not instances.
A `singleton node type` shares one instance across its uses and cannot declare
parameters. `native node type` indicates that the implementation also owns
Python state or resources. Both forms require native support when instantiated.

Identifiers are case-sensitive. Weak keywords `constant`, `default`,
`event`, `input`, `multi`, `on`, `output`, `resource`, `stage`, `state`, `type`,
and `value` can also be used as identifiers. Other keywords require the `r#`
prefix when used as names, for example `node r#node: Std.And {}`. Declarations
end with `;`, except node instances and type and namespace bodies, which end
at `}`.
`//` starts a line comment, and `/* ... */` block comments may be nested.

Comma-separated lists allow an optional trailing comma, including arguments,
list literals, connection sources, and procedure bindings.

## Parameters, types, and literals

A node type declares parameters with `parameter Name: Type;`. A parameter
may have a default (`= value`) or be marked `optional`. Instantiation passes
named parameters, for example `Time.TriggerAt { Time: "08:00" }`. Type parameters
use `ValueType`; a later parameter or connector can refer to one:

```wrl
node type Example {
  parameter Type: ValueType;
  parameter Initial: Type;
  default output: value Type;
  node constant: Std.Constant { Type: Type, Value: Initial }
  connect constant -> default output;
}
```

Read an optional parameter as an option with `parameter_option(Name)`.
The unqualified name must refer to an `optional parameter` in the current
node or root scope. For a parameter of type `T`, the result is `Option[T]`:
`None` when omitted and `Some(value)` when supplied. An optional `Option[T]`
supplied with `None` therefore returns `Some(None)`.

Use `Name?: expression` in a node instantiation to pass an `Option[T]` to an
optional or defaulted parameter of type `T`. `Some(value)` supplies the value;
`None` omits the argument and uses the destination's default, if any:

```wrl
node type Forward {
  optional parameter Label: String;
  node child: Child { Label?: parameter_option(Label) }
}
```

Ordinary `Name: expression` bindings require `T`, and reading an omitted
parameter fails compilation. The intrinsic is available in compile-time
expressions, including constants, assertions, parameter defaults and
arguments, and native `uses` bindings. Procedure bodies can read constants
computed with it. Its name is reserved for the intrinsic.

Declare a constant in a node type or at the program root with
`constant Name: Type = expression;`. Constants use the same types as
parameters, including `ValueType`, and cannot be overridden. Each instance
resolves all its constants once, even unused ones. Initializers may refer to
parameters and other constants in the same scope, in any declaration order.
Pass constants to children through ordinary parameter bindings:

```wrl
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
root or in a node type. The condition must be Boolean and may use compile-time
expressions, parameters, and constants declared later in the same scope:

```wrl
node type Checked {
  assert Start != End, "Start and end must differ";
  parameter Start: int16;
  parameter End: int16;
}
```

Assertions run in declaration order after constants resolve, before native
configuration preparation, once per instance (once for the root and each
instantiated singleton). A false condition stops compilation. Unused node types have their
assertions checked but do not evaluate them. Assertions generate no runtime
code. Messages may also use raw string literals.

Built-in types are `unit`, `boolean`, signed `int8`/`int16`/`int32`/`int64`,
unsigned `uint8`/`uint16`/`uint32`/`uint64`, `String`, `Duration`,
`TimeOfDay`, and `MonotonicTime`. `ValueType` is used for type parameters.
`Option[T]` and `List[T]` construct types from `T`.
See the [value type reference](value-types.md) for allowed values, runtime
status, and serialization support.

Declare a struct at top level or inside a namespace:

```wrl
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

Declare an enum at top level or inside a namespace:

```wrl
enum type Mode: int8 {
  Off = 0,
  Heat = 1,
}
```

Choose an underlying integer type and give each member an integer literal
within its range. Member names and values are unique. Separate members with
commas; a trailing comma is optional. Use `Mode.Heat` as a value, or
`Example.Mode.Heat` when referring to an enum in namespace `Example`.
The Matter library declares the global `ThermostatSystemModeEnum` type.

Use `()` for unit, `true` and `false` for booleans, decimal or `0x`/`0o`
integers for integer parameters, quoted strings for `String`, and `[a, b]`
for list values. Negative integers use a leading minus sign, such as `-42`,
and require a signed integer type. Raw strings (`r"..."` and `r#"..."#`)
are also accepted.
Durations and times of day are passed as strings, for example `"PT1S"` and
`"08:00"`. `None` is an absent `Option[T]` value, while `Some(x)` contains a
value of type `T`.
`MonotonicTime.Invalid` is the available monotonic-time constant. The
compile-time function `TimeOfDay.AddWrap(time, offset)` adds an `"HH:MM"`
offset and wraps at midnight.

Compile-time expressions support literals, parameter and constant
references, lists, generic types, expression blocks, conditionals, Boolean
operators, and declared native functions. General arithmetic
expressions are not currently implemented.

## Compile-time functions

Declare native functions at the root or inside a namespace. The library
supplies a fixed signature; a registered Python implementation supplies
its result.

```wrl
namespace Example {
  native function Parse(value: String): uint16;
}

constant Address: uint16 = Example.Parse("1/2/3");
```

Calls take positional arguments and may appear in constants, parameter
values and defaults, and assertions. Within a namespace, an unqualified
name searches enclosing scopes. Declarations may follow their calls.
A `ValueType` result can initialize a type-valued constant or parameter.
See [native Python implementations](native-python.md#compile-time-functions)
to supply an implementation.

## Connectors and connections

An `input` or `output` declares a named connector. `default input` and
`default output` declare the connectors used when a connection names just
the node. For example:

```wrl
node bus: KNX.Connection { UnixPath: "/run/knxnet" }
node receiver: KNX.ValueReceiver { Type: boolean, GroupAddress: "0/4/5" }
connect bus -> receiver.Bus;
```

The first connection uses `bus`'s default output and the named `Bus` input.
For a named output, write `node_name.OutputName`. Inside a type body,
`default input` and `default output` may also be connection endpoints.

Data connectors have a kind and a type: `value boolean` carries the current
value, while `event boolean` carries an occurrence with a boolean payload.
An event may be absent in a step. Source and target must have the same kind
and type; Wirely does not automatically convert them. Ordinary inputs accept
one source. A `multi value T` or `multi event T` input accepts several sources;
`connect a, b -> target;` connects both. `Std.And` is an example of a multi
input.

### Opaque resource connectors

Libraries can declare opaque resource types for connections between service
nodes and their clients:

```wrl
namespace Example {
  resource type Session;

  native node type Connection {
    default output: resource Session;
  }

  native node type Client {
    input Service: resource Session;
  }
}
```

`resource type Name;` is allowed at global scope and inside namespaces,
including reopened namespaces, but not inside node types. Node and resource
types cannot share a name in the same scope. Resource names resolve through
enclosing scopes; use a qualified name such as `Example.Session` outside
that namespace. A declaration may appear after its uses.

Resource connectors are compatible only when they refer to the same
resource declaration. Resources identify opaque service state rather than
carrying data values. They cannot be used for parameters, state, temporaries,
`ValueType` arguments, or value types such as `Option[T]` and `List[T]`.
Composite nodes can forward resources through their inputs and outputs.

The bundled libraries declare `KNX.Bus`, `MQTT.Broker`, and `TwoN.Device`.

## State and execution

Inside a node type, `state name: Type = value;` declares a persistent value
with an initial value. `temporary name: Type;` holds a value calculated
within a step. Procedures declare which inputs or temporaries they
`depend on` and which connectors or variables they `produce`. Procedures
run in dependency order; dependencies must be acyclic.

Native procedures accept `uses` after `produces`; native initializers accept
it after `depends on`. Bind node parameters and constants by name, or use
`expression as name` or `expression as "key"`. See
[native static values](native-python.md#static-values-and-prepared-data)
for binding syntax and Python access. Procedures with Wirely bodies refer
to parameters and constants directly and do not accept `uses`.

Use `reference as name` to name a procedure input or result. An unaliased
simple identifier uses its own name. Default connectors and complex
references require aliases. Names must be unique within each clause.
Native procedures also accept `as "key"` for Python keys.
For example:

```wrl
procedure run
  depends on default input as current, last_value.old as previous
  produces default output as result, last_value as next
{
  result = previous;
  next = current;
}
```

`last_value.old` reads the previous step's state without depending on this
step's writer. Dependencies can also name direct child outputs, such as
`sensor.Value as value`. Result destinations are local output connectors,
temporaries, or state variables. Arbitrary expressions and deeper paths
are not supported.

Use `connect last_value.old -> target;` to feed local state into a
`value Type` input or output. It reads the initial value on the first step
and the previous step's state thereafter.

`last_value.new` reads the value produced this step. Use it in procedure
dependencies or as a connection source. It requires a writer in the same
or an earlier stage and makes the reader depend on that writer. If the
writer is skipped because its inputs are unchanged, it supplies the
retained state value.

Procedures run in three stages: `stage input` reads external sources, the
default stage calculates values and updates state, and `stage output` sends
results to external systems. State updates become visible on the next step.
`Std.DelayOneStep` outputs the previous step's input and stores the current
input for the following step. Use it to break dependency cycles.

### Procedure bodies

Write a procedure body in `.wrl` to calculate results from declared inputs
and node parameters:

```wrl
node type Select {
  parameter Fallback: boolean = false;
  input Enabled: value boolean;
  default input: value boolean;
  default output: value boolean;

  procedure run
    depends on Enabled, default input as input
    produces default output as output
  {
    let selected = if Enabled { input } else { Fallback };
    output = selected;
  }
}
```

Inside the body, use input aliases and runtime-valued parameter and constant
names as values. Assign each result exactly once; results cannot be read.
Input, result, parameter, and constant names must be distinct. Alias state
reads and writes separately when a procedure uses both.

`let` creates an immutable local. A later `let` may reuse a local name;
locals cannot shadow inputs, results, parameters, or constants. A block's
locals are visible only inside that block. Results are assigned in the
outer body.

Local types are inferred from their initializers, or supplied by an
annotation. Integer literals default to `int32` without an expected type.
Annotations also select literal conversions, which happen during
compilation:

```wrl
let enabled = true;
let count: uint8 = 3;
let time: TimeOfDay = "08:00";
```

Locals must have runtime types. A string literal needs a runtime destination
such as `TimeOfDay` or `Duration`; `String` itself is parameter-only.

Expressions support parentheses, `!`, short-circuit `&&` and `||`, and
`==` and `!=` for matching Boolean, integer, enum, or unit types.
At compile time, they also compare `ValueType` values: built-in types
compare by type, `Option` and `List` compare their inner types, and structs
and enums compare by declaration. For example, `OutputType == unit` or
`Option[int16] == Option[int16]`.
`if` requires a Boolean condition and an `else`; both branches yield the same
type. Expression blocks may contain locals followed by a final expression
without a semicolon. These expressions also work in parameter values and
other compile-time expressions. Functions available at compile time cannot
be called in procedure bodies.

For `Option[T]` values, `.IsSome` is true when present and `.IsNone` is true
when absent.

Procedures can copy any runtime value, including options and structs.
Their connector dependencies and results currently use single `value`
connectors, not events or multi inputs. State reads must be listed in
`depends on`, using `.old` or `.new`.

Procedure bodies are pure: they read declared values and write declared
results. They cannot access resources, configuration, clocks, or deadlines.
The engine runs them initially and when dependencies change, retaining
results while dependencies are unchanged. Immutable locals and
single-assignment results keep these transformations easy to reason about.

Bodies have no loops or recursion. This keeps an unrelated computation
from holding up actions such as switching on a light. The goal is prompt,
predictable home automation, rather than a hard real-time guarantee;
execution cost still depends on the body and the size of its values.

Use native procedures for external effects or operations outside this
expression subset. Their `.wrl` declarations specify the interface and
stage; Python supplies the implementation. See the
[native Python implementation guide](native-python.md).

Node types can also combine child nodes and connections in `.wrl`, as
`KNX.ValueProvider` does. See the
[library reference](libraries.md) for available nodes and their connectors.
