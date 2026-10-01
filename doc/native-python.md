# Native Python implementations

Wirely declares node interfaces and procedure dependencies in `.wrl` files
and implements native behavior in Python under
[`wirely/native/`](../wirely/native/). This guide describes the interface
used by those implementations. See the [language guide](language.md) for
declaration syntax and the [value type reference](value-types.md) for
Wirely literals and type rules.

## Finding and registering implementations

The namespace-to-module map `native_block_pkgs` in
[`wirely/native/__init__.py`](../wirely/native/__init__.py) determines which
Python module implements a namespace. For example, `Std` maps to `std`,
and `ABB.JRA` maps to `abb_jra`. A new namespace needs an entry in this
map; adding a Python file alone does not register it.

Each module supplies a `native_block_types` dictionary. Decorate a class
with `@native_block_type(native_block_types, has_state=...)` to register
it under its Python class name. The class name must match the `.wrl` node
type name exactly, and procedure method names must match their `.wrl`
declarations. Duplicate class names in the dictionary are errors.

Use `@native_procedure` on procedure methods. For stateless implementations,
use the decorator order shown below: `@native_procedure` above
`@staticmethod`. Stateful implementations use instance methods with
`@native_procedure`.

| `.wrl` declaration | Python registration and method signature |
| --- | --- |
| `node type` with native procedures | `has_state=False`; `def run(*, context)` |
| `native node type` | `has_state=True`; `def run(self, *, context)` |

Here, `has_state` means a Python object owned by the engine. A node with
`has_state=False` can still declare `.wrl` state variables. The compiler
rejects a mismatch between `native node type` and `has_state`.

## A stateless procedure

The following is the interface of `Std.OptionSome`, declared in
[`wirely/lib/std.wrl`](../wirely/lib/std.wrl):

```wrl
namespace Std {
  node type OptionSome {
    parameter InnerType : ValueType;
    implicit input : value InnerType;
    implicit output : value Option[InnerType];

    native procedure run depends on implicit input produces implicit output;
  }
}
```

Its implementation in [`wirely/native/std.py`](../wirely/native/std.py)
reads the declared dependency and writes the declared result:

```python
from wirely.native import native_block_type, native_procedure

native_block_types = {}


@native_block_type(native_block_types, has_state=False)
class OptionSome:
    @native_procedure
    @staticmethod
    def run(*, context):
        input_var = context.static.sources['implicit input']
        output_var = context.static.targets['']
        value = context.dep_values[input_var]
        context.results[output_var] = (value,)
```

The engine calls procedures synchronously with the keyword argument
`context`. A procedure communicates through this context; its return value
is ignored. Put asynchronous I/O in native node tasks, described below.

## Procedure context

`context` is a `wirely.ProcedureContext`. Its `static` member is a
`wirely.StaticProcedureContext` reused for that procedure instance.

| Static field | Meaning |
| --- | --- |
| `sources` | Names of declared dependencies mapped to variable objects |
| `targets` | Names of declared results mapped to variable objects |
| `state` | All `.wrl` state variable names mapped to variable objects |
| `name` | Node instance name, useful for diagnostics |
| `block` | The instance's `StaticBlockContext` |
| `block_parameter_values` | Parameter bindings from compilation; prefer `block.parameter_values` for resolved instance parameters |

The implicit input key is `'implicit input'`; the implicit output key is
the empty string `''`. Named inputs, outputs, state, and temporaries use
their declared names. A dependency on a child node uses the child node's
name and refers to its implicit output.

| Invocation field | How to use it |
| --- | --- |
| `dep_values` | Read dependency values using variable objects from `static.sources` |
| `results` | Write outputs and temporaries using variable objects from `static.targets` |
| `state_old` | Read state from the previous step using objects from `static.state` |
| `state_new` | Write declared state results using objects from `static.state` |
| `step_info` | Read the timestamps captured at each stage's start |
| `wakeup_time` | Set the next monotonic deadline for this procedure, or `None` |

These value dictionaries use variable objects as keys, not strings. For
each invocation, write every result listed in `produces`, including an
explicit `None` when an event does not occur. State results go into
`state_new`; other results go into `results`. The engine rejects missing
results, extra result keys, and values that fail their declared type's
validation.

