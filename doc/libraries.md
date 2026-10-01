# Library reference

The compiler loads all ten libraries below by default. Instantiate a node
as `node name : Library.Type(Parameter = value);`. Names and parameter
capitalization matter. Public library namespaces, node and resource types,
parameters, and named connectors use PascalCase, preserving acronyms such
as `KNX` and `URI`. In the signatures below, `in` and `out` mean implicit
connectors; named connectors are written explicitly. `value T` carries a
current value, and `event T` carries an occurrence with a payload. A dash
means no connector of that kind.

See the [language guide](language.md) for syntax and connection rules. The
declarations in [`wirely/lib/`](../wirely/lib/) are the source for exact
signatures; behavior comes from [`wirely/native/`](../wirely/native/).

## Std

General data flow, optional values, edge detection, and debug nodes.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Constant` | `Type: ValueType`, `Value: Type` | Out: `value Type`. Produces the supplied value. |
| `And` | None | In: `list value boolean`; out: `value boolean`. True when every connected input is true, including an empty list. |
| `Delay` | `Type: ValueType`, `InitialValue: Type` | In/out: `value Type`. Outputs the previous step's input, initially `InitialValue`. |
| `TwoStageValueForward` | `Type: ValueType` | In/out: `value Type`. Forwards through two native procedures. |
| `DebugPrinter` | `Type: ValueType` | In: `value Type`. Prints changes in the output stage. |
| `DebugEventPrinter` | `Type: ValueType` | In: `event Type`. Prints events in the output stage. |
| `DebugValueSource` | `Type: ValueType` | Out: `value Type`. Debug-controlled value source. |
| `DebugEventSource` | `Type: ValueType` | Out: `event Type`. Debug-controlled event source. |
| `OptionNone` | `InnerType: ValueType` | Out: `value Option[InnerType]`. Produces `None`. |
| `OptionSome` | `InnerType: ValueType` | In: `value InnerType`; out: `value Option[InnerType]`. Wraps the input. |
| `OptionUnwrapOrDefault` | `InnerType: ValueType`, `Default: InnerType` | In: `value Option[InnerType]`; out: `value InnerType`. Uses `Default` for `None`. |
| `OptionIsSome` | `InnerType: ValueType` | In: `value Option[InnerType]`; out: `value boolean`. True when a value is present. |
| `OptionCreate` | `InnerType: ValueType` | Named in: `HasValue: value boolean`, `Value: value InnerType`; out: `value Option[InnerType]`. Wraps `Value` when `HasValue` is true; otherwise produces `None`. |
| `DetectEdge` | `EdgeType: String` | In: `value boolean`; out: `event boolean`. Only `"Falling"` works; `"Rising"` and `"Both"` raise an error when run. |

The debug sources are intended for testing and interactive control. Their
input is supplied through the engine's debug interface, not a `.wrl`
connection. `TwoStageValueForward` is primarily a demonstration of procedure
dependencies.

## Time

Time-based values and triggers. `TimeOfDay` parameters use strings such as
`"08:00"`; `Duration` parameters use strings such as `"PT1S"`.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `MonotonicTimeReached` | None | In: `value MonotonicTime`; out: `value Option[boolean]`. True once the time is reached, false before it, or `None` if the times cannot be compared. |
| `DelayChange` | `Type: ValueType`, `Duration: Duration` | In/out: `value Type`. Changes the output only after the input has remained stable for `Duration`. |
| `TimeOfDayMinute` | None | Out: `value TimeOfDay`. Shared singleton that updates at minute boundaries; normally used through other Time nodes. |
| `IsBetween` | `Start: TimeOfDay`, `End: TimeOfDay` | Out: `value boolean`. Tests the interval from `Start` (inclusive) to `End` (exclusive), including intervals that cross midnight. |
| `TriggerAt` | `Time: TimeOfDay`, `OutputType: ValueType = unit`, optional `OutputValue: OutputType` | Out: `event OutputType`. Emits around the requested time, with logic to avoid duplicate daily events during clock changes. Supply `OutputValue` when `OutputType` is not `unit`. |

## KNX

KNX nodes use a `KNX.Connection` node and a named
`Bus: resource KNX.Bus` input.
`GroupAddress` is a KNX group address string such as `"0/4/5"`.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Connection` | Optional `Host: String`, `Protocol: String`, `Port: uint16`, `UnixPath: String`, `SSHTarget: String`, `IndividualAddress: String` | Out: `resource KNX.Bus`. Opens a KNX connection. |
| `ValueReceiver` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; out: `value Option[Type]`. Reads the latest value, with `None` before one is available. |
| `ValueOptionProvider` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; in: `value Option[Type]`. Exposes an optional value on the bus. |
| `ValueProvider` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; in: `value Type`. Wraps the value with `Std.OptionSome` and uses `ValueOptionProvider`. |
| `EventReceiver` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; out: `event Type`. Emits received KNX events. |
| `EventEmitter` | `Type: ValueType`, `GroupAddress: String` | Named in: `Bus`; in: `event Type`. Sends KNX events. |

The current native value receiver and provider support `boolean` and
`uint16`. Other declared `Type` values may fail when those nodes are
instantiated. Event support depends on the KNX value type handled by the
native implementation. KNX use requires the `xknx` package and a reachable
KNX service.

Choose one connection mode:

- `Host` with `Protocol = "TCP"` or `"UDP"` opens a direct tunnel. `Port`
  defaults to `3671`.
