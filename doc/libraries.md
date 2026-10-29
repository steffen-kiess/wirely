# Library reference

The libraries below are available by default. Instantiate a node
as `node name: Library.Type { Parameter: value }`. Names and parameter
capitalization matter. Public library namespaces, node and resource types,
parameters, and named ports use PascalCase, preserving acronyms such as
`KNX` and `URI`. In the signatures below, `in` and `out` mean default ports;
named ports are written explicitly. `value T` carries a current value, and
`event T` carries an occurrence with a payload. A dash means no port of that
kind.

See the [language guide](language.md) for syntax and connection rules. The
declarations in [`wirely/lib/`](../wirely/lib/) are the source for exact
signatures; behavior comes from [`wirely/native/`](../wirely/native/).

## Path

The `path` library provides compile-time functions returning `string` values
using the compiler host's path conventions:

- `Path.Join(base: string, path: string)` joins paths. An absolute second
  path replaces the base; relative results stay relative and retain `..`.
- `Path.Parent(path: string)` returns the lexical parent. A bare filename
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
| `ReplaceKNXPath(default: string): string` | `get_knx_path() -> str` | Shared KNX socket path. |
| `ReplaceMQTTURI(default: string): string` | `get_mqtt_uri() -> str` | Shared MQTT broker URI. |
| `ReplaceOutputPath(default: string): string` | `get_output_path(default: str) -> str` | The default's basename in the shared test output directory. |
| `ReplaceTwoNHost(default: string): string` | `get_twon_info() -> ServerInfo` | Shared TwoN server host. |
| `ReplaceTwoNPort(default: uint16): uint16` | `get_twon_info() -> ServerInfo` | Shared TwoN server port. |
| `ReplaceTwoNTLSA(default: List[string]): List[string]` | `get_twon_info() -> ServerInfo` | Shared TwoN server TLSA record. |

The runner starts requested services before creating the engine. TwoN
reserves its port during compilation. Equal output basenames select the
same file. Set `WIRELY_TEST_KEEP_TESTDIR=1` to
retain output files after the test. Compiled test paths are valid only for
the fixture's lifetime.

## Std