A `list value T` input has a variable object with a `members` collection.
Read each connected value separately, as `Std.And` does:

```python
input_var = context.static.sources['implicit input']
values = [context.dep_values[member] for member in input_var.members]
```

This differs from a `value List[T]` input, whose single dependency value is
a Python list.

### Parameters and prepared instance data

`context.static.block.parameter_values` maps parameter names to resolved
Wirely objects. For a value parameter, call `.evaluate()` to obtain its
Python representation. A `ValueType` parameter is already a
`wirely.types.Type` object:

```python
parameters = context.static.block.parameter_values
value = parameters['Value'].evaluate()
value_type = parameters['Type']
```

The dictionary also contains metadata at `parameters['']`:
`parameters['']['BlkFilename']` is the instantiation's source filename, or
`None` for a singleton. Native implementations use it to resolve paths
relative to the `.wrl` file. Optional parameters without a supplied value
can be absent; check membership before reading them.

For validation or preprocessing once per instance, pass a `data_type` to
`@native_block_type`. Wirely calls
`data_type.create(parameter_values=...)` with the resolved parameter
dictionary while constructing the program's instances. Its return value
is available as `context.static.block.parsed_native_instance` and, for
native nodes, `self.static_block_context.parsed_native_instance`.

For example, `Std.DetectEdge` uses `data_type=EdgeData` to validate the
`EdgeType` parameter and store its parsed configuration. This works for
both stateless and stateful implementations and does not itself create
Python runtime state.

### Temporaries and state

Temporaries carry results between procedures in the same step. Declare
the producer with `produces temp` and the consumer with `depends on temp`.
The producer writes `context.results[context.static.targets['temp']]`;
the consumer reads `context.dep_values[context.static.sources['temp']]`.
`Std.TwoStageValueForward` demonstrates this arrangement.

State variables are initialized from their `.wrl` declarations. Every
procedure sees the previous step's state in `state_old`, even if another
procedure has already written `state_new` during this step. To preserve a
state result, explicitly write its old value again.

For example, `Std.Delay` declares:

```wrl
state value : Type = InitialValue;
native procedure copy_from_input depends on implicit input produces r#value;
native procedure copy_to_output produces implicit output;
```

Its two stateless methods use separate dictionaries for state and output:

```python
@native_procedure
@staticmethod
def copy_from_input(*, context):
    state_var = context.static.state['value']
    input_var = context.static.sources['implicit input']
    context.state_new[state_var] = context.dep_values[input_var]


@native_procedure
@staticmethod
def copy_to_output(*, context):
    state_var = context.static.state['value']
    output_var = context.static.targets['']
    context.results[output_var] = context.state_old[state_var]
```

These methods belong to the registered `Delay` class. The output is the
old value, while the current input becomes state for the next step.

## Python representations for native externs

| Wirely type | Python representation |
| --- | --- |
| `unit` | `None` |
| `boolean` | `bool` |
| All integer types | `int` |
| `String` | `str` |
| `Duration` | `wirely.time.Duration`, with `duration_ns` |
| `TimeOfDay` | `datetime.time` |
| `MonotonicTime` | `wirely.time.MonotonicTime`, with `generation` and `time_ns` |
| `ThermostatSystemModeEnum` | `wirely.enum.ThermostatSystemMode` |
| `ValueType` | `wirely.types.Type` instance |
| `Option[T]` | `None` or a one-element tuple `(value,)` |
| `List[T]` | `list` of inner values |

For example, a present optional unit value is `(None,)`, distinct from an
absent optional value, `None`.

Event connectors add an outer optional wrapper to their payload type:

| Connector | No event | Example occurrence |
| --- | --- | --- |
| `event unit` | `None` | `(None,)` |
| `event boolean` | `None` | `(True,)` |
| `event Option[boolean]` | `None` | `(None,)` for an absent payload; `((True,),)` for a present payload |

The same encoding applies when reading event dependencies and writing
event results. A value connector carries the type's representation
directly, without this event wrapper.