- `Protocol = "Unix"` opens a local Unix socket. `UnixPath` defaults to
  `/run/knxnet`; supplying `UnixPath` implies `Protocol = "Unix"`.
- `SSHTarget` opens a Unix socket on a remote host over SSH. It requires
  `Protocol = "Unix"` unless `UnixPath` is also supplied.
- `Protocol = "Auto"` uses automatic discovery.

`IndividualAddress` is supported for TCP and Unix socket connections. An
empty connection, an SSH target without a protocol or Unix path, and
parameters from conflicting modes are errors. For example,
`KNX.Connection(UnixPath = "/run/knxnet")` uses a local socket.

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
`Topic` is a topic string. `Format` and `Template`, where offered, are
optional strings used by the native JSON encoder. A template embeds the
encoded value at one `%s` placeholder; `%%` represents a literal percent
sign. The `URI` accepts `mqtt://host`, `mqtts://host`, or `mqtt+unix:///path`.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Connection` | `URI: String` | Out: `resource MQTT.Broker`. Connects to a broker. |
| `ValueOptionProvider` | `Type: ValueType`, `Topic: String`, optional `Format: String`, `Template: String` | Named in: `Broker`; in: `value Option[Type]`. Publishes JSON; `None` publishes an empty payload. |
| `ValueProvider` | Same as `ValueOptionProvider` | Named in: `Broker`; in: `value Type`. Wraps the value and uses `ValueOptionProvider`. |
| `EventEmitter` | `Type: ValueType`, `Topic: String`, optional `Format: String`, `Template: String` | Named in: `Broker`; in: `event Type`. Publishes each event as JSON. |
| `EventReceiver` | `Type: ValueType`, `Topic: String`, optional `Format: String` | Named in: `Broker`; out: `event Type`. Emits decoded messages received on the topic. |

Only types supported by the native JSON encoder/decoder can be used. MQTT
requires the `aiomqtt` package and a reachable broker.

## CSV

`CSV.ValueWriter` takes `Type: ValueType` and `Filename: String`. It appends
rows from an implicit `value Type` input, recording the timestamp, old value,
and new value. An empty field marks an unavailable value at startup or
shutdown. It writes on changes and repeats a current value hourly. `Type`
must support CSV conversion, such as `boolean`, an integer type, or an
`Option` of one. The file is opened for append when the service starts.

## TwoN

TwoN nodes communicate with a 2N device. Create `TwoN.Connection`, then
connect its implicit `resource TwoN.Device` output to each client's named
`Device` input.

| Node type | Parameters | Connectors and behavior |
| --- | --- | --- |
| `Connection` | `Host: String`, `Port: uint16 = 443`, `TLSA: List[String]`, `UsernamePasswordFile: String` | Out: `resource TwoN.Device`. Connects to the device. The credential file contains a username and password on separate lines; a relative path is resolved beside the `.wrl` file. |
| `KeyEvent` | `Key: String` | Named in: `Device`; out: `event boolean`. True on press, false on release. |
| `KeyState` | `Key: String`, `Timeout: Duration = "30s"` | Named in: `Device`; out: `value boolean`. Tracks a key, assuming release after the timeout. Startup state can be wrong if the key was already pressed. |
| `IOState` | `Port: String` | Named in: `Device`; out: `value boolean`. Tracks an input or output port. |
| `SwitchState` | `SwitchNumber: int32` | Named in: `Device`; out: `value boolean`. Tracks whether a switch is active. |
| `CodeEnteredEvent` | `Code: String` | Named in: `Device`; out: `event unit`. Emits when the specified code is entered. |
| `OutputControl` | `Port: String` | Named in: `Device`; in: `event boolean`. Sets an output on or off. |

`TLSA` provides certificate association records. TwoN use needs the bundled
TwoN dependencies, device credentials, and network access to the device.

## GPIO

`GPIO.Output` sets a GPIO line from an implicit `value boolean` input.
Parameters are `ChipDeviceFile: String`, `Name: String`,
`ActiveState: String = "High"`, `Drive: String = "PushPull"`, and
`Bias: String = "AsIs"`. `ActiveState` accepts `"High"` or `"Low"`;
`Drive` accepts `"PushPull"`, `"OpenDrain"`, or `"OpenSource"`; and
`Bias` accepts `"PullDown"`, `"PullUp"`, `"Disable"`, or `"AsIs"`.
GPIO use requires the `gpio` extra (or the official `gpiod` 2.x Python
bindings) and access to the chip device.

## Memory

`Memory.ToggleFlipFlopTrigger` can switch an external impulse relay
(latching relay) on or off. Each pulse toggles the relay, so the node compares
the requested state with feedback and emits a pulse only when the state needs
to change. It takes an implicit `event boolean` command input (`true` for on,
`false` for off) and a named `Feedback: value Option[boolean]` input. Its
implicit `event OutputType` output provides the trigger pulse. If feedback is
`None`, commands are ignored. Its parameters are `OutputType: ValueType = unit`,
optional `OutputValue: OutputType`, and `MinimumDelay: Duration = "PT1S"`.
Set `OutputValue` when `OutputType` is not `unit`. The minimum delay limits
successive output events.

## ExtProg

`ExtProg.LaunchOnEvent` launches a shell command when an input event arrives.
Events received while a launch is pending may be combined into one launch.
It takes `ShellCommand: String` and `Type: ValueType = unit`, with an implicit
`event Type` input. The event payload is ignored. The command is passed to
`sh -c` in the output stage and runs with the service process's permissions.
