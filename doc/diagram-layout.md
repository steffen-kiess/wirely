# Diagram layout

A diagram is the drawing of a graph: the nodes and other declarations of a
scope placed as boxes, with their ports connected by lines. `Diagram.*`
attributes store a diagram in the source so that every editor shows the same
layout. They never affect compilation. An editor that follows this document
places boxes and ports as described here; an editor that deviates draws a
different picture of the same program.

## Grid units

All coordinates and sizes are in grid units. One grid unit is the height of
a row of ports, roughly the height of a line of text. The
graphical editor snaps to this grid. `x` grows to the right and `y`
downward. Values can be fractional; editors write whole numbers as integers.

## Attributes

| Attribute | Applies to | Value |
|---|---|---|
| `Diagram.Position(X = 1, Y = 2)` | diagram items | top-left corner |
| `Diagram.Width = 12` | diagram items | positive number |
| `Diagram.Height = 6` | diagram items | positive number |
| `Diagram.DefaultWidth = 12` | node types | positive number |
| `Diagram.DefaultHeight = 6` | node types | positive number |
| `Diagram.Row = 2` | `input` and `output` declarations | integer from 0 |
| `Diagram.ShowValue` | node type parameters | flag |

Diagram items are node instances, and `input` and `output` declarations,
parameters, constants, states, temporaries, and procedures. Each attribute
can appear once per declaration; an instance can also set its attributes as
inner attributes of its body:

```wirely
node dimmer: Lights.Dimmer {
  #![Diagram.Position(X = 10, Y = 4)]
  #![Diagram.Width = 12]
  GroupAddress: "1/2/3",
}
```

Items without `Diagram.Position` are placed by the editor. Editors write
positions only for items the user places.

## Size

The width of an instance is its `Diagram.Width`, otherwise the
`Diagram.DefaultWidth` of its node type, otherwise 8. Other items use their
`Diagram.Width` or 8. There is no minimum width; long text is shortened.

The height is the larger of the minimum height `m` and the requested height:
the item's `Diagram.Height`, for an instance otherwise the
`Diagram.DefaultHeight` of its node type. Without a requested height, the
height is `m`.

## Header and rows

Each item starts with a header of height 2: its name in the first grid unit,
its type in the second. Below the header follow rows, each one grid unit
high. Row `r` covers `y + 2 + r` to `y + 3 + r`, where `y` is the item's top
edge, and its ports lie in its middle. First come the port rows, then the
value rows:

- `P`, the number of port rows, is one more than the highest row used by a
  port on either side, or 0 if no port uses a row. Unused rows below the
  highest one count.
- `V`, the number of value rows, is the number of parameters of the node
  type marked `Diagram.ShowValue`, whether or not the instance binds them.
  Value row `i` is row `P + i`.
- With `R = P + V`, the minimum height is `m = 2 + R`, so the last row
  ends at the bottom edge.

The geometry of an item therefore depends only on its declarations and those
of its node type, not on bound values, connections, or text.

## Ports

For an item at `(x, y)` with width `w`:

| Port | Position |
|---|---|
| Input on row `r` | `(x, y + 2.5 + r)` |
| Output on row `r` | `(x + w, y + 2.5 + r)` |
| Input on the header | `(x, y + 1.5)` |
| Output on the header | `(x + w, y + 1.5)` |

Header ports lie next to the type, one unit above row 0. Ports are half a
unit off the grid lines, so ports of items placed at whole-number positions
line up with each other.

The ports of an instance come from its node type's `input` and `output`
declarations:

- A port with `Diagram.Row` uses that row. Inputs and outputs share row
  numbers, so input row 2 and output row 2 are level.
- Otherwise, a `default input` or `default output` is on the header.
- The remaining named ports fill the free rows of their side from row 0 in
  declaration order. Skipped rows stay empty and can separate groups.

Two ports on the same side cannot use the same row.

Other items have fixed ports:

| Item | Ports | `m` |
|---|---|---|
| State | input on the header; `old` on row 0, `new` on row 1 | 4 |
| Temporary | input and output on the header | 2 |
| Constant, parameter | output on the header | 2 |
| `input` declaration | output on the header | 2 |
| `output` declaration | input on the header | 2 |
| Procedure | its inputs and outputs fill rows from 0 in order | `2 + P` |

Programs with errors, such as an unknown node type or a connection to a
missing port, have no defined layout; editors may add ports and rows.

## Values

A value row shows a parameter marked `Diagram.ShowValue` with the value
bound by the instance. Unbound parameters keep their row and show their
default, show that an optional parameter is absent, or show that a required
parameter is missing. Use `Diagram.ShowValue` for values that tell instances
apart, such as addresses and topics.

## Example

```wirely
node type Dimmer {
  #![Diagram.DefaultWidth = 10]
  #[Diagram.ShowValue]
  parameter GroupAddress: String;
  #[Diagram.ShowValue]
  optional parameter Label: String;
  default input: value uint8;
  input Enable: value boolean;
  #[Diagram.Row = 2]
  input Reset: value boolean;
  default output: value uint8;
  output Active: value boolean;
  // ...
}

#[Diagram.Position(X = 10, Y = 4)]
node dimmer: Dimmer { GroupAddress: "1/2/3" }
```

`Enable` fills row 0 and `Reset` uses row 2, so `P = 3` with row 1 empty.
Two values give `V = 2`, so `m = 2 + 5 = 7`. The instance covers `x` from 10
to 20 and `y` from 4 to 11:

| Port or row | Position |
|---|---|
| `default input`, `default output` | `(10, 5.5)`, `(20, 5.5)` |
| `Enable`, `Active` | `(10, 6.5)`, `(20, 6.5)` |
| `Reset` | `(10, 8.5)` |
| `GroupAddress`, `Label` values | rows 3 and 4, from `y` 9 to 11 |

## Editor choices

Items overlapping each other are drawn in source order, later items on top.
Colours, fonts, text, and the routing of connection lines are up to each
editor.
