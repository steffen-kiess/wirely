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
procedure names must match their `.wrl` declarations exactly.

For example, `Std.OptionSome` declares:

```wrl
namespace Std {
  node type OptionSome {
    parameter InnerType : ValueType;
    implicit input : value InnerType;
    implicit output : value Option[InnerType];

    native procedure run
      depends on implicit input as "input"
      produces implicit output as "output";
  }
}
```

Its Python implementation is:

```python
import wirely
from wirely.native import native_block_type, native_procedure

native_block_types = {}


@native_block_type(native_block_types, has_state=False)
class OptionSome:
    @native_procedure
    @staticmethod
    def run(*, context):
        context.set_result('output', wirely.Some(context.inputs['input']))
```

Use `has_state=False` and static methods for a `node type` with native
procedures. Put `@native_procedure` above `@staticmethod`, as shown.
A `native node type` instead uses `has_state=True` and instance methods,
`def run(self, *, context)`. Here, `has_state` means a Python object owned
by the engine; either form can also declare `.wrl` state variables.

Procedures run synchronously with the keyword argument `context`; their
return values are ignored. Use native node tasks for asynchronous I/O.

## Inputs, results, and state

`context.inputs` is a read-only `Mapping[str, object]` from
`collections.abc`, backed by `types.MappingProxyType`. It holds dependency
values for this invocation; contained values are not deeply immutable.
Unknown keys raise `KeyError`. A `list value T` input provides a Python
list of connected values in connection order, or `[]` when unconnected.
A `value List[T]` input carries a single list-valued datum.

A resource dependency supplies the provider's native object; a list
resource supplies these objects in connection order. Resource identity is
stable, and changes inside an object do not trigger a procedure rerun.

`context.set_result(name, value)` writes outputs, temporaries, and state
updates through the same interface. Unknown names, repeated writes, and
invalid values raise `ValueError` at the call. Write every declared result
once per invocation, including `None` for no event. Missing results raise
`ValueError` after the procedure returns.

Keys come from the `depends on` and `produces` bindings:

- A simple identifier uses its exact name unless given an alias.
- `reference as "key"` supplies an alias, decoded using normal string
  literal rules without case conversion.
- Implicit connectors and complex references require aliases.
- Keys must be unique within each clause; an input and result may share a
  key.

Dependencies can name local inputs, temporaries, a child node's implicit
output, direct child outputs such as `sensor.Value`, or old local state
such as `count.old`. Results name local outputs, temporaries, or state.
Arbitrary expressions, deeper paths, and `count.new` reads are unsupported.

For example, a state update can bind its old value and its next value:

```wrl
state count : uint8 = 0;

native procedure run
  depends on count.old as "previous"
  produces count as "next";
```

```python
def run(*, context):
    context.set_result('next', context.inputs['previous'] + 1)
```

Old state is the previous step's snapshot, even after another procedure
writes it in this step. Old-state dependencies introduce no producer edge
or self-update cycle. To preserve a declared state result, write its old
value again. Temporaries instead pass results between procedures in the
same step. See `Std.Delay` and `Std.TwoStageValueForward` for examples.

## Parameters and prepared data

`context.static.name` identifies the node for diagnostics.
`context.static.block.parameter_values` contains resolved parameters:
call `.evaluate()` on a value parameter to obtain its Python value; a
`ValueType` parameter is already a `wirely.types.Type` object. Prefer these
resolved parameters over `context.static.block_parameter_values`, which
contains compilation bindings. Optional parameters may be absent.

The parameter dictionary also contains source metadata:
`parameters['']['BlkFilename']` is the instantiation's `.wrl` filename, or
`None` for a singleton. Use it to resolve configuration paths relative to
that file.

Pass `data_type=SomeClass` to `@native_block_type` to prepare configuration
once per instance. Wirely calls
`SomeClass.create(parameter_values=...)` with the resolved dictionary.
The returned object is available as
`context.static.block.parsed_native_instance` or, in a native node,
`self.static_block_context.parsed_native_instance`. This works with either
`has_state` setting; see `Std.DetectEdge` in
[`std.py`](../wirely/native/std.py).

