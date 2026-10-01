# The Wirely language

Wirely is a declarative language for connecting typed nodes. A `.wrl` file
instantiates node types and connects their outputs to inputs. A node can
compute a value, emit an event, keep state, or interact with an external
service. Several `.wrl` files passed to the compiler form one program.

## A first program

```wrl
node source : Std.Constant(Type = boolean, Value = true);
node printer : Std.DebugValuePrinter(Type = boolean);
connect source -> printer;
```

`source` has an implicit `value boolean` output, and `printer` has an implicit
`value boolean` input. The connection feeds the output to the input. Save this
as `hello.wrl` and check it from the repository root with:

```sh
python3 -m wirely compile hello.wrl
```

`compile` builds the graph and checks its connections. To start the service,
use `python3 -m wirely run-service hello.wrl`. Runtime service dependencies
are described in [the library reference](libraries.md).

## Declarations and names

Declarations may appear at the top level or inside a node type. Node
instantiation uses `node name : Type;` or named parameters in parentheses:

```wrl
node source : Std.Constant(Type = boolean, Value = true);
node sink : Std.DebugValuePrinter(Type = boolean);
connect source -> sink;
```

The type name may be a local name or a namespace-qualified name such as
`Std.Constant`. A program may also declare its own types:

```wrl
node type Forward {
  implicit input : value boolean;
  implicit output : value boolean;

  native procedure run
    depends on implicit input as "input"
    produces implicit output as "output";
}
```

This illustrates a type declaration, but its `native procedure` needs a
matching Python implementation before the type can be instantiated. To make
a composite type entirely in `.wrl`, connect its external input to child
nodes:

```wrl
node type PrintBoolean {
  implicit input : value boolean;
  node printer : Std.DebugValuePrinter(Type = boolean);
  connect implicit input -> printer;
}

node source : Std.Constant(Type = boolean, Value = true);
node sink : PrintBoolean;
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
unique. Namespaces contain node types or other namespaces, not instances.
A `singleton node type` shares one instance across its uses and cannot declare
parameters. `native node type` indicates that the implementation also owns
Python state or resources. Both forms require native support when instantiated.

Identifiers are case-sensitive. A keyword can be used as an identifier with
the `r#` prefix, for example `node r#node : Std.And;`. Declarations end with
`;`, except type and namespace bodies, which use braces. `//` starts a line
comment, and `/* ... */` block comments may be nested.

## Parameters, types, and literals

A node type declares parameters with `parameter Name : Type;`. A parameter
may have a default (`= value`) or be marked `optional`. Instantiation passes
named arguments, for example `Time.TriggerAt(Time = "08:00")`. Type parameters
use `ValueType`; a later parameter or connector can refer to one:

```wrl
node type Example {
  parameter Type : ValueType;
  parameter Initial : Type;
  implicit output : value Type;
  node constant : Std.Constant(Type = Type, Value = Initial);
  connect constant -> implicit output;
}
```

Built-in types are `unit`, `boolean`, signed `int8`/`int16`/`int32`/`int64`,
unsigned `uint8`/`uint16`/`uint32`/`uint64`, `String`, `Duration`,
`TimeOfDay`, `MonotonicTime`, and `ThermostatSystemModeEnum`. `ValueType` is
used for type parameters. `Option[T]` and `List[T]` construct types from `T`.
See the [value type reference](value-types.md) for allowed values, runtime
status, and serialization support.

Use `()` for unit, `true` and `false` for booleans, decimal or `0x`/`0o`
integers for integer parameters, quoted strings for `String`, and `[a, b]`
for list values. Negative integers use a leading minus sign, such as `-42`,
and require a signed integer type. Raw strings (`r"..."` and `r#"..."#`)
are also accepted.
Durations and times of day are passed as strings, for example `"PT1S"` and
`"08:00"`. `None` is an absent `Option[T]` value, while `Some(x)` contains a
value of type `T`.
`MonotonicTime.Invalid` is the available monotonic-time constant. The
compile-time function `TimeOfDay_AddWrap(time, offset)` adds an `"HH:MM"`
offset and wraps at midnight.

Expressions have limited support: literals, parameter references, lists,
generic types, and the named time function above. General arithmetic
expressions are not currently implemented.

## Connectors and connections

An `input` or `output` declares a named connector. `implicit input` and
`implicit output` declare the connectors used when a connection names just
the node. For example:

```wrl
node bus : KNX.Connection(UnixPath = "/run/knxnet");
node receiver : KNX.ValueReceiver(Type = boolean, GroupAddress = "0/4/5");
connect bus -> receiver.Bus;
```

The first connection uses `bus`'s implicit output and the named `Bus` input.
For a named output, write `node_name.OutputName`. Inside a type body,
`implicit input` and `implicit output` may also be connection endpoints.

Data connectors have a kind and a type: `value boolean` carries the current
value, while `event boolean` carries an occurrence with a boolean payload.
An event may be absent in a step. Source and target must have the same kind
and type; Wirely does not automatically convert them. Ordinary inputs accept
one source. A `list value T` or `list event T` input accepts several sources;
`connect a, b -> target;` connects both. `Std.And` is an example of a list
input.

### Opaque resource connectors

Libraries can declare opaque resource types for connections between service
nodes and their clients:

```wrl
namespace Example {
  resource type Session;

  native node type Connection {
    implicit output : resource Session;
  }

  native node type Client {
    input Service : resource Session;
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

Inside a node type, `state name : Type = value;` declares a persistent value
with an initial value. `temporary name : Type;` holds a value calculated
within a step. Native procedures declare which inputs or temporaries they
`depend on` and which connectors or variables they `produce`. The engine
orders procedures from these dependencies and rejects dependency cycles.

Use `reference as "key"` to name a procedure's Python input or result.
An unaliased simple identifier uses its own name. Implicit connectors and
complex references require aliases. Input and result keys are checked for
uniqueness separately, and aliases are not automatically case-converted.
For example:

```wrl
native procedure run
  depends on implicit input as "value", last_value.old as "previous"
  produces implicit output as "result", last_value as "next";
```

`last_value.old` reads the previous step's state without depending on this
step's writer. Dependencies can also name direct child outputs, such as
`sensor.Value as "value"`. Result destinations are local output connectors,
temporaries, or state variables. Arbitrary expressions, deeper paths, and
`last_value.new` dependencies are not supported.

Procedures run in three stages: `stage input` reads external sources, the
default stage calculates values and updates state, and `stage output` sends
results to external systems. State updates become visible on the next step.
`Std.Delay` demonstrates this: it outputs the previous value and stores the
current input for the following step.

The `.wrl` declaration specifies a native procedure's interface; its code
lives in Python under `wirely/native/`; see the
[native Python implementation guide](native-python.md) for that interface.
Node types can combine child nodes and connections in `.wrl`, as
`KNX.ValueProvider` does. See the
[library reference](libraries.md) for available nodes and their connectors.
