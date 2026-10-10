# Changelog

## 0.1.3 (Unreleased)

### Breaking changes and migration

#### Shared member names

Nodes, procedures, state and temporary variables, and named connectors in
a node type or at the top level share one namespace. Rename members that
share a name, including an input and an output with the same name.

#### Procedure outputs and temporaries

Procedures cannot produce resource outputs.

Values are stored per procedure output port, and a declared temporary is
another name for its source. Temporary symbols are therefore always
`<node>:<procedure>.<port>`; declared temporaries remain available by name
as variable aliases.

### Added

- Procedure ports can be declared with a type and wired with `connect`.
  Refer to a port as `procedure.port`, also in bound ports of other
  procedures:

  ```wirely
  procedure invert
    depends on value: value boolean
    produces inverted: value boolean
  {
    inverted = !value;
  }
  connect default input -> invert.value;
  connect invert.inverted -> default output;
  ```
- Connections can target temporaries and state; state takes its value from
  a procedure output port. Bound output ports can target child inputs.
- `if` conditions accept type arguments, as in `T == Option[uint8]`.

## 0.1.2 - 2026-10-10

### Breaking changes and migration

#### Optional parameter forwarding

Replace implicit forwarding with `Foo?: parameter_option(Foo)` in node
arguments.

#### Node instance syntax

Replace parameter parentheses with braces, parameter `=` with `:`, and
remove the final semicolon. Every node instance needs braces:

| Before | After |
| --- | --- |
| `node n: T(P = value);` | `node n: T { P: value }` |
| `node n: T;` | `node n: T {}` |
| `node n: T();` | `node n: T {}` |

#### Connector syntax

Use `multi value`, `multi event`, and `multi resource` instead of `list value`,
`list event`, and `list resource` in input declarations.

Use `default input` and `default output` instead of `implicit input` and
`implicit output` in connector declarations, connection endpoints, and
procedure bindings.

#### Library and function names

Update node types and function calls as follows:

| In 0.1.1 | In 0.1.2 |
| --- | --- |
| `ExtProg.LaunchOnEvent` | `Process.LaunchOnEvent` |
| `MQTT.EventEmitter` | `MQTT.EventSender` |
| `KNX.EventEmitter` | `KNX.EventSender` |
| `Time.DelayChange` | `Time.DebounceStable` |
| `Std.Delay` | `Std.DelayOneStep` |
| `Time.IsBetween` | `Time.IsInDailyInterval` |
| `Std.OptionUnwrapOrDefault` | `Std.OptionUnwrapOr` |

#### Attribute names

Separate qualified attribute names with `.` instead of `::`.

#### Library settings

String settings are now library enums. For example, replace
`ActiveState: "Low"` with `ActiveState: GPIO.ActiveState.Low`:

| Node type | Parameter | Enum type |
| --- | --- | --- |
| `GPIO.Output` | `ActiveState` | `GPIO.ActiveState` |
| `GPIO.Output` | `Drive` | `GPIO.Drive` |
| `GPIO.Output` | `Bias` | `GPIO.Bias` |
| `Std.DetectEdge` | `EdgeType` | `Std.EdgeType` |
| `KNX.Connection` | `Protocol` | `KNX.Protocol` |
| MQTT nodes | `Format` | `MQTT.Format` |

Member names match the former strings.

`TwoN.Connection` requires an absolute `UsernamePasswordFile`. To read a
file beside the calling source file, use:

```wirely
UsernamePasswordFile:
  Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt"),
```

#### Time values

Replace `TimeOfDay_AddWrap(time, offset)` with
`TimeOfDay.WrappingAdd(time, duration)`, for example
`TimeOfDay.WrappingAdd("23:30", "PT1H")`.

`TimeOfDay` strings use two-digit hours and minutes, optionally followed by
seconds and up to nine fraction digits, such as `"08:00:30.125"`. `"24:00"`
is the end of the day.

Replace `MonotonicTime.Invalid` with `Option[MonotonicTime]` and `None`.
`Time.MonotonicTimeReached` takes `value Option[MonotonicTime]`.

#### Stricter checks

- Every input except `multi` inputs needs a source, including resource
  inputs that nothing uses.
- Each state and temporary variable needs exactly one producing procedure.
- `assert` is a keyword; write `r#assert` to use it as an identifier.

### Added

