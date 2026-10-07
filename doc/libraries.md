# Library reference

The libraries below are available by default. Instantiate a node
as `node name: Library.Type { Parameter: value }`. Names and parameter
capitalization matter. Public library namespaces, node and resource types,
parameters, and named connectors use PascalCase, preserving acronyms such
as `KNX` and `URI`. In the signatures below, `in` and `out` mean default
connectors; named connectors are written explicitly. `value T` carries a
current value, and `event T` carries an occurrence with a payload. A dash
means no connector of that kind.

See the [language guide](language.md) for syntax and connection rules. The
declarations in [`wirely/lib/`](../wirely/lib/) are the source for exact
signatures; behavior comes from [`wirely/native/`](../wirely/native/).

## Path

The `path` library provides compile-time functions returning `String` values
using the compiler host's path conventions:

- `Path.Join(base: String, path: String)` joins paths. An absolute second
  path replaces the base; relative results stay relative and retain `..`.
- `Path.Parent(path: String)` returns the lexical parent. A bare filename
  returns `"."`; roots remain roots and trailing separators are ignored.
- `Path.SourceFile()` returns the absolute path of the file containing the
  call. Calls in library expressions and parameter defaults refer to those
  files. The source must have a filesystem filename.

These functions do not check existence or resolve symlinks and cannot be
called in runtime procedure bodies.

## Test

These compile-time functions return their argument during ordinary
compilation. With a `"wirely.test_resources"` provider in the compiler's
`services` mapping, they select test resources:

| Function | Provider method | Test resource |
| --- | --- | --- |
| `ReplaceKNXPath(default: String): String` | `get_knx_path() -> str` | Shared KNX socket path. |
| `ReplaceMQTTURI(default: String): String` | `get_mqtt_uri() -> str` | Shared MQTT broker URI. |
| `ReplaceOutputPath(default: String): String` | `get_output_path(default: str) -> str` | The default's basename in the shared test output directory. |
| `ReplaceTwoNHost(default: String): String` | `get_twon_info() -> ServerInfo` | Shared TwoN server host. |
| `ReplaceTwoNPort(default: uint16): uint16` | `get_twon_info() -> ServerInfo` | Shared TwoN server port. |
| `ReplaceTwoNTLSA(default: List[String]): List[String]` | `get_twon_info() -> ServerInfo` | Shared TwoN server TLSA record. |

The runner starts requested services before creating the engine. TwoN
reserves its port during compilation. Equal output basenames select the
same file. Set `WIRELY_TEST_KEEP_TESTDIR=1` to
retain output files after the test. Compiled test paths are valid only for
the fixture's lifetime.

## Std

General data flow, optional values, edge detection, and debug nodes.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Constant` | `Type: ValueType`, `Value: Type` | Out: `value Type`. Produces the supplied value. |
| `And` | None | In: `multi value boolean`; out: `value boolean`. True when every connected input is true, including an empty list. |
| `DelayOneStep` | `Type: ValueType`, `InitialValue: Type` | In/out: `value Type`. Outputs the previous step's input, initially `InitialValue`. Can break dependency cycles. |
| `TwoStageValueForward` | `Type: ValueType` | In/out: `value Type`. Passes the input to the output. |
| `DebugValuePrinter` | `Type: ValueType` | In: `value Type`. Prints changes in the output stage. |
| `DebugEventPrinter` | `Type: ValueType` | In: `event Type`. Prints events in the output stage. |
| `DebugValueSource` | `Type: ValueType` | Out: `value Type`. Debug-controlled value source. |
| `DebugEventSource` | `Type: ValueType` | Out: `event Type`. Debug-controlled event source. |
| `OptionNone` | `InnerType: ValueType` | Out: `value Option[InnerType]`. Produces `None`. |
| `OptionSome` | `InnerType: ValueType` | In: `value InnerType`; out: `value Option[InnerType]`. Wraps the input. |
| `OptionUnwrapOr` | `InnerType: ValueType`, `Default: InnerType` | In: `value Option[InnerType]`; out: `value InnerType`. Uses `Default` for `None`. |
| `OptionIsSome` | `InnerType: ValueType` | In: `value Option[InnerType]`; out: `value boolean`. True when a value is present. |
| `OptionCreate` | `InnerType: ValueType` | Named in: `HasValue: value boolean`, `Value: value InnerType`; out: `value Option[InnerType]`. Wraps `Value` when `HasValue` is true; otherwise produces `None`. |
| `DetectEdge` | `EdgeType: Std.EdgeType` | In: `value boolean`; out: `event boolean`. Only `Std.EdgeType.Falling` works; `Rising` and `Both` raise an error when run. |

The debug sources are intended for testing and interactive control. Their
input is supplied through the engine's debug interface, not a `.wrl`
connection. `TwoStageValueForward` is primarily a demonstration of procedure
dependencies.

## Time

Time-based values and triggers. `TimeOfDay` parameters use strings such as
`"08:00"`; `Duration` parameters use strings such as `"PT1S"`.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `MonotonicTimeReached` | None | In: `value Option[MonotonicTime]`; out: `value Option[boolean]`. True once the time is reached, false before it, or `None` if the input is `None` or not comparable to the current time. |
| `DebounceStable` | `Type: ValueType`, `Duration: Duration` | In/out: `value Type`. Outputs the initial value immediately, then accepts changes after the input remains stable for `Duration`. |
| `IsInDailyInterval` | `Start: TimeOfDay`, `End: TimeOfDay` | Out: `value boolean`. True while the current local time is in the daily interval from `Start` (inclusive) to `End` (exclusive). The interval may cross midnight; `00:00` to `24:00` is the whole day and `24:00` to `00:00` never. |
| `TriggerAt` | `Time: TimeOfDay`, `OutputType: ValueType = unit`, optional `OutputValue: OutputType` | Out: `event OutputType`. Emits around the requested time, with logic to avoid duplicate daily events during clock changes. Supply `OutputValue` when `OutputType` is not `unit`. |

## KNX

KNX nodes use a `KNX.Connection` node and a named
`Bus: resource KNX.Bus` input.
`GroupAddress` is a KNX group address string such as `"0/4/5"`.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Connection` | Optional `Host: String`, `Protocol: KNX.Protocol`, `Port: uint16`, `UnixPath: String`, `SSHTarget: String`, `IndividualAddress: String` | Out: `resource KNX.Bus`. Opens a KNX connection. |
| `ValueReceiver` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; out: `value Option[Type]`. Reads the latest value, with `None` before one is available. |
| `ValueOptionProvider` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; in: `value Option[Type]`. Exposes an optional value on the bus. |
| `ValueProvider` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; in: `value Type`. Exposes a value on the bus. |
| `EventReceiver` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; out: `event Type`. Emits received KNX events. |
| `EventSender` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; in: `event Type`. Sends KNX events. |