An event source must also arrange to clear its output after an occurrence.
`Std.DebugEventSource` writes its pending event, clears the pending Python
field, and triggers its procedure again so the next invocation writes
`None` instead of leaving the event in the cached output.

## Native node objects and resources

A `native node type` owns a Python object, usually a subclass of
`wirely.NativeState`. The engine constructs it as
`Class(path, engine, static_block_context=...)`. A typical constructor is:

```python
import wirely as blocks


def __init__(self, path, engine, static_block_context):
    super().__init__(path, engine, static_block_context)
    self.data = static_block_context.parsed_native_instance
```

This constructor belongs to a class registered with `has_state=True`.
The base class stores `block_path`, `engine`, and `static_block_context`.
`self.block` gives the instantiated block, and `self.block_type` its type.
For native singletons, the engine shares one object under the path
`(block_type,)`; use `self.block_type` or `self.block_or_singleton`, because
`self.block` is unavailable for a singleton.

Python fields such as a connection, pending message, or current external
value are separate from `.wrl` state. Updating a Python field does not
automatically schedule its procedure. Locate the compiled procedure in
`self.block_or_singleton.block_procedures` by its `name`, then call
`self.engine.trigger(self.block_path, proc)` after updating the field.
Pass the procedure object, not its name. Repeated triggers for the same
procedure are coalesced; keep a queue in the native object if individual
occurrences must be retained.

Resource connectors identify native service objects rather than carrying
values in `dep_values` or `results`. For example, a client with a named
`Broker: resource MQTT.Broker` input obtains its provider as follows:

```python
broker_input = self.block.block_inputs_byname['Broker']
provider_path, _ = blocks.resolve_variable(self.block_path, broker_input)
self.native_server = self.engine.native_states[provider_path]
```

`resolve_variable` follows connections, including resources forwarded
through composite nodes. The resource output identifies its provider's
native object; it does not need a procedure to publish a Python value.
See [`wirely/native/mqtt.py`](../wirely/native/mqtt.py) for provider and
client implementations.

### Tasks and cleanup

For each native object, engine startup calls
`start_tasks(task_group, async_exit_stack)`, then awaits
`start_tasks_async(task_group, async_exit_stack)`. Override the synchronous
hook to launch background tasks and the asynchronous hook when startup
needs to await resource acquisition. Constructors should initialize the
object; use these hooks for service startup.

`NativeState.start_task(task_group, async_exit_stack, coroutine)` creates a
task expected to run until cancellation and registers cancellation with
the exit stack. Unexpected normal completion is treated as an error. Use
the supplied `AsyncExitStack` to register cleanup callbacks and enter
resource context managers. Cleanup runs in reverse registration order.

Background tasks can receive data, update Python fields, and trigger input
procedures. Output procedures can hand data to a background task for
asynchronous transmission, as MQTT providers do. Procedures themselves
must complete synchronously.

## Scheduling and time

Procedures execute in dependency order within three stages:
`stage input`, the default calculation stage, and `stage output`. Input
procedures read external data and cannot declare dependencies. Output
procedures perform external side effects. Declare all dependencies and
results in `.wrl`; the engine uses them to order and cache execution.

The first step runs procedures. Later steps can reuse cached results when
dependencies and the node's `.wrl` state have not changed, unless a
procedure is explicitly triggered or its deadline has become due. A
procedure with no dependencies is therefore not automatically run on
every step.

`context.step_info` provides `stage_start_time_monotonic`,
`stage_start_time_utc`, and `stage_start_time` dictionaries, keyed by
`wirely.RunStage.Input`, `wirely.RunStage.Default`, or
`wirely.RunStage.Output`. The first holds
`MonotonicTime` values; the others hold UTC and local timezone-aware
`datetime` values. Entries exist for stages that have started.

To request another invocation at a deadline, assign a `MonotonicTime` to
`context.wakeup_time`. Each invocation starts with `None`; leave it unset
or assign `None` to cancel the previous deadline. The engine also reruns
the procedure if the clock generation makes its deadline incomparable.
Handle that case in time-based logic, as `Time.MonotonicTimeReached` does
in [`wirely/native/time.py`](../wirely/native/time.py).