- Write procedure bodies in Wirely, in node types or at the program root.
  Bodies assign each output once and support `let`, `if`, and expressions:

  ```wirely
  procedure run
    depends on Enabled, default input as input
    produces default output as output
  {
    output = if Enabled { input } else { Fallback };
  }
  ```

  See [procedure bodies](doc/language.md#procedure-bodies).
- Declare compile-time constants with `constant Name: Type = expression;`
  and check conditions with `assert condition, "message";`.
- Expressions support ordering comparisons, equality for all value types
  (and for types at compile time), struct field access, and compile-time
  integer arithmetic. Integer types provide wrapping, saturating, and
  checked arithmetic and conversions such as `uint8.CheckedFrom(value)`.
  Options support `IsSome`, `IsNone`, `UnwrapOr(fallback)`,
  `if let Some(x) = value { ... } else { ... }`, and compile-time
  `Unwrap()`.
- Declare `native function` and `native constant function` in libraries.
  See [native functions](doc/language.md#native-functions).
- The `std` library provides arithmetic and conversions for `Duration`,
  `MonotonicTime`, and `TimeOfDay`. See [value types](doc/value-types.md).
- The `path` library provides `Path.Join`, `Path.Parent`, and
  `Path.SourceFile`.
- `wirely run-service --state-directory DIR` serves local control sockets
  for status, pausing, stepping, and state snapshots; `wirely client` sends
  these requests. See
  [local sockets](doc/control-protocol.md#local-sockets).
- `wirely.compiler.attribute_editing` adds, sets, and removes attributes
  while preserving source formatting.

## 0.1.1 - 2026-10-04

### Breaking changes and migration

#### Library names

Update node types, named arguments, and connection endpoints as follows.
Names are case-sensitive.

| In 0.1.0 | In 0.1.1 |
| --- | --- |
| `Std.DebugInput` | `Std.DebugValueSource` |
| `Std.DebugPrinter` | `Std.DebugValuePrinter` |
| `MQTT.Server` | `MQTT.Connection` |
| MQTT client input `Server` | `Broker` |
| `TwoN.Device` node type | `TwoN.Connection` |
| TwoN connection parameter `Hostname` | `Host` |
| `Time.TriggerAt` parameter `Type` | `OutputType` |
| `Time.TriggerAt` parameter `Value` | `OutputValue` |
| `Time.DelayChange` parameter `Time` | `Duration` |
| `Std.Delay` parameter `initialValue` | `InitialValue` |
| `Time.IsBetween` parameters `start`, `end` | `Start`, `End` |

Rename the parameter `type` to `Type` on `Std.Delay`,
`Std.TwoStageValueForward`, `Std.DebugValuePrinter`, `Std.DebugEventPrinter`,
`Std.DebugValueSource`, and `Std.DebugEventSource`.

The MQTT input rename applies to `ValueOptionProvider`, `ValueProvider`,
`EventEmitter`, and `EventReceiver`. For example, change
`connect broker -> publisher.Server;` to
`connect broker -> publisher.Broker;`.

#### Resource connectors

Replace integration-specific connector kinds in custom node declarations:

| In 0.1.0 | In 0.1.1 |
| --- | --- |
| `knx_bus` | `resource KNX.Bus` |
| `mqtt_server` | `resource MQTT.Broker` |
| `two_n_device` | `resource TwoN.Device` |

Custom libraries can declare their own opaque resources with
`resource type Name;`. See [resource connectors](doc/language.md#opaque-resource-connectors).

#### Runtime types

`String` and `List[T]` are now enforced as parameter-only types, including
options containing them.

#### Procedure bindings and state dependencies

Add `as "key"` aliases to implicit connectors and complex references in
`depends on` and `produces`. Simple identifiers use their own names.
Procedures that read previous state must declare that dependency explicitly;
state changes only rerun procedures that depend on them. For example:

```wirely
native procedure run
  depends on implicit input as "input", last_value.old as "previous"
  produces implicit output as "output", last_value as "next";
```

See [state and execution](doc/language.md#state-and-execution).

#### String escapes

Quoted strings use Rust-style escapes. Replace Python-style Unicode escapes
such as `\u0041` or `\U0001F600` with `\u{41}` or `\u{1F600}`. Use raw
strings such as `r"C:\logs\wirely"` to preserve literal backslashes.

### Added

- Integer literals accept unary `+` and `-`, including hexadecimal and
  octal literals, such as `-42`, `-0x2a`, and `-0o52`.
- Use `()` for unit values and `Some(value)` for present optional values;
  `None` represents absence.
- Declare named enum types with explicit integer representations and member
  values, and use qualified literals such as `Mode.Heat`:

  ```wirely
  enum type Mode: uint8 {
    Off = 0,
    Heat = 1,
  }
  ```

- Declare named struct types and construct values by field name:

  ```wirely
  struct type Reading {
    temperature: int16,
    valid: boolean,
  }
  node reading: Std.Constant(
    Type = Reading,
    Value = Reading { valid: true, temperature: 215 }
  );
  ```

  See [types and literals](doc/language.md#parameters-types-and-literals).
- Connect previous-step state directly with
  `connect last_value.old -> target;`. Use `last_value.new` in connections
  or procedure dependencies to read this step's value after its writer.
  `.old` reads the initial value on the first step.
- Attach compiler attributes with `#[...]` and `#![...]`. Set node
  coordinates with `#[position(x = 120, y = -40.5)]` before a node
  declaration. See [attributes](doc/language.md#attributes).

## 0.1.0

Initial release: typed node graphs, value and event connections, stateful
execution, composite and native nodes, and bundled KNX, MQTT, GPIO, 2N,
time, memory, CSV, and external-command libraries.