Value receivers and providers support `boolean` and `uint16`. KNX use
requires the `xknx` package and a reachable KNX service.

Choose one connection mode:

- `Host` with `Protocol = KNX.Protocol.TCP` or `KNX.Protocol.UDP` opens a
  direct tunnel. `Port` defaults to `3671`.
- `Protocol = KNX.Protocol.Unix` opens a local Unix socket. `UnixPath`
  defaults to `/run/knxnet`; supplying `UnixPath` implies
  `Protocol = KNX.Protocol.Unix`.
- `SSHTarget` opens a Unix socket on a remote host over SSH. It requires
  `Protocol = KNX.Protocol.Unix` unless `UnixPath` is also supplied.
- `Protocol = KNX.Protocol.Auto` uses automatic discovery.

`IndividualAddress` is supported for TCP and Unix socket connections.
Choose parameters for one connection mode. For example,
`KNX.Connection { UnixPath: "/run/knxnet" }` uses a local socket.

## ABB.JRA

These nodes decode a 16-bit ABB JRA status word into named boolean outputs.
`InterpretStatusInformation` takes `value uint16` and produces `value
boolean` outputs. `InterpretStatusInformationOption` takes `value
Option[uint16]` and produces `value Option[boolean]` outputs; a missing input
remains missing in every output. Neither node has parameters.

Both expose the same named outputs: `ManualOperation`, `BlockActive`,
`ForcedOperation`, `FrostAlarm`, `RainAlarm`, `WindAlarm`,
`AutomaticSunProtection`, `AutomaticHeatingCooling`, `DriveError`,
`DriveInMotion`, `DriveDown`, `DriveUp`, and `SendReceiveDelayActive`.

## MQTT

