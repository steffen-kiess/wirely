# Native Python implementations

Wirely declares node interfaces in [`wirely/lib/`](../wirely/lib/) and
implements native behavior in [`wirely/native/`](../wirely/native/).
See the [language guide](language.md) for `.wrl` syntax and the
[value type reference](value-types.md) for literals and type rules.

## Registration and procedures

`native_block_pkgs` in
[`wirely/native/__init__.py`](../wirely/native/__init__.py) maps namespaces
to modules: `Std` maps to `std`, and `ABB.JRA` to `abb_jra`. A new namespace
needs a map entry. Each module supplies a `native_block_types` dictionary;
`@native_block_type` registers a class under its Python name. Class and
procedure names match their `.wrl` declarations unless the procedure has
an alias, such as `native procedure Poll as "poll";`. The alias supplies
both the Python method name and its trigger key.

Native registries must be available when constructing an `Engine` from
`wirely.runtime.engine`. It resolves classes using the fully qualified
type names stored in `Program` as tuples,
for example `("ABB", "JRA", "Blind")`. Each engine creates its own native
states from the program's initializer values and resource bindings.

For example, `Std.OptionSome` declares:

```wrl
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
from wirely.native import native_block_type

native_block_types = {}


@native_block_type(native_block_types, has_native_state=False)
class OptionSome:
    @staticmethod
    def run(*, context):
        context.set_result('output', wirely.Some(context.inputs['input']))
```

Use `has_native_state=False` for a `node type` with native procedures, as
above. For a `native node type`, use `has_native_state=True` and instance
methods such as `def run(self, *, context)`. Either form can also declare
`.wrl` state variables.

Procedures run synchronously with the keyword argument `context`; their
return values are ignored. Use native node tasks for asynchronous I/O.

## Inputs, results, and state

