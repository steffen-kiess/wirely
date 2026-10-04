# Changelog

## 0.1.2 (Unreleased)

### Breaking changes and migration

#### Default connectors

Use `default input` and `default output` in connector declarations and
references instead of `implicit input` and `implicit output`.
`default` is a weak keyword and can also be used as an identifier.

#### Library names

Use `Time.DebounceStable` instead of `Time.DelayChange`,
`Std.DelayOneStep` instead of `Std.Delay`, and
`Time.IsInDailyInterval` instead of `Time.IsBetween`.
Use `TimeOfDay.AddWrap` instead of `TimeOfDay_AddWrap`.

#### Node instance syntax

Replace parameter parentheses with braces, parameter `=` with `:`, and
remove the final semicolon. Every node instance needs braces:

| Before | After |
| --- | --- |
| `node n: T(P = value);` | `node n: T { P: value }` |
| `node n: T;` | `node n: T {}` |
| `node n: T();` | `node n: T {}` |

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

```wrl
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

  ```wrl
  enum type Mode: uint8 {
    Off = 0,
    Heat = 1,
  }
  ```

- Declare named struct types and construct values by field name:

  ```wrl
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
