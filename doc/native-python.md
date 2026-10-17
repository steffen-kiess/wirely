# Native Python implementations

Wirely declares node interfaces in [`wirely/lib/`](../wirely/lib/) and
implements native behavior in [`wirely/native/`](../wirely/native/).
See the [language guide](language.md) for `.wrl` syntax and the
[value type reference](value-types.md) for literals and type rules.

## Registration and procedures

`native_node_pkgs` in
[`wirely/native/__init__.py`](../wirely/native/__init__.py) maps namespaces
to modules: `Std` maps to `std`, and `KNX` to `knx`. A new namespace
needs a map entry. Each module supplies a `native_node_types` dictionary;
`@native_node_type` registers a class under its Python name. The
[native name](language.md#declarations-and-names) of a node type with
native state selects its class: the namespace part selects the map entry,
and the last component the class.

A native procedure's
[native name](language.md#state-and-execution), such as
`Std.DebugEventPrinter:run`, selects a class: the part before `:` names a
registered class, and the part after it the name under which
`@native_procedure` registers a class nested in that class or one of its
bases. Procedures receive a native state only through a resource
dependency, as shown [below](#native-objects-resources-and-tasks).

Native registries must be available when constructing an `Engine` from
`wirely.runtime.engine`. It resolves classes using the native type names
stored in `Program`, such as `"ABB.JRA.Blind"`. Each engine creates its own
native states and procedure objects.

For example, `Std.DebugEventPrinter` declares:

```wirely
namespace Std {
  node type DebugEventPrinter {
    parameter Type: ValueType;

    default input: event Type;

    native procedure run stage output
      depends on default input as "input"
      uses Type;
  }
}
```

Its Python implementation is:

```python
from typing import Any

from wirely import DynamicValue, Some, ValueType
from wirely.contracts import std as contracts
from wirely.native import native_node_type, native_procedure
from wirely.runtime.native_api import PreparationServices, ProcedureServices

native_node_types: dict[str, type] = {}


@native_node_type(native_node_types, has_native_state=False)
class DebugEventPrinter:
    @native_procedure('run')
    class Run(contracts.DebugEventPrinterRun):
        def prepare(self, *, services: PreparationServices,
                    type: ValueType[Any]) -> None:
            pass

        def run(self, *, services: ProcedureServices,
                input: Some[DynamicValue] | None
                ) -> contracts.DebugEventPrinterRunResult:
            if input is not None:
                print(f'[{services.name}]: {input.value.to_text()}')
            return contracts.DebugEventPrinterRunResult()
```

Register the class of a `native node type` with `has_native_state=True`,
and other classes with `has_native_state=False`, as above. Either kind of
class can implement procedures. Node types of either kind can also
declare `.wrl` state.

## Contracts

Each native initializer, procedure, and runtime function implements a
contract generated from its declaration. The contracts of a namespace are
in a module of `wirely.contracts` named in snake case, such as
`wirely.contracts.two_n` for `TwoN`:

| Declaration | Contract | Result class |
| --- | --- | --- |
| Initializer of `KNX.Connection` | `ConnectionInitializer` | `ConnectionInitializerResult` |
| Procedure `MQTT.EventSender:send_value` | `EventSenderSendValue` | `EventSenderSendValueResult` |
| Function `Duration.CheckedAdd` | `CheckedAdd` | None |

Contract methods take keyword arguments named after the keys of `depends
on`, `produces`, and `uses`, in snake case: `ChipDeviceFile` becomes
`chip_device_file`, and `"implicit input"` becomes `implicit_input`. The
key `""` becomes `_`, and Python keywords, `self`, and `services` get a
`_` suffix. Result classes are frozen dataclasses whose fields, named the
same way, are all required keyword arguments.

Implementations derive from their contract, so mypy checks argument and
result types. Before running a program, the runtime also checks that each
implementation derives from its contract, implements every method
synchronously, accepts the contract's keyword arguments, and can be
constructed without arguments.

`tools/generate-native-types` generates the contracts from the standard
library and from [`wirely/native/test_library.wrl`](../wirely/native/test_library.wrl),
which declares the interfaces of the repository's test implementations.
Tests declaring other `TestLibrary` interfaces get contracts generated in
memory from `wirely.test.test_contracts(graph)`.

## Inputs, outputs, and state

The engine constructs each procedure class once per node instance without
arguments and calls `prepare()` after all native objects are constructed
and before the first step. `prepare()` receives the resource dependencies
and the [static values](#static-values-and-prepared-data); store what
`run()` needs. Contracts with static values declare `prepare()` abstract.
Raise from `prepare()` to reject unsupported configuration; the program
then does not start.

Each invocation calls `run()` synchronously with the current values of
all dependencies, including resources, and uses the outputs from the
returned result. Return every declared output; to preserve state, return
its previous value again. Use native node tasks for asynchronous I/O.
Native procedures currently accept only single value/event dependencies.
Use a Wirely procedure to aggregate multi value inputs.

`services` has read-only `name`, which uses the node naming described
under [native objects](#native-objects-resources-and-tasks), and the
operations described under [scheduling and time](#scheduling-and-time).

Input and output keys are the port names or `as "key"` strings of the
`.wrl` `depends on` and `produces` clauses. See
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

## Static values and prepared data

Declare static values for each native procedure or initializer with `uses`:

```wirely
native procedure run
  depends on input
  produces output
  uses Scale, Offset as offset, Example.Parse(Address) as "address";

native initializer depends on Broker uses Topic as topic;
```

A bare name binds a parameter or constant in the node's scope. Expressions
and qualified references require an alias; aliases may be identifiers or
strings. Keys must be unique within each `uses` clause. For expressions
that need type context, use a typed constant or a typed local in an
expression block. Clauses may be empty and accept trailing commas.

An initializer receives its static values as keyword arguments of
`initialize()`, and a procedure as keyword arguments of `prepare()`. Each
expression is evaluated once per consumer per instance during compilation;
constants are shared within the instance. Static values do not schedule
procedures.

Bind an optional parameter with `uses optional(Foo) as foo`: `foo` is
`None` when omitted and `Some(value)` when supplied. For an optional
`Option[T]`, a supplied `None` becomes `Some(None)`. Ordinary bindings and
resolved defaults are ordinary values.

## Python representations

| Wirely type | Python representation |
| --- | --- |
| `unit` | `wirely.Unit` |
| `boolean` | `bool` |
| Integer types | `wirely.Int8` through `wirely.Int128`, `wirely.UInt8` through `wirely.UInt128` |
| `String` | `str` |
| `Duration` | `wirely.Duration`, with `ticks` |
| `TimeOfDay` | `wirely.TimeOfDay`, with `ticks` since midnight (up to 24:00) |
| `MonotonicTime` | `wirely.MonotonicTime`, with `generation` and `ticks` |
| Native enums | Generated Python enum |
| Native structs | Generated `NamedTuple` |
| `ValueType` | `wirely.ValueType` |
| `Option[T]` | `None` or `wirely.Some(value)` |
| `List[T]` | `tuple` of inner values |
| A `ValueType` parameter `T` | `wirely.DynamicValue` |
| Resources | The object the provider returned |

Integer values are immutable and check their type's range when
constructed; booleans are not integers. Read the Python integer with
`value`. They compare and hash only with values of the same class, and
have no arithmetic operators: compute with `value` and construct the
result.

Time `ticks` count units of 2^-64 s, given by
`wirely.types.time.TICKS_PER_SECOND`. `Duration.from_seconds()` and
`total_seconds` convert from and to seconds. `Unit` is immutable and
fieldless; all instances compare equal.

Ports and static values of enum or struct types require `native` enums
and structs. Code written for them relies on their numbers or fields, which
are their contract with native code; member and field names are not part
of it. [`wirely/runtime/enums.py`](../wirely/runtime/enums.py) has an
`IntEnum` for each native enum, and
[`wirely/runtime/structs.py`](../wirely/runtime/structs.py) a `NamedTuple`
for each native struct, both generated by `tools/generate-native-types`
and named after the native name, such as `KNXProtocol` for
`KNX.Protocol`. An open enum may carry numbers the runtime does not know,
for example from a newer library; they are unnamed members whose `name` is
`None`, so report such values as unsupported.

Before running a program, the runtime checks the native interfaces it uses:

- Each native enum must have the same kind, underlying type, and, for a
  closed enum, member count.
- Each native struct must have the same field types in the same order.
- Each native initializer, procedure, and runtime function must have the
  stage, keys of inputs, outputs, and `uses`, resource types, and value
  types its generated description in
  [`wirely/runtime/native_interfaces.py`](../wirely/runtime/native_interfaces.py)
  lists. Each type variable is replaced by the type its `uses` binding
  passes, and each description passes every type parameter its ports use
  through its own `uses`. An event port and a value port of `Option[T]`
  are not told apart.

Run `tools/generate-native-types` after changing any native declaration.

## Value type descriptors and dynamic values

Ports of a type given by a `ValueType` parameter, such as `value Type`,
carry `wirely.DynamicValue`s. A dynamic value is immutable and has a
`type`, a `wirely.ValueType`. Dynamic values are equal when their types
and values are equal.

- `to_text()` formats the value in the [text format](value-types.md#text-format).
- `json_encode(format=None)` returns a JSON-compatible Python value,
  without serializing JSON text.
- `to_csv_value()` returns `(text, needs_quotes)`.
- `extract(value_type)` returns the value's Python representation if its
  type is `value_type`.
- `to_native()` returns the Python representation of any type that has one.

`wirely.ValueType` is immutable and supports equality, hashing, and `str()`
with Wirely type names. Built-in descriptors are in `wirely.types.value_types`,
using Python names: `unit`, `boolean`, `int8` through `int128`, `uint8` through
`uint128`, `string`, `duration`, `time_of_day`, `monotonic_time`,
and `value_type`. `option(inner)` and `list_of(inner)` describe options and
lists. These descriptors are typed, so `extract(wirely.value_types.uint8)`
returns a `wirely.UInt8`.

- `from_native(value)` returns the dynamic value of a Python representation.
- `parse(text)` parses text.
- `json_decode(value, format=None)` converts a JSON-compatible Python value.
- `supports_json` and `supports_csv` indicate support for JSON and CSV
  conversion.

Invalid values and unsupported conversions raise `ValueError`. Composite
descriptors are available through static bindings. Each compiled program
owns its enum and struct types; use values belonging to that program.

To check that a procedure's type parameter matches its native state's,
call `wirely.native.check_state_type(services.name, type, state_type)` in
`prepare()`.

## Native objects, resources, and tasks

A `native node type` subclasses its initializer contract, which derives
from `NativeState` in `wirely.runtime.native_api`. The engine constructs
it without arguments and then calls `initialize()`, which receives the
initializer's resource dependencies and static values and returns its
resource outputs. Initialize fields there. Native singletons share one
object.

The native initializer declares the resource inputs and outputs:

```wirely
native initializer depends on Broker produces default output as "topic";
```

Dependencies and outputs use the same key rules as procedure ports. An
initializer accepts only resource dependencies and outputs. Providers are
constructed before their consumers. Each output must be an instance of the
Python type registered for the output's resource type, and the contract
annotates it with that type. See [`mqtt.py`](../wirely/native/mqtt.py) for
provider and client examples.

Register the Python type of each resource type in the module's
`native_resource_types` dictionary with `@native_resource_type`, under the
last component of the resource type's name. A program using a resource
type without a registered Python type fails when its `Engine` is
constructed.

To give procedures access to the native object, produce it as a
[resource](language.md#opaque-resource-ports) and depend on it:

```wirely
resource type ReceiverState;

native node type Receiver {
  default output: value Option[boolean];
  binding state: resource ReceiverState;
  native initializer produces state as "state";
  native procedure get_value stage input
    depends on state as "state"
    produces default output as "output";
}
```

```python
native_resource_types: dict[str, type] = {}


@native_resource_type(native_resource_types, 'ReceiverState')
@native_node_type(native_node_types, has_native_state=True)
class Receiver(contracts.ReceiverInitializer):
    def initialize(self, *, services: StateServices[Literal['state']]
                   ) -> contracts.ReceiverInitializerResult:
        self.value: bool | None = None
        return contracts.ReceiverInitializerResult(state=self)

    def receive(self, value: bool) -> None:
        self.value = value
        self.services.trigger('state')

    @native_procedure('get_value')
    class GetValue(contracts.ReceiverGetValue):
        def run(self, *, services: ProcedureServices, state: 'Receiver'
                ) -> contracts.ReceiverGetValueResult:
            value = None if state.value is None else Some(state.value)
            return contracts.ReceiverGetValueResult(output=value)
```

`self.services` is the `StateServices` object that `initialize()` also
receives. It lasts for the node's lifetime and has read-only `name`, the
dotted instance name or, for a singleton, the qualified type name, and
`shutdown_time`, when the current shutdown started. Changes to Python
fields do not automatically schedule procedures. Call
`services.trigger(output)` to schedule each procedure that depends on the
resource output with this Python name, in any node. Repeated triggers
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
output stages. Input procedures can depend only on resources. After the
first step, procedures run when dependencies or state change, when
explicitly triggered, or when due for a wakeup. A procedure without
dependencies does not automatically run every step.

`services.start_time_for_stage(stage)` returns a read-only
`TimeSnapshot` from `wirely.runtime.time_snapshot`, captured at the start
of `RunStage.INPUT`, `DEFAULT`, or `OUTPUT` (`RunStage` is defined in
`wirely.program`). Its attributes are `monotonic` (`MonotonicTime`),
`utc` (UTC-aware `datetime`), and `local` (local-aware `datetime`). Timestamps
are available for stages that have started.

Call `services.trigger_self()` to schedule the running procedure again in
the next step.

Call `services.set_wakeup_time(deadline)` at most once per invocation with
a `MonotonicTime` deadline to request another invocation. Passing `None` or
leaving it unset cancels any previous deadline. An incomparable clock
generation also causes a rerun; handle that case as
`Timer.MonotonicTimeReached` does in
[`timer.py`](../wirely/native/timer.py).

## Native functions

### Runtime functions

A `native function` runs in procedure bodies and in compile-time
expressions, so its implementation is shared by the compiler and the
runtime and lives in [`wirely/functions/`](../wirely/functions/) rather
than with the runtime-only native node types. `function_pkgs` in
[`wirely/functions/__init__.py`](../wirely/functions/__init__.py) maps each
namespace to a module there. Register the implementation in that module's
`native_functions` dictionary with its contract:

```python
from wirely import Int16
from wirely.contracts import test_library as contracts
from wirely.functions import RegisteredFunction, native_function

native_functions: dict[str, RegisteredFunction] = {}


@native_function(native_functions, contracts.Clamp)
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

Declare a compile-time-only function with `native constant function` in a
`.wrl` library included in the compilation. Before compiling, add its
Python implementation to `native_function_impls` in
`wirely.compiler.native_functions`. The key is the function's native
name, such as `'Example.Parse'`. The value is a callable or a pair
of an absolute Python module name and the name of a callable in that module:

```python
from wirely.compiler.native_functions import native_function_impls

native_function_impls['Example.Parse'] = (
    'my_integration.functions', 'parse',
)


def parse(*, context):
    value, = context.arguments
    try:
        return int(value)
    except ValueError:
        raise context.error('Expected an integer', argument=0)
```

The compiler imports module entries when a call is evaluated. Unused
functions and skipped conditional branches do not need their modules.
Implementations must be synchronous, accept `context=context`, and return
one value matching the declared return type.

`NativeFunctionContext` has read-only `function_name` (the native name),
`arguments` (a tuple of values in declaration order), `return_type`,
`arguments_ast` (a tuple of call-site expressions in the same order), and
`call` (the call node).
`services` is a read-only mapping of Python objects supplied through the
optional `services` argument to `compile_files()` or `compile_strs()`.
The compiler copies the mapping, preserving its objects by reference, and
makes it available through program generation. It defaults to empty.
Services belong to compiler structures, including optional debug graphs,
rather than runtime execution data. Service objects may manage mutable
state. Treat values and AST nodes as read-only.

Constant functions receive and return values in these representations:

| Wirely type | Python representation |
| --- | --- |
| Integer types | `int` within the type's range; not `bool` |
| Declared enums | Immutable `wirely.EnumValue` |
| Declared structs | Immutable `wirely.StructValue` |
| Other types | As [for native code](#python-representations); list results may also be lists |

An `EnumValue` has the member number as `value`, the member `name`, and its
`type`, a `wirely.EnumType` with the enum's `name`, `underlying_type`,
`members` (numbers by name), `names` (names by number), and `is_open`.
Open values may have unnamed numbers; reading `name` then raises
`ValueError`. Two values are equal when both type and number are equal.
For a native enum, `native` is the member of its generated Python enum.
Build values with the `EnumType`'s `from_name(name)` and
`from_value(number)`.

A `StructValue` has its field values in declaration order as `values`, and
its `type`, a `wirely.StructType` with the struct's `name`, its `fields` as
`(name, ValueType)` pairs, and `index(name)` for a field's position. Two
values are equal when both type and field values are equal. Build values
with the type's `make(values)`, or, for a native struct, with
`from_native(value)` from an instance of its generated `NamedTuple`. Get the
types through a `ValueType`'s `enum_type` and `struct_type`, for example
`context.return_type.struct_type`.

Use `raise context.error(message)` for a call-site diagnostic, or
supply `argument=0` to point at the first argument (indices are zero-based).
`ValueError`, `ArithmeticError`, and `OSError` also produce call-site
compilation errors.
`context.warn(message)` reports a `NativeFunction` warning at the call and
returns; it accepts `argument=` as well. A call can be evaluated more than
once, for example once per instance; equal warnings at one call are
reported once.
