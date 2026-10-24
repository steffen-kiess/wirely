# Native Python implementations

Wirely declares node interfaces in [`wirely/lib/`](../wirely/lib/) and
implements native behavior in [`wirely/native/`](../wirely/native/).
See the [language guide](language.md) for `.wrl` syntax and the
[value type reference](value-types.md) for literals and type rules.

## Implementations and procedures

Implementation modules use the snake-case namespace name: `Std` uses
`wirely.native.std`, and `TwoN` uses `wirely.native.two_n`. A
native initializer or procedure's [native name](language.md#declarations-and-names)
selects a flat class name: `KNX.Connection:create` selects
`wirely.native.knx.ConnectionCreate`, and `Std.DebugEventPrinter:run`
selects `wirely.native.std.DebugEventPrinterRun`. Snake-case member names
become CamelCase. Each class subclasses its generated contract.
Procedures receive native state through fixed reads, as shown
[below](#native-objects-resources-and-tasks).

Run `tools/generate-native-types` after changing native declarations.
Generated bindings import implementations lazily, and mypy checks their
names, signatures, and constructors. Each engine creates its own native
states and procedure objects.

For example, `Std.DebugEventPrinter` declares:

```wirely
namespace Std {
  node type DebugEventPrinter {
    parameter Type: type;

    default input: event Type;

    native procedure run stage output
      reads default input as "input", Type;
  }
}
```

Its Python implementation is:

```python
from typing import Any

from wirely import DynamicValue, Some, ValueType
from wirely.generated import std as contracts
from wirely.runtime.native_api import PreparationServices, ProcedureServices


class DebugEventPrinterRun(contracts.DebugEventPrinterRun):
    def prepare(self, *, services: PreparationServices,
                type: ValueType[Any]) -> None:
        pass

    def run(self, *, services: ProcedureServices,
            input: Some[DynamicValue] | None, type: ValueType[Any]
            ) -> contracts.DebugEventPrinterRunResult:
        if input is not None:
            print(f'[{services.name}]: {input.value.to_text()}')
        return contracts.DebugEventPrinterRunResult()
```

## Contracts

Each native initializer, procedure, and function implements a contract
generated from its declaration. The contracts of a namespace are in a
module of `wirely.generated` named in snake case, such as
`wirely.generated.two_n` for `TwoN`:

| Declaration | Contract | Result class |
| --- | --- | --- |
| Initializer `KNX.Connection:create` | `ConnectionCreate` | `ConnectionCreateResult` |
| Procedure `MQTT.EventSender:send_value` | `EventSenderSendValue` | `EventSenderSendValueResult` |

Contract methods take keyword arguments named after the keys of `reads`
and `writes`, in snake case: `ChipDeviceFile` becomes
`chip_device_file`, and `"implicit input"` becomes `implicit_input`. The
key `""` becomes `_`, and Python keywords, `self`, and `services` get a
`_` suffix. Result classes are frozen dataclasses whose fields, named the
same way, are all required keyword arguments.

Initializer and procedure classes derive from their generated contract.
Mypy checks method signatures and the generated factories’ calls to
constructors. The runtime checks the compiled
program's interfaces and values against the installed contracts.

## Extensions

Pass runtime extensions to both compilation and execution, and constant
function extensions to compilation:

```python
from acme.generated import extension
from acme.compiler.generated import extension as compiler_extension
from wirely.compiler import compile_files
from wirely.compiler.program import compile_nocatch
from wirely.runtime.engine import Engine


graph = compile_files(['library.wrl', 'program.wrl'], extensions=[extension],
                      compiler_extensions=[compiler_extension])
assert graph is not None
engine = Engine(compile_nocatch(graph), extensions=[extension])
```

`RuntimeNativeExtension` in `wirely.runtime.extensions` contains `states`,
`procedures`, `functions`, `resources`, `enums`, `structs`, and
`opaque_types`, keyed by native name. `NativeInitializer`,
`NativeProcedure`, and `NativeFunction` entries bundle a signature with a
lazy implementation loader. Resource entries load Python classes; enum,
struct, and opaque struct entries use `NativeEnum`, `NativeStruct`, and
`NativeOpaque` from `wirely.types.value_types`.
`CompilerNativeExtension` in `wirely.compiler.extensions` contains only
`functions`: `NativeConstantFunction` entries with signatures and loaders.
All enum and struct classes belong to the runtime extension, including
parameter-only types. Extension mappings are copied and read-only.

Built-in extensions are included automatically. Set
`include_builtin_extensions=False` to supply all bindings yourself;
this is independent of `add_std`, which includes Wirely source declarations.
Standard library literals such as `"PT1S"` then need the built-in
`opaque_types`: pass
`RuntimeNativeExtension(opaque_types=extension.opaque_types)` with the
`extension` of `wirely.generated`.
Duplicate native names within a category are rejected. For replacements,
pass `override_extensions` and, when compiling,
`compiler_override_extensions`. Each override replaces a whole entry,
including its signature; duplicate override names are also rejected.
Bindings are local to each compilation or engine.

Generate an external package from its declarations:

```sh
tools/generate-native-types library.wrl --package acme --output-root src
```

Use `acme.native`, `acme.functions`, and
`acme.compiler.function_implementations` for implementations, following the
same naming conventions as Wirely's built-ins. The command writes
`acme.generated` and `acme.compiler.generated`; standard-library
dependencies keep their built-in Python classes. Add `--check` to check
for out-of-date generated files.

The generated public API consists of each package's `extension`, runtime
initializer/procedure contracts and result classes, and native enum/struct
classes. Function typing contracts, adapters, loaders, and tables are
private. Tests use the same extension arguments to provide their own
implementations and overrides.

## Inputs, outputs, and state

For runtime procedures, the engine constructs each class once per node
instance without arguments and calls `prepare()` after loading and before
the first step. `prepare()` receives the fixed reads and the
[compile-time values](#compile-time-values-and-prepared-data); store what
`run()` needs. Contracts with compile-time reads declare `prepare()`
abstract. Raise from `prepare()` to reject unsupported configuration; the
program then does not start. Keep `prepare()` free of side effects.

Each invocation calls `run()` synchronously with the current values of
all reads, including fixed and compile-time values, and uses the outputs
from the returned result. Return every declared output; to preserve state,
return its previous value again. Use native node tasks for asynchronous I/O.
Native procedures currently accept only single value/event reads.
Use a Wirely procedure to aggregate multi value inputs.

A native `stage load` procedure implements `load()` instead of `prepare()`
and `run()`. The engine calls it once with all compile-time and fixed reads
and stores its returned fixed outputs. Its `PreparationServices` provides
`name` and `native_types`, without triggers or wakeups. Load procedures are
pure; use an initializer to own native state or asynchronous tasks.

`services` has read-only `name`, which uses the node naming described
under [native objects](#native-objects-resources-and-tasks), and the
operations described under [scheduling and time](#scheduling-and-time).

Input and output keys are the port names or `as "key"` strings of the
`.wrl` `reads` and `writes` clauses. See
[state and execution](language.md#state-and-execution) for ports and state
access.

Event ports add an outer option to their payload. A present optional unit
is `wirely.Some(wirely.Unit())`, distinct from absence, `None`:

| Port | No event | Example occurrence |
| --- | --- | --- |
| `event unit` | `None` | `Some(Unit())` |
| `event boolean` | `None` | `Some(True)` |
| `event Option[boolean]` | `None` | `Some(None)` for an absent payload; `Some(Some(True))` for a present payload |

These encodings apply to inputs and outputs. Value ports carry values
directly. After emitting an event, schedule another invocation with
`services.trigger_self()` to return `None`.

## Compile-time values and prepared data

Native procedures and initializers read compile-time values in their
`reads` clause, next to their ports:

```wirely
native procedure run
  reads input, Scale, Offset as offset, Example.Parse(Address) as "address"
  writes output;

native initializer create reads Broker, Topic as topic;
```

A bare name reads a parameter or constant in the node's scope. Other
compile-time expressions, including qualified references, require an alias;
aliases may be identifiers or strings. Keys must be unique within each
`reads` clause. For expressions that need type context, use a typed
constant or a typed local in an expression block. Clauses may be empty and
accept trailing commas.

An initializer receives its compile-time values as keyword arguments of
`initialize()`, a load procedure in `load()`, and a runtime procedure in
`prepare()` and `run()`. Each expression is evaluated once per consumer per instance during
compilation; constants are shared within the instance. Compile-time values
never change, so they do not schedule procedures.

Read an optional parameter with `optional(Foo) as foo`: `foo` is
`None` when omitted and `Some(value)` when supplied. For an optional
`Option[T]`, a supplied `None` becomes `Some(None)`. Ordinary bindings and
resolved defaults are ordinary values.

## Python representations

| Wirely type | Python representation |
| --- | --- |
| `unit` | `wirely.Unit` |
| `boolean` | `bool` |
| Integer types | `wirely.Int8` through `wirely.Int128`, `wirely.UInt8` through `wirely.UInt128` |
| `string` | `str` |
| `Std.Duration` | `wirely.Duration`, with `ticks` |
| `Schedule.TimeOfDay` | `wirely.TimeOfDay`, with `ticks` since midnight (up to 24:00) |
| `Timer.MonotonicTime` | `wirely.MonotonicTime`, with `generation` and `ticks` |
| Native enums | Generated Python enum |
| Native structs | Generated `NamedTuple` |
| Opaque structs | The class implementing it |
| `type` | `wirely.ValueType` |
| `Option[T]` | `None` or `wirely.Some(value)` |
| `List[T]` | `tuple` of inner values |
| A runtime value of a `type` parameter `T` | `wirely.DynamicValue` |
| Resources | The object the provider returned |

A generic `fixed T` port carries a resource object when `T` is a resource
type, and a `DynamicValue` when `T` is a runtime value type.

Integer values are immutable and check their type's range when
constructed; booleans are not integers. Read the Python integer with
`value`. They compare and hash only with values of the same class, and
have no arithmetic operators: compute with `value` and construct the
result.

Time `ticks` count units of 2^-64 s, given by
`wirely.types.time.TICKS_PER_SECOND`. `Duration.from_seconds()` and
`total_seconds` convert from and to seconds. `Unit` is immutable and
fieldless; all instances compare equal.

Ports, compile-time values, and function arguments and results of enum or struct
types require `native` enums and structs. Code written for them relies on
their numbers or fields, which are their contract with native code;
member and field names are not part of it.
[`wirely/generated/enums.py`](../wirely/generated/enums.py) has an
`IntEnum` for each native enum, and
[`wirely/generated/structs.py`](../wirely/generated/structs.py) a `NamedTuple`
for each native struct, both generated by `tools/generate-native-types`
and named after the native name, such as `KNXProtocol` for
`KNX.Protocol`. An open enum may carry numbers the runtime does not know,
for example from a newer library; they are unnamed members whose `name` is
`None`, so report such values as unsupported.

An opaque struct is implemented by a class named after the last part of
its native name, in the module of `wirely.native` for its namespace, such
as `wirely.native.std.Duration` for `Std.Duration`. The generated
extension loads it from there; external packages use their own `native`
package. Its values have the struct's fields in the representation above:

- `from_fields(*fields)` returns the value with these fields, or raises
  `ValueError` if the fields are not a valid value. The value with all
  fields zero must be valid; it is the default.
- `to_fields()` returns the fields of a value.
- The optional class method `parse(text)` returns the value of a string
  literal or of the text format, and the optional `to_text()` formats a
  value.

Before running a program, the runtime checks the native interfaces it uses:

- Each native enum must have the same kind, underlying type, and, for a
  closed enum, member count.
- Each native struct must have the same field types in the same order.
- Each opaque struct must have the same field types in the same order, and
  its implementation must accept the program's constant values.
- Each native initializer, procedure, and runtime function must have the
  stage, keys of reads and writes, resource types, and value types its
  selected extension entry declares. Each type variable is replaced by the
  type its compile-time read passes. Each description passes every type
  parameter its ports use through its own compile-time reads. An event port
  and a value port of `Option[T]` are not told apart.

Run `tools/generate-native-types` after changing any native declaration.

## Value type descriptors and dynamic values

Ports of a type given by a `type` parameter, such as `value Type`,
carry `wirely.DynamicValue`s. A dynamic value is immutable and has a
`type`, a `wirely.ValueType`. Dynamic values are equal when their types
and values are equal.

- `to_text()` formats the value in the [text format](value-types.md#text-format).
- `json_encode(format=None)` returns a JSON-compatible Python value,
  without serializing JSON text.
- `to_csv_value()` returns `(text, needs_quotes)`.
- `extract(value_type, native_types=context)` returns the Python representation
  if the value has type `value_type`.
- `to_native(native_types=context)` returns the Python representation
  of any type that has one.

`wirely.ValueType` is immutable and supports equality, hashing, and `str()`
with Wirely type names. Built-in descriptors are in `wirely.types.value_types`,
using Python names: `unit`, `boolean`, `int8` through `int128`, `uint8` through
`uint128`, `string`, `duration`, `time_of_day`, `monotonic_time`,
and `value_type`. `option(inner)` and `list_of(inner)` describe options and
lists. These descriptors are typed, so extracting a `uint8` returns
a `wirely.UInt8`.

- `from_native(value, native_types=context)` returns the dynamic value
  of a Python representation.
- `parse(text)` parses text.
- `json_decode(value, format=None)` converts a JSON-compatible Python value.
- `supports_json` and `supports_csv` indicate support for JSON and CSV
  conversion.

Native conversions require a `NativeTypes` context: use
`services.native_types` inside native implementations or
`engine.native_types` when calling from Python. Contexts select native enum
and struct classes. Values and descriptors carry no context; text and JSON
conversion, equality, and hashing need none.

Invalid values and unsupported conversions raise `ValueError`. Composite
descriptors are available through compile-time reads. Each compiled program
owns its enum and struct types; use values belonging to that program.

To check that a procedure's type parameter matches its native state's,
call `wirely.native.check_state_type(services.name, type, state_type)` in
`prepare()`.

## Native objects, resources, and tasks

Each native initializer's class subclasses its generated contract, which
derives from `NativeState` in
`wirely.runtime.native_api`. The engine constructs it without arguments
and then calls `initialize()`, which receives the initializer's fixed and
compile-time reads and returns its fixed outputs. Initialize fields there.
Each initializer owns its state object. For a singleton node type, each
initializer shares its object across uses of the node type.

The native initializer declares its reads and fixed outputs:

```wirely
native initializer create reads Broker writes default output as "topic";
```

Reads and outputs use the same key rules as procedure ports. Providers are
constructed before their consumers. Each output must be an instance of the
Python type exported for a resource output's type; plain fixed outputs use
the usual native value representation. See [`mqtt.py`](../wirely/native/mqtt.py) for
provider and client examples.

Export the Python class under the resource type's last name component.
Use an ordinary type alias when the names differ, with a short comment:

```python
Bus = ConnectionCreate  # Used as a resource type.
```

When the class itself has the resource's name, put the comment above its
class declaration. Resource objects may differ from the initializer
object, and an initializer may return several resources.

To give procedures access to the native object, write it as a
[fixed value](language.md#fixed-ports) and read it:

```wirely
resource type ReceiverState;

node type Receiver {
  default output: value Option[boolean];
  binding state: fixed ReceiverState;
  native initializer create writes state as "state";
  native procedure get_value stage input
    reads state as "state"
    writes default output as "output";
}
```

```python


class ReceiverCreate(contracts.ReceiverCreate):
    def initialize(self, *, services: StateServices[Literal['state']]
                   ) -> contracts.ReceiverCreateResult:
        self.value: bool | None = None
        return contracts.ReceiverCreateResult(state=self)

    def receive(self, value: bool) -> None:
        self.value = value
        self.services.trigger('state')


class ReceiverGetValue(contracts.ReceiverGetValue):
    def run(self, *, services: ProcedureServices, state: 'ReceiverCreate'
            ) -> contracts.ReceiverGetValueResult:
        value = None if state.value is None else Some(state.value)
        return contracts.ReceiverGetValueResult(output=value)


ReceiverState = ReceiverCreate  # Used as a resource type.
```

`self.services` is the `StateServices` object that `initialize()` also
receives. It lasts for the node's lifetime and has read-only `name`, the
dotted instance name or, for a singleton, the qualified type name, and
`is_shutting_down`, whether cleanup follows completed startup, and
`shutdown_time`, the wall time when that cleanup started. `shutdown_time`
is `None` outside shutdown or when wall time is unknown; use
`is_shutting_down` to distinguish these cases. Changes to Python fields
do not automatically schedule procedures. Call
`services.trigger(output)` to schedule each procedure that reads the
resource output with this Python name, including consumers reached through
a load selector returning that handle. Repeated triggers
before execution schedule one invocation.

Startup calls `start_tasks(task_group, async_exit_stack)`, then awaits
`start_tasks_async(task_group, async_exit_stack)` on each native object.
Use these hooks for task creation and resource acquisition. Use the
supplied `AsyncExitStack` for resource contexts and cleanup callbacks,
which run in reverse registration order.

Use `self.start_task(task_group, async_exit_stack, coroutine)` for a task
that runs until cancellation during cleanup. Tasks can update Python fields
and trigger procedures, or send data handed off by output procedures.

## Scheduling and time

Procedures run in dependency order within input, default calculation, and
output stages. Input procedures can read only fixed and compile-time
values. After the first step, procedures run when dependencies or state
change, when explicitly triggered, or when due for a wakeup. A procedure
without dependencies does not automatically run every step.

`services.start_time_for_stage(stage)` returns a read-only
`TimeSnapshot` from `wirely.runtime.time_snapshot`, captured at the start
of `RunStage.INPUT`, `DEFAULT`, or `OUTPUT` (`RunStage` is defined in
`wirely.program`). Its attributes are `monotonic` (`MonotonicTime`),
`utc` (UTC-aware `datetime`), and `local` (local-aware `datetime`). Both wall
timestamps are `None` while wall time is unknown; monotonic time remains
available. Snapshots are available for stages that have started.

Call `services.trigger_self()` to schedule the running procedure again in
the next step.

Call `services.set_wakeup_time(deadline)` at most once per invocation with
a `MonotonicTime` deadline to request another invocation. Passing `None` or
leaving it unset cancels any previous deadline. An incomparable clock
generation also causes a rerun; handle that case as
`Timer.MonotonicTimeReached` does in
[`timer.py`](../wirely/native/timer.py).

Call `services.set_wall_wakeup_time(start, end, utc_offset=offset)` to rerun
when wall time leaves the aware-datetime window `[start, end)`, becomes
unknown, or its local UTC offset differs from `offset`. Pass the
`timedelta` offset of the snapshot used to compute your result, or `None`
to ignore offset changes for UTC-based results. Call it without arguments
to wait for known wall time. Renew the request on each invocation to remain
subscribed. See the method's docstring
in [`native_api.py`](../wirely/runtime/native_api.py) for the full contract.
Detecting UTC-offset changes, and wall-clock changes or resume without
native timers, can take up to one second, plus scheduling delay. With native
timers, offset polling runs only while a request monitors offsets.

## Native functions

### Runtime functions

A `native function` runs in procedure bodies and in compile-time
expressions, so its implementation is shared by the compiler and the
runtime and lives in [`wirely/functions/`](../wirely/functions/) rather
than with the runtime-only native implementations. The module name is the
snake-case namespace name, and the function name is the snake-case last
component of its native name:

```python
from wirely import Int16


def clamp(value: Int16, low: Int16, high: Int16) -> Int16:
    return min(max(value, low), high)
```

Implementations receive the arguments positionally in declaration order
and return one value of the declared return type. They must be synchronous
and pure: the result depends only on the arguments, and calling the
function has no side effects. They must return a value for every argument;
return an `Option` when a result may be absent. An exception is an
internal error: during compilation it stops compilation, and at runtime it
propagates out of `Engine.step()`. An `Engine` resolves every function its
program calls when it is constructed.

### Constant functions

A `native constant function` runs only in compile-time expressions. It can
take and return types without a runtime representation, such as `string`
and `type`. Implementations live in the snake-case namespace module
under [`wirely/compiler/function_implementations/`](../wirely/compiler/function_implementations/),
with the snake-case function name:

```python
from typing import Any

from wirely import ValueType
from wirely.compiler.native_functions import FunctionServices
from wirely.knx.value_types import get_value_type


def value_type_name(type: ValueType[Any], *, services: FunctionServices) -> str:
    try:
        return get_value_type(type).name
    except ValueError as error:
        raise services.error(str(error), argument=0) from error
```

Implementations receive the arguments positionally in declaration order,
in the [same representations](#python-representations) as native code,
and return one value of the declared return type. They must be
synchronous. The compiler imports a module when one of its functions is
called. A call can be evaluated more than once, for example once per
instance.

`services` has read-only `function_name` (the native name), `source_path`
(the path of the file containing the call, or `None`), and `services`, a
read-only mapping of Python objects supplied through the optional
`services` argument to `compile_files()` or `compile_strs()`. The compiler
copies the mapping, preserving its objects by reference. Service objects
may manage mutable state.

Use `raise services.error(message)` for a call-site diagnostic, or
supply `argument=0` to point at the first argument (indices are zero-based).
`ValueError`, `ArithmeticError`, and `OSError` also produce call-site
compilation errors.
`services.warn(message)` reports a `NativeFunction` warning at the call and
returns; it accepts `argument=` as well. Equal warnings at one call are
reported once.