`context.name` is read-only and uses the node naming described under
[native objects](#native-objects-resources-and-tasks).

`context.inputs` is a read-only mapping of dependency names to values for
this invocation. A `multi value T` input provides a tuple of connected
values in connection order, or `()` when unconnected. A `value List[T]`
input carries a single tuple-valued datum.

A resource dependency supplies the provider's native object; a multi
resource supplies a tuple of these objects in connection order. Resource
identity is stable, and changes inside an object do not trigger a procedure
rerun.

`context.set_result(name, value)` writes outputs, temporaries, and state.
Write every declared result once per invocation, including `None` for no
event. To preserve a state result, write its previous value again.

Input and result names come from the `.wrl` `depends on` and `produces`
clauses. See [state and execution](language.md#state-and-execution) for
aliases, dependencies, and state access.

## Static values and prepared data

Declare static values for each native procedure or initializer with `uses`:

```wrl
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
`uses parameter_option(Foo) as foo`: `values['foo']` contains `None` when
omitted and `wirely.Some(value)` when supplied. For an optional `Option[T]`,
a supplied `None` becomes `wirely.Some(None)`. Ordinary bindings and resolved
defaults contain their ordinary values. `ValueType` bindings provide
`wirely.ValueType` descriptors, including inside options.

## Python representations for native externs

| Wirely type | Python representation |
| --- | --- |
| `unit` | `wirely.Unit` |
| `boolean` | `bool` |
| All integer types | `int` |
| `String` | `str` |
| `Duration` | `wirely.types.time.Duration`, with `duration_ns` |
| `TimeOfDay` | `datetime.time` |
| `MonotonicTime` | `wirely.types.time.MonotonicTime`, with `generation` and `time_ns` |
| Declared enums | Members of a Python `enum.Enum` class |
| Declared structs | Immutable `wirely.StructValue` mappings |
| `ValueType` | `wirely.ValueType` |
| `Option[T]` | `None` or `wirely.Some(value)` |
| `List[T]` | `tuple` of inner values |

`List[T]` values can be supplied as Python lists or tuples; nested lists
are normalized into tuples.

`Unit` is immutable and fieldless; all instances compare equal.

Read a present option's payload with `.value`. A present optional unit is
`wirely.Some(wirely.Unit())`, distinct from absence, `None`.
Event connectors use the same representation, adding an outer option:

| Connector | No event | Example occurrence |
| --- | --- | --- |
| `event unit` | `None` | `wirely.Some(wirely.Unit())` |
| `event boolean` | `None` | `wirely.Some(True)` |
| `event Option[boolean]` | `None` | `wirely.Some(None)` for an absent payload; `wirely.Some(wirely.Some(True))` for a present payload |

These encodings apply to both reads and writes. Value connectors carry
values directly. After emitting an event, schedule another invocation with
`self.trigger('run')` to write `None`.

## Value type descriptors

`wirely.ValueType` is immutable and supports equality, hashing, and `str()`
with Wirely type names. Built-in descriptors are in `wirely.types.value_types`,
using Python names: `unit`, `boolean`, `int8` through `int64`, `uint8` through
`uint64`, `string`, `duration`, `time_of_day`, `monotonic_time`,
and `value_type`.

- `validate(value)` normalizes a Python value; `parse(text)` parses text.
- `make_struct(fields)` constructs a struct from a mapping of field values.
  Read fields with `value["field"]`; values belong to their declared type.
- `supports_json` indicates support for `json_encode(value, format=None)`
  and `json_decode(value, format=None)`. These convert Python values to or
  from JSON-compatible Python values, without serializing JSON text.
- `supports_csv` indicates support for `to_csv_value(value)`, which returns
  `(text, needs_quotes)`.

Invalid values and unsupported conversions raise `ValueError`. Composite
descriptors are available through static bindings. For a declared enum,
pass its type as a `ValueType` parameter and use `parse("MemberName")` on
that descriptor to obtain a member. Each compiled program owns its enum
classes; pass values belonging to that program.

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

Changes to Python fields do not automatically schedule procedures. Call
`self.trigger('get_value')` to schedule a procedure on this native node.
Repeated triggers before execution schedule one invocation.

Declare constructor resource inputs inside a native node type:

```wrl
native initializer depends on Broker;
```

The bindings use the same key rules as procedure inputs. Read the provider
with `context.inputs['Broker']` in the constructor or `self.inputs['Broker']`
in tasks. Providers are constructed before their consumers. An initializer
accepts only resource dependencies and has no `produces` clause.

Resource outputs need no procedure to publish a value. See
[`mqtt.py`](../wirely/native/mqtt.py) for provider and client examples.

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

Call `context.set_wakeup_time(deadline)` at most once per invocation with a
`MonotonicTime` deadline to request another invocation. Passing `None` or
leaving it unset cancels any previous deadline. An incomparable clock
generation also causes a rerun; handle that case as
`Time.MonotonicTimeReached` does in
[`time.py`](../wirely/native/time.py).

## Native functions

### Runtime functions

A `native function` runs in procedure bodies and in compile-time
expressions, so its implementation is shared by the compiler and the
runtime and lives in [`wirely/functions/`](../wirely/functions/) rather
than with the runtime-only native node types. `function_pkgs` in
[`wirely/functions/__init__.py`](../wirely/functions/__init__.py) maps each
namespace to a module there. Register the implementation in that module's
`native_functions` dictionary, using the Wirely function name:

```python
from wirely.functions import native_function

native_functions = {}


@native_function(native_functions, 'Clamp')
def clamp(*, context):
    arguments = context.arguments
    return min(max(arguments['value'], arguments['low']), arguments['high'])
```

The context has read-only `function_name` and `arguments` (native values by
declared argument name). Implementations must be synchronous and pure:
the result depends only on the arguments, and calling the function has no
side effects. They must return a value of the declared return type for
every argument; return an `Option` when a result may be absent. An
exception is an internal error: during compilation it stops compilation,
and at runtime it propagates out of `Engine.step()`. An `Engine` resolves
every function its program calls when it is constructed.

### Constant functions

Declare a compile-time-only function with `native constant function` in a
`.wrl` library included in the compilation. Before compiling, add its
Python implementation to `native_function_impls` in
`wirely.compiler.native_functions`. The key is a tuple of namespace
components followed by the function name, such as `('Example', 'Parse')`
for `Example.Parse`. The value is a callable or a pair
of an absolute Python module name and the name of a callable in that module:

```python
from wirely.compiler.native_functions import native_function_impls

native_function_impls[('Example', 'Parse')] = (
    'my_integration.functions', 'parse',
)


def parse(*, context):
    value = context.arguments['value']
    try:
        return int(value)
    except ValueError:
        raise context.error('Expected an integer', argument='value')
```

The compiler imports module entries when a call is evaluated. Unused
functions and skipped conditional branches do not need their modules.
Implementations must be synchronous, accept `context=context`, and return
one value matching the declared return type.

`NativeFunctionContext` has read-only `function_name` (the qualified name
tuple), `arguments` (native values by declared argument name),
`arguments_ast` (call-site expression nodes by name), and `call` (the call
node). `services` is a read-only mapping of Python objects supplied through
the optional `services` argument to `compile_files()` or `compile_strs()`.
The compiler copies the mapping, preserving its objects by reference, and
makes it available through program generation. It defaults to empty.
Services belong to compiler structures, including optional debug graphs,
rather than runtime execution data. Service objects may manage mutable
state. Treat values and AST nodes as read-only. Native value conversions
apply, including `wirely.ValueType` for type values and `wirely.Unit()` for
unit. Use `raise context.error(message)` for a call-site diagnostic, or
supply `argument='name'` to point at that argument. `ValueError`,
`ArithmeticError`, and `OSError` also produce call-site compilation errors.
