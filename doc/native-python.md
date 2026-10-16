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
`Std.Constant:run`, selects a static method or class method: the part
before `:` names a registered class, and the part after it the method.
Procedures receive a native state only through a resource dependency, as
shown [below](#native-objects-resources-and-tasks).

Native registries must be available when constructing an `Engine` from
`wirely.runtime.engine`. It resolves classes using the native type names
stored in `Program`, such as `"ABB.JRA.Blind"`. Each engine creates its own
native states from the program's initializer values and resource bindings.

For example, `Std.OptionSome` declares:

```wirely
namespace Std {
  node type OptionSome {
    parameter InnerType: ValueType;
    default input: value InnerType;
    default output: value Option[InnerType];

    native procedure run
      depends on default input as "input"
      produces default output as "output";
  }
}
```

Its Python implementation is:

```python
import wirely
from wirely.native import native_node_type

native_node_types = {}


@native_node_type(native_node_types, has_native_state=False)
class OptionSome:
    @staticmethod
    def run(*, context):
        context.set_output('output', wirely.Some(context.inputs['input']))
```

Register the class of a `native node type` with `has_native_state=True`,
and other classes with `has_native_state=False`, as above. Either kind of
class can implement procedures. Node types of either kind can also
declare `.wrl` state.

Procedures run synchronously with the keyword argument `context`; their
return values are ignored. Use native node tasks for asynchronous I/O.

## Inputs, outputs, and state

`context.name` is read-only and uses the node naming described under
[native objects](#native-objects-resources-and-tasks).

`context.inputs` is a read-only mapping of dependency names to values for
this invocation. Native procedures currently accept only single
value/event dependencies. Use a Wirely procedure to aggregate multi value
inputs.
A `value List[T]` input carries a single tuple-valued datum.

A resource dependency supplies an immutable `ResourceValue` from
`wirely.runtime.resources`. Its `object` is what the provider's initializer
wrote for the connected output, and its `type` is a `ResourceType` with the
resource type's `name`, such as `'KNX.Bus'`, and its `python_type`. Resource
identity is stable, and changes inside an object do not trigger a procedure
rerun.

`context.set_output(name, value)` writes a procedure output port. Write
every declared output once per invocation, including `None` for no event.
To preserve state, write its previous value again.

Input and output names are the port names or `as "key"` strings of the
`.wrl` `depends on` and `produces` clauses. See
[state and execution](language.md#state-and-execution) for ports and state
access.

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

`context.values` is a read-only mapping of that consumer's bindings in
native Python form. An initializer's bindings are also available as
`self.values` throughout the native node's lifetime. Each expression is
evaluated once per consumer per instance during compilation; constants
are shared within the instance. Static values do not schedule procedures.
Omitting `uses` provides an empty mapping.

An undeclared key raises `KeyError`. Bind an optional parameter with
`uses optional(Foo) as foo`: `values['foo']` contains `None` when
omitted and `wirely.Some(value)` when supplied. For an optional `Option[T]`,
a supplied `None` becomes `wirely.Some(None)`. Ordinary bindings and resolved
defaults contain their ordinary values. `ValueType` bindings provide
`wirely.ValueType` descriptors, including inside options.

## Python representations for native externs

| Wirely type | Python representation |
| --- | --- |
| `unit` | `wirely.Unit` |
| `boolean` | `bool` |
| All integer types | `int` within the type's range; not `bool` |
| `String` | `str` |
| `Duration` | `wirely.types.time.Duration`, with `ticks` |
| `TimeOfDay` | `wirely.TimeOfDay`, with `ticks` since midnight (up to 24:00) |
| `MonotonicTime` | `wirely.types.time.MonotonicTime`, with `generation` and `ticks` |
| Declared enums | Immutable `wirely.EnumValue` |
| Declared structs | Immutable `wirely.StructValue` |
| `ValueType` | `wirely.ValueType` |
| `Option[T]` | `None` or `wirely.Some(value)` |
| `List[T]` | `tuple` of inner values |

`List[T]` values can be supplied as Python lists or tuples; nested lists
are normalized into tuples.

Time `ticks` count units of 2^-64 s, given by
`wirely.types.time.TICKS_PER_SECOND`. `Duration.from_seconds()` and
`total_seconds` convert from and to seconds.

`Unit` is immutable and fieldless; all instances compare equal.

An `EnumValue` has the member number as `value`, the member `name`, and its
`type`, a `wirely.EnumType` with the enum's `name`, `underlying_type`,
`members` (numbers by name), `names` (names by number), and `is_open`.
Open values may have unnamed numbers; reading `name` then raises
`ValueError`. Two values are equal when both type and number are equal;
compare with numbers or Python enums through `value` or `native`. An enum
output accepts only `EnumValue`s of its own enum type.

Code written for a specific enum relies on its numbers, so declare that
enum `native`. For a native enum, `native` is the member of the Python
`IntEnum` that [`wirely/runtime/enums.py`](../wirely/runtime/enums.py)
registers under its native name, and raises `ValueError` for other enums.
An open enum may carry numbers the runtime does not know, for example from
a newer library; `native` then gives an unnamed member whose `name` is
`None`, so report such values as unsupported. Code handling any enum
generically, through `EnumType`, works with every enum.

The numbers are a native enum's contract with native code; member names are
not part of it. Before running a program, the runtime checks that it knows
each native enum the program uses, with the same kind, underlying type,
and, for a closed enum, member count.

A `StructValue` has its field values in declaration order as `values`, and
its `type`, a `wirely.StructType` with the struct's `name`, its `fields` as
`(name, ValueType)` pairs, and `index(name)` for a field's position. Two
values are equal when both type and field values are equal. Build values
with the type's `make(values)`. `native` is an instance of the `NamedTuple`
that [`wirely/runtime/structs.py`](../wirely/runtime/structs.py) registers
for the struct type, with fields by name, and raises `ValueError` for
struct types without one; `from_native(value)` builds a `StructValue` from
such an instance. A struct output accepts only `StructValue`s of its own
struct type.

`tools/generate-native-types` generates both files from the standard
library: Python enums for its native enums, and `NamedTuple`s for the
struct types listed in that script. Run it after changing either.

Read a present option's payload with `.value`. A present optional unit is
`wirely.Some(wirely.Unit())`, distinct from absence, `None`.
Event ports use the same representation, adding an outer option:

| Port | No event | Example occurrence |
| --- | --- | --- |
| `event unit` | `None` | `wirely.Some(wirely.Unit())` |
| `event boolean` | `None` | `wirely.Some(True)` |
| `event Option[boolean]` | `None` | `wirely.Some(None)` for an absent payload; `wirely.Some(wirely.Some(True))` for a present payload |

These encodings apply to both reads and writes. Value ports carry
values directly. After emitting an event, schedule another invocation with
`context.trigger_self()` to write `None`.

## Value type descriptors

`wirely.ValueType` is immutable and supports equality, hashing, and `str()`
with Wirely type names. Built-in descriptors are in `wirely.types.value_types`,
using Python names: `unit`, `boolean`, `int8` through `int128`, `uint8` through
`uint128`, `string`, `duration`, `time_of_day`, `monotonic_time`,
and `value_type`.

- `validate(value)` normalizes a Python value; `parse(text)` parses text.
- `to_text(value)` formats a value in the
  [text format](value-types.md#text-format).
- `struct_type` and `enum_type` give the `StructType` or `EnumType` of a
  struct or enum type.
- `supports_json` indicates support for `json_encode(value, format=None)`
  and `json_decode(value, format=None)`. These convert Python values to or
  from JSON-compatible Python values, without serializing JSON text.
- `supports_csv` indicates support for `to_csv_value(value)`, which returns
  `(text, needs_quotes)`.

Invalid values and unsupported conversions raise `ValueError`. Composite
descriptors are available through static bindings. For a declared enum or
struct, pass its type as a `ValueType` parameter; the descriptor's
`enum_type` provides `from_name(name)` and `from_value(number)`, and its
`struct_type` provides `make(values)`. Each compiled program owns its enum
and struct types; pass values belonging to that program.

## Native objects, resources, and tasks

A `native node type` usually subclasses `NativeState` from
`wirely.runtime.contexts`. The engine calls `Class(context=context)` with a
`NativeNodeContext` from the same module. Define `__init__(self, *, context)`
and call `super().__init__(context=context)`.

`self.context` lasts for the node's lifetime and has read-only attributes:

| Attribute | Value |
| --- | --- |
| `values` | Initializer static bindings |
| `inputs` | Initializer resource mapping |
| `name` | Dotted instance name, or qualified type name for a singleton |

`self.values` and `self.inputs` give the same mappings. Native
singletons share one object.

The native initializer declares the constructor's resource inputs and
outputs:

```wirely
native initializer depends on Broker produces default output as "topic";
```

Dependencies and outputs use the same key rules as procedure ports. Read a
resource input with `context.inputs['Broker'].object` in the constructor or
`self.inputs['Broker'].object` in tasks. The constructor writes each
produced resource output with `context.set_output(key, value)`, as
procedures do, usually passing the native object itself. The value must be
an instance of the Python type registered for the output's resource type.
Providers are constructed before their consumers. An initializer accepts
only resource dependencies and outputs. See
[`mqtt.py`](../wirely/native/mqtt.py) for provider and client examples.

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
native_resource_types = {}


@native_resource_type(native_resource_types, 'ReceiverState')
@native_node_type(native_node_types, has_native_state=True)
class Receiver(NativeState):
    def __init__(self, *, context):
        super().__init__(context=context)
        context.set_output('state', self)
        self.value = None

    def receive(self, value):
        self.value = value
        self.trigger('state')

    @staticmethod
    def get_value(*, context):
        value = context.inputs['state'].object.value
        if value is not None:
            value = wirely.Some(value)
        context.set_output('output', value)
```

Changes to Python fields do not automatically schedule procedures. Call
`self.trigger(key)` to schedule each procedure that depends on this
object's output `key`, in any node. Repeated triggers before execution
schedule one invocation. Inside a procedure, use `context.trigger_self()`
instead.

Startup calls `start_tasks(task_group, async_exit_stack)`, then awaits
`start_tasks_async(task_group, async_exit_stack)` on each native object.
Use these hooks for task creation and resource acquisition; initialize
fields in the constructor. Use the supplied `AsyncExitStack` for resource
contexts and cleanup callbacks, which run in reverse registration order.

Use `self.start_task(task_group, async_exit_stack, coroutine)` for a task
that runs until cancellation during cleanup. Tasks can update Python fields
and trigger procedures, or send data handed off by output procedures.

## Scheduling and time

Procedures run in dependency order within input, default calculation, and
output stages. Input procedures can depend only on resources. After the
first step, procedures run when dependencies or state change, when
explicitly triggered, or when due for a wakeup. A procedure without
dependencies does not automatically run every step.

`context.start_time_for_stage(stage)` returns a read-only
`TimeSnapshot` from `wirely.runtime.time_snapshot`, captured at the start
of `RunStage.INPUT`, `DEFAULT`, or `OUTPUT` (`RunStage` is defined in
`wirely.program`). Its attributes are `monotonic` (`MonotonicTime`),
`utc` (UTC-aware `datetime`), and `local` (local-aware `datetime`). Timestamps
are available for stages that have started.

Call `context.trigger_self()` to schedule the running procedure again in
the next step.

Call `context.set_wakeup_time(deadline)` at most once per invocation with a
`MonotonicTime` deadline to request another invocation. Passing `None` or
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
`native_functions` dictionary, using the last component of the function's
native name:

```python
from wirely.functions import native_function

native_functions = {}


@native_function(native_functions, 'Clamp')
def clamp(*, context):
    value, low, high = context.arguments
    return min(max(value, low), high)
```

The context has read-only `function_name`, `arguments` (a tuple of native
values in declaration order), and `return_type` (a `wirely.ValueType`). Build a
struct result with `context.return_type.struct_type`. Implementations must
be synchronous and pure: the result depends only on the arguments, and
calling the function has no side effects. They must return a value of the
declared return type for every argument; return an `Option` when a result
may be absent. An exception is an internal error: during compilation it
stops compilation, and at runtime it propagates out of `Engine.step()`. An
`Engine` resolves every function its program calls when it is constructed.

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
`arguments` (a tuple of native values in declaration order), `return_type`,
`arguments_ast` (a tuple of call-site expressions in the same order), and
`call` (the call node).
`services` is a read-only mapping of Python objects supplied through the
optional `services` argument to `compile_files()` or `compile_strs()`.
The compiler copies the mapping, preserving its objects by reference, and
makes it available through program generation. It defaults to empty.
Services belong to compiler structures, including optional debug graphs,
rather than runtime execution data. Service objects may manage mutable
state. Treat values and AST nodes as read-only. Native value conversions
apply, including `wirely.ValueType` for type values and `wirely.Unit()` for
unit. Use `raise context.error(message)` for a call-site diagnostic, or
supply `argument=0` to point at the first argument (indices are zero-based).
`ValueError`, `ArithmeticError`, and `OSError` also produce call-site
compilation errors.
`context.warn(message)` reports a `NativeFunction` warning at the call and
returns; it accepts `argument=` as well. A call can be evaluated more than
once, for example once per instance; equal warnings at one call are
reported once.
