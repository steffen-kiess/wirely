# Wirely value types

Value types describe node parameters, data carried by connectors, and values
stored in state or temporaries. Names are case-sensitive. This reference
complements the [language guide](language.md) and
[library reference](libraries.md).

## Type overview

A runtime type represents values used during execution, for example on a
`value` or `event` connector. Parameter-only types configure a node when it
is instantiated. Value and event connectors, state, and temporaries
require runtime types. Runtime types can also be used for parameters.

| Type | Allowed values | Use |
| --- | --- | --- |
| `unit` | `()`, carrying no data | Runtime |
| `boolean` | `false`, `true` | Runtime |
| `int8` | -128 to 127 | Runtime |
| `int16` | -32,768 to 32,767 | Runtime |
| `int32` | -2,147,483,648 to 2,147,483,647 | Runtime |
| `int64` | -9,223,372,036,854,775,808 to 9,223,372,036,854,775,807 | Runtime |
| `uint8` | 0 to 255 | Runtime |
| `uint16` | 0 to 65,535 | Runtime |
| `uint32` | 0 to 4,294,967,295 | Runtime |
| `uint64` | 0 to 18,446,744,073,709,551,615 | Runtime |
| `String` | Text | Parameter-only |
| `Duration` | Non-negative elapsed time, in nanoseconds | Runtime |
| `TimeOfDay` | Time within a day, without a timezone | Runtime |
| `MonotonicTime` | A monotonic clock reading or the invalid value | Runtime |
| Declared enums | A named member | Runtime |
| Declared structs | Named fields | Runtime within the limits below |
| `ValueType` | A value type | Parameter-only |
| `Option[T]` | No value, or one value of type `T` | Runtime within the limits below |
| `List[T]` | An ordered list of values of type `T`, including an empty list | Parameter-only |

Structs and options are runtime types when their members are runtime types
and they have at most 128 storage bytes, nesting depth 16, and total member
count 128. Storage size includes alignment padding. Primitives, including
enums, have depth 0 and member count 1. Struct depth is one more than the
maximum field depth (1 for an empty struct); its member count is 1 plus the
sum of its field counts. Options and lists add 1 to their inner type's depth
and count. Repeated fields count separately; empty structs count as 1.
Types exceeding these limits remain usable for parameters and constants.

Integer ranges are inclusive. For a signed integer of width `n`, the range
is -2^(n-1) through 2^(n-1)-1; for an unsigned integer it is 0 through 2^n-1.
Enums must declare at least one member.

Value types are distinct from connector kinds. `value T` carries a current
value of type `T`; `event T` carries occurrences with payloads of type `T`.

## Values and examples

### Unit and boolean

`unit` is useful for events that only signal an occurrence. For example,
`Time.TriggerAt { Time: "08:00" }` emits a unit event each day at the specified
time. The default event type is `unit`, so no payload parameter is needed.

The unit literal is `()`:

```wrl
node source: Std.Constant { Type: unit, Value: () }
```

`()` is a value of type `unit`; `None` is an absent `Option[T]` value.

Boolean literals are `true` and `false`:

```wrl
node source: Std.Constant { Type: boolean, Value: true }
node printer: Std.DebugValuePrinter { Type: boolean }
connect source -> printer;
```

### Integers

Integer literals can use decimal, hexadecimal (`0x`), or octal (`0o`)
notation. Leading zeros do not change the base: `010` is 10 and `080` is 80.
A parameter's declared type determines the integer width and
signedness. For example:

```wrl
node small: Std.Constant { Type: uint8, Value: 42 }
node large: Std.Constant { Type: int32, Value: 100000 }
node negative: Std.Constant { Type: int8, Value: -128 }
```

Write negative integers with a leading minus sign, such as `-42`, `-0x2a`,
or `-0o52`. These all represent -42 and require a signed integer type whose
range includes that value.

### Strings