General data flow, optional values, edge detection, and debug nodes. The
library also declares the [`Std.Duration`](value-types.md#duration) type and
its functions.

| Node type | Parameters | Ports and behavior |
| --- | --- | --- |
| `Constant` | `Type: type`, `Value: Type` | Out: `value Type`. Produces the supplied value. |
| `And` | None | In: unordered `multi value boolean`; out: `value boolean`. True when every connected input is true, including an empty list. |
| `TwoStageValueForward` | `Type: type` | In/out: `value Type`. Passes the input to the output. |
| `DebugValuePrinter` | `Type: type` | In: `value Type`. Prints changes in the output stage. |
| `DebugEventPrinter` | `Type: type` | In: `event Type`. Prints events in the output stage. |
| `WarnOnEvent` | `Format: string`, `Type: type = unit` | In: `event Type`. Prints `Format` as a warning for each event in the output stage. `{}` marks the payload in the [text format](value-types.md#text-format) and is required exactly when `Type` is not `unit`; write `{{` and `}}` for braces. |
| `DebugValueSource` | `Type: type`, `Initial: Type` | Out: `value Type`. Debug-controlled value source; outputs `Initial` until a value is set. |
| `DebugEventSource` | `Type: type` | Out: `event Type`. Debug-controlled event source. |
| `OptionNone` | `InnerType: type` | Out: `value Option[InnerType]`. Produces `None`. |
| `OptionSome` | `InnerType: type` | In: `value InnerType`; out: `value Option[InnerType]`. Wraps the input. |
| `OptionUnwrapOr` | `InnerType: type`, `Default: InnerType` | In: `value Option[InnerType]`; out: `value InnerType`. Uses `Default` for `None`. |
| `OptionIsSome` | `InnerType: type` | In: `value Option[InnerType]`; out: `value boolean`. True when a value is present. |
| `OptionCreate` | `InnerType: type` | Named in: `HasValue: value boolean`, `Value: value InnerType`; out: `value Option[InnerType]`. Wraps `Value` when `HasValue` is true; otherwise produces `None`. |
| `DetectEdge` | `EdgeType: Std.EdgeType` | In: `value boolean`; out: `event boolean`. `Rising` detects false → true, `Falling` true → false, and `Both` either transition. Emits the new value; initialization emits nothing. |

The debug sources are intended for testing and interactive control. Their
input is supplied through the engine's debug interface or the control
protocol's [`NativeCommand`](control-protocol.md#nativecommand), not a
`.wrl` connection. `TwoStageValueForward` is primarily a demonstration of
procedure dependencies.

Commands of the debug sources take and return maps. Key 1 holds a value
as a byte string in the
[runtime binary representation](value-types.md#runtime-binary-representation),
with the data size of `Type`; event payloads have no `Option` wrapper. An
omitted argument is an empty map.

| Node type | Command | Argument | Result |
| --- | --- | --- | --- |
| `DebugValueSource` | 0 | `{}` | `{1: current value}` |
| `DebugValueSource` | 1 | `{1: new value}` | `{}` |
| `DebugEventSource` | 0 | `{}` | `{1: payload}` of the queued event, or `{}` |
| `DebugEventSource` | 1 | `{1: payload}` of an event to send | `{}`; while an event is queued, `CommandFailed` with reason 1 |

## Schedule

Daily schedules use local clock time. `Schedule.TimeOfDay` parameters use
strings such as `"08:00"`. Interval boundaries take effect at the first
representable wall-clock microsecond at or after the specified time. The
library declares the [`Schedule.TimeOfDay`](value-types.md#time-of-day) type
and its functions.

| Node type | Parameters | Ports and behavior |
| --- | --- | --- |
| `IsInDailyInterval` | `Start: Schedule.TimeOfDay`, `End: Schedule.TimeOfDay` | Out: `value Option[boolean]`. `Some(true)` while the current local time is in the daily interval from `Start` (inclusive) to `End` (exclusive), `Some(false)` outside it, and `None` while wall time is unknown. The interval may cross midnight. `00:00` to `24:00` always returns `Some(true)` and `24:00` to `00:00` always returns `Some(false)`, including while wall time is unknown. |
| `TriggerAt` | `Time: Schedule.TimeOfDay`, `OutputType: type = unit`, `OutputValue: OutputType = ()` | Out: `event OutputType`. Emits around the requested time, avoiding duplicate daily events during clock changes. Unknown time resets its arming state; restoring time does not replay missed events. Supply `OutputValue` when `OutputType` is not `unit`. |

## Timer

Timers use monotonic time, declared with its functions as
[`Timer.MonotonicTime`](value-types.md#monotonic-time). `Std.Duration`
parameters use strings such as `"PT1S"`.

| Node type | Parameters | Ports and behavior |
| --- | --- | --- |
| `MonotonicTimestamp` | `Type: type = unit` | In: `event Type`; out: `event Timer.MonotonicTime`. Emits the step's monotonic time while the input event is present, keeping the engine stepping. |
| `MonotonicTimeReached` | None | In: `value Option[Timer.MonotonicTime]`; out: `value Option[boolean]`. True once the time is reached, false before it, or `None` if the input is `None` or not comparable to the current time. |
| `DurationElapsed` | `Duration: Std.Duration` | In: `value Option[Timer.MonotonicTime]`; out: `value Option[boolean]`. True once `Duration` has elapsed since the input time, false before it, or `None` if the input is `None` or not comparable to the current time. |
| `DebounceStable` | `Type: type`, `Duration: Std.Duration` | In/out: `value Type`. Outputs the initial value immediately, then accepts changes after the input remains stable for `Duration`. If monotonic time is lost while waiting, accepts the current input immediately. |

## KNX

KNX nodes use a `KNX.Connection` node and a named
`Bus: fixed KNX.Bus` input.
`GroupAddress` is a KNX group address string such as `"0/4/5"`.

| Node type | Parameters | Ports and behavior |
| --- | --- | --- |
| `Connection` | Optional `Host: string`, `Protocol: KNX.Protocol`, `Port: uint16`, `UnixPath: string`, `SSHTarget: string`, `IndividualAddress: string` | Out: `fixed KNX.Bus`; named out `Connected: value boolean`. Connects to a KNX interface. |
| `ValueReceiver` | `Type: type`, `GroupAddress: string` | Named in: `Bus`; out: `value Option[Type]`. Reads the latest value, with `None` before one is available. |
| `ValueOptionProvider` | `Type: type`, `GroupAddress: string` | Named in: `Bus`; in: `value Option[Type]`. Exposes an optional value on the bus. |
| `ValueProvider` | `Type: type`, `GroupAddress: string` | Named in: `Bus`; in: `value Type`. Exposes a value on the bus. |
| `EventReceiver` | `Type: type`, `GroupAddress: string` | Named in: `Bus`; out: `event Type`. Emits values written to the group address; read responses are not events. |
| `EventSender` | `Type: type`, `GroupAddress: string` | Named in: `Bus`; in: `event Type`. Sends KNX events. |

Value and event nodes support `boolean`, `int8`, `uint8`, `int16`,
`uint16`, `int32`, `uint32`, and `int64`. Running KNX nodes requires the
`xknx` package and a reachable KNX service.

Choose one connection mode:

- `Host` with `Protocol = KNX.Protocol.TCP` or `KNX.Protocol.UDP` opens a
  direct tunnel. `Port` defaults to `3671`.
- `Protocol = KNX.Protocol.Unix` opens a local Unix socket. `UnixPath`
  defaults to `/run/knxnet`; supplying `UnixPath` implies
  `Protocol = KNX.Protocol.Unix`.
- `SSHTarget` opens a Unix socket on a remote host over SSH. It requires
  `Protocol = KNX.Protocol.Unix` unless `UnixPath` is also supplied.
- `Protocol = KNX.Protocol.Auto` discovers a tunnelling interface without
  KNX Secure.

`IndividualAddress` is supported for TCP and Unix socket connections.
Choose parameters for one connection mode. For example,
`KNX.Connection { UnixPath: "/run/knxnet" }` uses a local socket.

A `ValueOptionProvider` must be the only sender on its group address; use
`EventSender` for addresses that other devices write too. A connection
allows one `ValueOptionProvider` per group address. A provider answers
reads while its value is not `None`. If sending falls behind, only the
newest value is sent. A provider sends its value again after about 50
minutes without sending it.

`ValueReceiver` includes values written by the program itself. It is `None`
while disconnected and for at least one step after each disconnect, until a
value arrives. After each connect, it reads its group address unless a value
arrives first, and it reads again when no value was seen on the address for
about an hour.

`Connection` retries connecting until it succeeds, also after losing the
connection. `Connected` is true while connected and false for at least one
step after each disconnect. A closed connection is noticed when the
operating system reports it; otherwise, a lost tunnel is noticed through its
heartbeat within about two minutes. Through knxd, `Connected`
refers to the connection to knxd. After each connect, providers send their
current value. Telegrams are dropped while disconnected.

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
`Broker: fixed MQTT.Broker` input.
`Topic` is a topic string without wildcards. Optional `Format: MQTT.Format` selects
default JSON formatting or `MQTT.Format.SnakeCase` enum encoding. Omitting
`Format` is equivalent to `MQTT.Format.Default`. `Template` is an optional
string controlling JSON output. A template embeds the encoded value at one
`%s` placeholder; `%%` represents a literal percent sign. Received messages
must match the text around the placeholder exactly. The `URI` accepts `mqtt://host`, `mqtts://host`, or `mqtt+unix:///path`.

| Node type | Parameters | Ports and behavior |
| --- | --- | --- |
| `Connection` | `URI: string`, `KeepAlive: Std.Duration = "PT60S"`, optional `ClientID: string` | Out: `fixed MQTT.Broker`; named out `Connected: value boolean`. Connects to a broker. |
| `ValueOptionProvider` | `Type: type`, `Topic: string`, optional `Format: MQTT.Format`, `Template: string`, `Retain: boolean = true`, optional `LastWill: Option[Type]` | Named in: `Broker`; in: `value Option[Type]`. Publishes JSON; `None` publishes an empty payload, which also removes a retained value. |
| `ValueProvider` | Same as `ValueOptionProvider`, without `LastWill` | Named in: `Broker`; in: `value Type`. Publishes the value as JSON. |
| `EventSender` | `Type: type`, `Topic: string`, optional `Format: MQTT.Format`, `Template: string` | Named in: `Broker`; in: `event Type`. Publishes each event as JSON. |
| `EventReceiver` | `Type: type`, `Topic: string`, optional `Format: MQTT.Format`, `Template: string` | Named in: `Broker`; out: `event Type`; named out `Subscribed: value boolean`. Emits decoded messages received on the topic. |
| `ValueReceiver` | Same as `EventReceiver` | Named in: `Broker`; out: `value Option[Type]`; named out `Subscribed: value boolean`. The last value received on the topic, including a retained one. |

Use types with [JSON support](value-types.md#json-and-csv-support). MQTT
requires the `aiomqtt` package.

`Connection` retries connecting until it succeeds, also after losing the
connection. `Connected` is true while connected and false for at least one
step after each disconnect; noticing a lost connection can take up to 1.5
times `KeepAlive`, which is rounded up to whole seconds. After each connect,
providers publish their current value again. `EventSender` drops events
while disconnected. Messages can be lost around a disconnect.

Values and events of a connection are sent in order. While a value of a
provider waits to be sent, newer values of that provider are not queued;
the current value is sent after it if it differs. `EventReceiver` ignores
retained messages.

`ValueReceiver` is `None` until the first message after connecting, for an
empty payload or a message that cannot be decoded, and whenever `Subscribed`
is false.

`Subscribed` is true while connected and the broker accepted the
subscription, and, like `Connected`, false for at least one step after each
disconnect. A broker may also withhold messages without rejecting the
subscription. A rejected subscription is retried every 10 minutes and after
each reconnect.

The broker publishes the `LastWill` of a provider when the connection is
lost, and the connection publishes it when the runtime stops. Use it, for
example, to mark the other values as unavailable. A connection allows one
`LastWill`, which requires a `ClientID`; its provider's value is sent after
the other values. Use a different `ClientID` for each connection to a
broker.

## CSV

`CSV.ValueWriter` takes `Type: type` and `Filename: string`. It appends
rows from a default `value Type` input, recording the timestamp, old value,
and new value. An empty field marks an unavailable value at startup or
shutdown. It writes on changes and repeats a current value hourly. `Type`
must support CSV conversion, such as `boolean`, an integer type, or an
`Option` of one. The file is opened for append when the service starts.
The timestamp field is empty while wall time is unknown.

## TwoN

TwoN nodes communicate with a 2N device. Create `TwoN.Connection`, then
connect its default `fixed TwoN.Device` output to each client's named
`Device` input.

| Node type | Parameters | Ports and behavior |
| --- | --- | --- |
| `Connection` | `Host: string`, `Port: uint16 = 443`, `TLSA: List[string]`, `UsernamePasswordFile: string` | Out: `fixed TwoN.Device`; named out `Connected: value boolean`. Connects to the device. The credential file must have an absolute path and contain a username and password on separate lines. |
| `KeyEvent` | `Key: string` | Named in: `Device`; out: `event boolean`. True on press, false on release. |
| `KeyState` | `Key: string`, `Timeout: Std.Duration = "30s"` | Named in: `Device`; out: `value Option[boolean]`. Tracks a key, assuming release after the timeout or when monotonic time is lost. False after each connect until a press, so it can be wrong if the key was already pressed. |
| `IOState` | `Port: string` | Named in: `Device`; out: `value Option[boolean]`. Tracks an input or output port. |
| `SwitchState` | `SwitchNumber: int32` | Named in: `Device`; out: `value Option[boolean]`. Tracks whether a switch is active. |
| `CodeEnteredEvent` | `Code: string` | Named in: `Device`; out: `event unit`. Emits when the specified code is entered. |
| `OutputControl` | `Port: string` | Named in: `Device`; in: `event boolean`. Sets an output on or off. |

`Connection` retries connecting until it succeeds, also after losing the
connection. `Connected` is true while connected and false for at least one
step after each disconnect. A lost connection is noticed when a request
fails or times out; an event from the device that cannot be handled also
ends the connection. Events are lost while disconnected, and `OutputControl`
drops values while disconnected or when sending fails.

`KeyEvent` and `CodeEnteredEvent` deliver one event per step and keep up to
10 pending events; if more arrive, the oldest one is dropped.

`KeyState`, `IOState`, and `SwitchState` are `None` while disconnected and
for at least one step after each disconnect. After each connect and about
every hour, `IOState` and `SwitchState` read their state, and a state that
changed without an event is logged as a warning; for a port or switch the
device does not have, they stay `None` and a warning is logged.

`Connection` accepts control commands. Command 0 reads all states again
after the current pull, within about a minute, and restarts the hourly
interval. Command 1 reads or sets how long a pull waits for events (60 s by
default), from the next pull; the setting is kept across reconnects but not
across a restart of the runtime.

| Node type | Command | Argument | Result |
| --- | --- | --- | --- |
| `Connection` | 0 | `{}` | `{}`; while disconnected, `CommandFailed` with reason 1 |
| `Connection` | 1 | `{}` or `{1: seconds}`, from 1 to 60 | `{1: seconds}` |

`TLSA` provides certificate association records. TwoN use needs the bundled
TwoN dependencies, device credentials, and network access to the device.

To read credentials beside the file containing the configuration, supply:

```wirely
UsernamePasswordFile:
  Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt"),
```

Credentials are read during compilation; execution uses the prepared
username and password.

## GPIO

`GPIO.Output` sets a GPIO line from a default `value boolean` input.
Parameters are `ChipDeviceFile: string`, `Name: string`,
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
`None`, commands are ignored. Its parameters are `OutputType: type = unit`,
`OutputValue: OutputType = ()`, and `MinimumDelay: Std.Duration = "PT1S"`.
Set `OutputValue` when `OutputType` is not `unit`. The minimum delay limits
successive output events. During that delay, the latest command remains pending
until feedback matches it or becomes unavailable. Lost monotonic time discards
the pending command; a fresh command can emit immediately.

## Process

`Process.LaunchOnEvent` launches a shell command when an input event arrives.
Events received while a launch is pending may be combined into one launch.
It takes `ShellCommand: string` and `Type: type = unit`, with a default
`event Type` input. The event payload is ignored. The command is passed to
`sh -c` in the output stage and runs with the service process's permissions.
If the command cannot be started, a warning is logged and the event is
dropped. A command that is still running when the runtime stops is killed.

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

```wirely
node mode: Std.Constant {
  Type: ThermostatSystemModeEnum,
  Value: ThermostatSystemModeEnum.Heat,
}
node modes: Std.DebugEventSource { Type: ThermostatSystemModeEnum }
node printer: Std.DebugEventPrinter { Type: ThermostatSystemModeEnum }
connect modes -> printer;
```