MQTT nodes use a `MQTT.Connection` and a named
`Broker: resource MQTT.Broker` input.
`Topic` is a topic string. Optional `Format: MQTT.Format` selects
default JSON formatting or `MQTT.Format.SnakeCase` enum encoding. Omitting
`Format` is equivalent to `MQTT.Format.Default`. `Template` is an optional
string controlling JSON output. A template embeds the encoded value at one
`%s` placeholder; `%%` represents a literal percent sign. The `URI` accepts `mqtt://host`, `mqtts://host`, or `mqtt+unix:///path`.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Connection` | `URI: String` | Out: `resource MQTT.Broker`. Connects to a broker. |
| `ValueOptionProvider` | `Type: ValueType`, `Topic: String`, optional `Format: MQTT.Format`, `Template: String` | Named in: `Broker`; in: `value Option[Type]`. Publishes JSON; `None` publishes an empty payload. |
| `ValueProvider` | Same as `ValueOptionProvider` | Named in: `Broker`; in: `value Type`. Publishes the value as JSON. |
| `EventSender` | `Type: ValueType`, `Topic: String`, optional `Format: MQTT.Format`, `Template: String` | Named in: `Broker`; in: `event Type`. Publishes each event as JSON. |
| `EventReceiver` | `Type: ValueType`, `Topic: String`, optional `Format: MQTT.Format` | Named in: `Broker`; out: `event Type`. Emits decoded messages received on the topic. |

Use types with [JSON support](value-types.md#json-and-csv-support). MQTT
requires the `aiomqtt` package and a reachable broker.

## CSV

`CSV.ValueWriter` takes `Type: ValueType` and `Filename: String`. It appends
rows from a default `value Type` input, recording the timestamp, old value,
and new value. An empty field marks an unavailable value at startup or
shutdown. It writes on changes and repeats a current value hourly. `Type`
must support CSV conversion, such as `boolean`, an integer type, or an
`Option` of one. The file is opened for append when the service starts.

## TwoN

TwoN nodes communicate with a 2N device. Create `TwoN.Connection`, then
connect its default `resource TwoN.Device` output to each client's named
`Device` input.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Connection` | `Host: String`, `Port: uint16 = 443`, `TLSA: List[String]`, `UsernamePasswordFile: String` | Out: `resource TwoN.Device`. Connects to the device. The credential file must have an absolute path and contain a username and password on separate lines. |
| `KeyEvent` | `Key: String` | Named in: `Device`; out: `event boolean`. True on press, false on release. |
| `KeyState` | `Key: String`, `Timeout: Duration = "30s"` | Named in: `Device`; out: `value boolean`. Tracks a key, assuming release after the timeout. Startup state can be wrong if the key was already pressed. |
| `IOState` | `Port: String` | Named in: `Device`; out: `value boolean`. Tracks an input or output port. |
| `SwitchState` | `SwitchNumber: int32` | Named in: `Device`; out: `value boolean`. Tracks whether a switch is active. |
| `CodeEnteredEvent` | `Code: String` | Named in: `Device`; out: `event unit`. Emits when the specified code is entered. |
| `OutputControl` | `Port: String` | Named in: `Device`; in: `event boolean`. Sets an output on or off. |

`TLSA` provides certificate association records. TwoN use needs the bundled
TwoN dependencies, device credentials, and network access to the device.

To read credentials beside the file containing the configuration, supply:

```wrl
UsernamePasswordFile:
  Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt"),
```

Credentials are read during compilation; execution uses the prepared
username and password.

## GPIO

`GPIO.Output` sets a GPIO line from a default `value boolean` input.
Parameters are `ChipDeviceFile: String`, `Name: String`,
`ActiveState: GPIO.ActiveState = GPIO.ActiveState.High`,
`Drive: GPIO.Drive = GPIO.Drive.PushPull`, and
`Bias: GPIO.Bias = GPIO.Bias.AsIs`. Active states are `High` and `Low`;
drives are `PushPull`, `OpenDrain`, and `OpenSource`; biases are `AsIs`,
`Disable`, `PullDown`, and `PullUp`. Use qualified enum literals, such as
`GPIO.Drive.OpenDrain`.

GPIO use requires the `gpio` extra (or the official `gpiod` 2.x Python
bindings) and access to the chip device.

## Memory

`Memory.ToggleFlipFlopTrigger` can switch an external impulse relay
(latching relay) on or off. Each pulse toggles the relay, so the node compares
the requested state with feedback and emits a pulse only when the state needs
to change. It takes a default `event boolean` command input (`true` for on,
`false` for off) and a named `Feedback: value Option[boolean]` input. Its
default `event OutputType` output provides the trigger pulse. If feedback is
`None`, commands are ignored. Its parameters are `OutputType: ValueType = unit`,
optional `OutputValue: OutputType`, and `MinimumDelay: Duration = "PT1S"`.
Set `OutputValue` when `OutputType` is not `unit`. The minimum delay limits
successive output events.

## Process

`Process.LaunchOnEvent` launches a shell command when an input event arrives.
Events received while a launch is pending may be combined into one launch.
It takes `ShellCommand: String` and `Type: ValueType = unit`, with a default
`event Type` input. The event payload is ignored. The command is passed to
`sh -c` in the output stage and runs with the service process's permissions.

## Matter

### Thermostat system mode

The Matter library declares this enum in the global namespace.

`ThermostatSystemModeEnum` represents the Thermostat cluster's `SystemModeEnum`
from the Matter Application Cluster Specification. Its names and numeric
values follow the [Matter Thermostat data model](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/Thermostat.xml).
Its underlying integer type is `uint8`, matching Matter's `enum8`.

| Member | Numeric value |
| --- | --- |
| `Off` | 0 |
| `Auto` | 1 |
| `Cool` | 3 |
| `Heat` | 4 |
| `EmergencyHeat` | 5 |
| `Precooling` | 6 |
| `FanOnly` | 7 |
| `Dry` | 8 |
| `Sleep` | 9 |

Write literals as `ThermostatSystemModeEnum.Heat`, using the exact type and
member names. For example:

```wrl
node mode: Std.Constant {
  Type: ThermostatSystemModeEnum,
  Value: ThermostatSystemModeEnum.Heat,
}
node modes: Std.DebugEventSource { Type: ThermostatSystemModeEnum }
node printer: Std.DebugEventPrinter { Type: ThermostatSystemModeEnum }
connect modes -> printer;
```