String literal syntax is based on
[Rust's string literal syntax](https://doc.rust-lang.org/reference/tokens.html#string-literals).

`String` holds text used for configuration, such as filenames, addresses,
and MQTT topics. Use double-quoted strings, with escapes such as `\n` and
`\"`, or raw strings to preserve their contents:

```wrl
node type TextSettings {
  parameter Label: String = "Kitchen";
  parameter Path: String = r"C:\logs\wirely";
  parameter Message: String = r#"Say "hello""#;
}
```

### Duration

Duration literals are based on
[ISO 8601-1:2019](https://www.iso.org/standard/70907.html), section 5.5.2.

`Duration` represents a non-negative amount of elapsed time with nanosecond
resolution. Its string format uses `P`, optional days (`D`), and an optional
time part introduced by `T` containing hours (`H`), minutes (`M`), and
seconds (`S`). At least one component is required. Seconds can have a
decimal fraction using a dot. Examples include `"PT0S"`, `"PT1.5S"`,
`"PT2H30M"`, and `"P1DT4H"`.

Days are fixed periods of 24 hours. Duration components use days, hours,
minutes, and seconds rather than calendar months or years.

```wrl
node source: Std.Constant { Type: boolean, Value: true }
node delay: Time.DebounceStable { Type: boolean, Duration: "PT1.5S" }
connect source -> delay;
```

### Time of day

`TimeOfDay` describes a clock time without a date or timezone. Hours range
from 0 to 23, minutes and seconds from 0 to 59, and fractional seconds have
microsecond resolution. String examples include `"08:00"`, `"08:00:30"`,
and `"08:00:30.125"`.

The `std` library declares the compile-time function
`TimeOfDay.AddWrap(time, offset)`, which adds an offset
written as an hours-and-minutes string, with an optional `+` or `-` sign,
and wraps at midnight:

```wrl
node morning: Time.TriggerAt { Time: "08:00" }

node type Schedule {
  parameter Late: TimeOfDay = TimeOfDay.AddWrap("23:30", "01:00");
}
```

Here, `Late` defaults to 00:30.

### Monotonic time

`MonotonicTime` is a point on a monotonic clock, used for deadlines and
elapsed-time calculations. It contains a clock generation and a nanosecond
reading. Readings from the same generation can be compared; generations
distinguish clock histories when the clock's continuity is lost. A
monotonic time is distinct from a wall-clock time or a time of day.

Use `MonotonicTime.Invalid` as an initial value when there is no deadline:

```wrl
node deadline: Std.Constant {
  Type: MonotonicTime,
  Value: MonotonicTime.Invalid,
}
```

Time library nodes produce and work with monotonic times, including
`Time.MonotonicTimeReached`.

### Type parameters

`ValueType` is the type of a parameter whose value is itself a type, such as
`boolean`, `uint16`, or `Option[boolean]`. Other parameters and connectors
can refer to that parameter:

```wrl
node type ConstantValue {
  parameter Type: ValueType;
  parameter Initial: Type;
  default output: value Type;
  node constant: Std.Constant { Type: Type, Value: Initial }
  connect constant -> default output;
}
```

### Optional values and lists

`Option[T]` represents either no value or one value of type `T`. Use `None`
for absence and `Some(x)` for presence, where `x` has type `T`.
`Some` takes exactly one argument and requires an `Option[T]` context:

```wrl
node absent: Std.Constant { Type: Option[boolean], Value: None }
node literal: Std.Constant { Type: Option[boolean], Value: Some(true) }
node source: Std.Constant { Type: boolean, Value: true }
node present: Std.OptionSome { InnerType: boolean }
connect source -> present;
```

`List[T]` represents an ordered list parameter containing values of type
`T`. List literals use brackets, such as `[]`, `[1, 2]`, or
`["first", "second"]`. For example:

```wrl
node type ListSettings {
  parameter Names: List[String] = ["Kitchen", "Hall"];
  parameter Values: List[uint8] = [1, 2, 3];
}
```

Generic types can nest, for example `Option[List[uint8]]` or
`List[Option[boolean]]`. A type parameter can also be the inner type, as in
`Option[Type]`.

`List[T]` is a single list-valued datum. A `multi value T` input instead
accepts several connections, each carrying a value of type `T`.

## JSON and CSV support

Serialization support is used by library nodes such as MQTT providers and
`CSV.ValueWriter`.

| Type | JSON | CSV |
| --- | --- | --- |
| `boolean` | Boolean | `false` or `true` |
| All integer types | Number | Decimal integer |
| `String` | String | No |
| Declared enums | Member name | No |
| `unit`, `Duration`, `TimeOfDay`, `MonotonicTime`, `ValueType`, structs | No | No |
| `Option[T]` | No direct type-level conversion | Supported when `T` supports CSV |
| `List[T]` | No | No |

For enums, the default JSON format encodes and decodes names such as
`"Enabled"` and `"StandbyMode"`. Encoding with `Format = "SnakeCase"` uses
strings such as `"enabled"` and `"standby_mode"`. Use the default format for
receiving enum values.

For CSV, an absent optional value becomes an empty field; a present value
uses the inner type's conversion. MQTT's `ValueOptionProvider` handles
optionality itself: it publishes an empty payload for `None`, and JSON
encoded using the inner type for a present value.


## Runtime binary representation

Each runtime type has a fixed binary representation, independent of its
value. Its data size is the number of encoded bytes. Its alignment is the
required multiple for its starting byte offset in aligned storage. Its
storage size rounds the data size up to that alignment, adding zero tail
padding so consecutive values start at aligned offsets.

All multibyte values are little-endian. Signed integers use two's
complement.

| Type | Data bytes | Alignment | Encoding |
| --- | --- | --- | --- |
| `unit` | 0 | 1 | Empty |
| `boolean` | 1 | 1 | `00` or `01` |
| Integer of width N bytes | N | N | Declared integer width |
| Enum | Underlying integer size | Underlying integer alignment | Member number |
| Struct | Through the last field's data bytes | Maximum field alignment | Fields in declaration order |
| `Duration` | 8 | 8 | `u64` nanoseconds |
| `MonotonicTime` | 16 | 8 | `u64` generation, then `u64` nanoseconds |
| `TimeOfDay` | 8 | 8 | `u64` nanoseconds since midnight |
| `Option[T]` | Data size of T + 1 | Alignment of T | Payload, then presence byte |

Each enum uses its declared underlying integer type.

Struct fields start at offsets aligned for their types, in declaration
order. Each field reserves its full storage size before the next field;
internal padding is zero. The outer struct's final tail padding is omitted
from serialized data. An empty struct has zero data and storage size and
alignment 1. With fields `a: Option[uint64]` then `b: uint8`, `b` starts
at offset 16; data size is 17, alignment is 8, and storage size is 24.

An option's presence byte follows its payload's data bytes: `00` means
`None`, with all payload bytes zero; `01` means `Some(value)`. Nested
flags follow the underlying payload from innermost to outermost, reusing
tail padding. For `Option[Option[uint64]]`, the flags occupy offsets 8 and
9: data size is 10, alignment is 8, and storage size is 16. `Some(None)` ends
in `00 01`; `None` contains ten zero bytes.

Duration and monotonic fields fit unsigned 64-bit integers. Monotonic
`(0, 0)` retains its invalid-timestamp meaning. The time-of-day format
extends through `86_400_000_000_000` nanoseconds (24:00). Wirely currently
accepts times before 24:00 at microsecond precision, without a timezone.

Boolean and presence bytes are exactly `00` or `01`. Absent payloads and
tail padding contain only zeros. Integer values fit their encoded widths,
and enum numbers identify declared members. Each serialized value has
exactly the data size of its resolved runtime type.