## Python representations for native externs

| Wirely type | Python representation |
| --- | --- |
| `unit` | `wirely.Unit` |
| `boolean` | `bool` |
| All integer types | `int` |
| `String` | `str` |
| `Duration` | `wirely.time.Duration`, with `duration_ns` |
| `TimeOfDay` | `datetime.time` |
| `MonotonicTime` | `wirely.time.MonotonicTime`, with `generation` and `time_ns` |
| `ThermostatSystemModeEnum` | `wirely.enum.ThermostatSystemMode` |
| `ValueType` | `wirely.types.Type` instance |
| `Option[T]` | `None` or `wirely.Some(value)` |
| `List[T]` | `list` of inner values |

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
values directly. Event sources must clear an emitted event on a later
invocation: `Std.DebugEventSource` clears its pending Python field and
triggers itself again to write `None` instead of retaining a cached event.

## Native objects, resources, and tasks

A `native node type` usually subclasses `wirely.NativeState`. The engine
constructs it as `Class(path, engine, static_block_context=..., inputs=...)`;
call the base constructor with those arguments. It stores `block_path`,
`engine`, `static_block_context`, and the read-only mapping `inputs`.
`self.block` gives the instantiated block and `self.block_type` its type.
Native singletons share one object at
`(block_type,)`; use `block_type` or `block_or_singleton` because `block`
is unavailable for them.

Changes to Python fields do not automatically schedule procedures. Find
the compiled procedure by name in `self.block_or_singleton.block_procedures`
and call `self.engine.trigger(self.block_path, proc)` with that object.
Repeated triggers are coalesced; retain individual occurrences in a queue
when needed.

Declare constructor resource inputs inside a native node type:

```wrl
native initializer depends on Broker;
```

The bindings use the same key rules as procedure inputs. Read the provider
with `inputs['Broker']` in the constructor or `self.inputs['Broker']` in
tasks. Providers are constructed before their consumers. An initializer
accepts only resource dependencies and has no `produces` clause.

Resolution follows forwarded resources through composite nodes. Resource
outputs need no procedure to publish a value. See
[`mqtt.py`](../wirely/native/mqtt.py) for provider and client examples.

Startup calls `start_tasks(task_group, async_exit_stack)`, then awaits
`start_tasks_async(task_group, async_exit_stack)` on each native object.
Use these hooks for task creation and resource acquisition; initialize
fields in the constructor. Use the supplied `AsyncExitStack` for resource
contexts and cleanup callbacks, which run in reverse registration order.

`self.start_task(task_group, async_exit_stack, coroutine)` starts a task
expected to run until cancellation and registers its cancellation with the
exit stack. Normal completion is an error. Tasks can update Python fields
and trigger procedures, or send data handed off by output procedures.

## Scheduling and time

Procedures run in dependency order within input, default calculation, and
output stages. Input procedures can depend only on resources. After the
first step, the engine can reuse cached results when dependencies and
state have not changed, unless explicitly triggered or due for a wakeup.
No-dependency procedures therefore do not automatically run every step.

`context.step_info` holds timestamp dictionaries keyed by
`wirely.RunStage.Input`, `Default`, or `Output`, for stages already started:

| Field | Timestamp |
| --- | --- |
| `stage_start_time_monotonic` | `MonotonicTime` |
| `stage_start_time_utc` | UTC timezone-aware `datetime` |
| `stage_start_time` | Local timezone-aware `datetime` |

Set `context.wakeup_time` to a `MonotonicTime` deadline to request another
invocation. It starts as `None` each time; leaving it unset cancels any
previous deadline. An incomparable clock generation also causes a rerun;
handle that case as `Time.MonotonicTimeReached` does in
[`time.py`](../wirely/native/time.py).
