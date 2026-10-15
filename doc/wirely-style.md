# Wirely style guide

Use judgment: favor readable code over mechanical application of these
guidelines.

## Layout

- Save files as UTF-8 with LF line endings and a final newline.
- Use spaces, never tabs. Indent two spaces per level, including namespaces.
- Prefer lines of at most 80 characters. Longer lines are fine when clearer.
- Put opening braces on the declaration line and closing braces on their
  own lines, aligned with the declaration.
- Use no space before `:` and one space after it. Use spaces around
  `=` and `->`, and after commas. Keep qualified names and brackets
  compact: `Std.Constant`, `Option[boolean]`.

Use one declaration per line. Separate type declarations and logical groups
with one blank line. Keep closely related declarations together, such as
a node and its connections. Avoid blank lines immediately inside braces.

```wirely
namespace Example {
  node type Forward {
    parameter Type: ValueType;
    default input: value Type;
    default output: value Type;

    procedure run
      depends on default input as input
      produces default output as output
    {
      output = input;
    }
  }
}
```

For enum and struct bodies, put each member on its own line with a trailing
comma:

```wirely
closed enum type Mode {
  Off,
  Heat,
}
```

## Breaking lines

Keep short declarations on one line. For longer procedures, break before
`depends on` and `produces`, indenting both clauses two spaces beyond the
declaration. Keep `stage input` or `stage output` with the procedure name
when practical.

For a multiline procedure header with a body, put the opening brace on its
own line, aligned with `procedure`. Indent the body one level. A short
header may keep its opening brace on the declaration line.

```wirely
procedure run
  depends on default input as input
  produces default output as output
{
  let enabled = !input;
  output = enabled;
}
```

Break long lists after commas, adding another two spaces for their
continuation. Keep a reference and its `as` alias, or a port name and its
type, together where practical.

Prefer reference ports in hand-written code. Put the connections of typed ports
after the procedure.

```wirely
native procedure run stage output
  depends on current_value.old as "current_value_old",
    requested_value as "requested_value", Enabled
  produces current_value, default output as "output";
```

For multiline node configurations, put parameters on separate lines with
trailing commas. For multiline argument lists, break after `(` and align
the closing parenthesis with the declaration. Use trailing commas and
similar layouts for other long lists; avoid elaborate column alignment.

```wirely
node source: Std.Constant {
  Type: boolean,
  Value: true,
}
node printer: Std.DebugValuePrinter { Type: boolean }
connect source -> printer;
```

## Names and comments

Choose descriptive names. Use `PascalCase` for namespaces, types, and public
parameters and ports; prefer `snake_case` for internal nodes, state,
bindings, and procedures. Match existing interface names exactly.

Prefer `// ` comments, including consecutive lines for longer explanations.
Put comments above the code they describe and at the same indentation.
Explain purpose or non-obvious behavior rather than restating the code.

```wirely
// Keep the lights off until an occupancy value has been received.
node occupied: Std.OptionUnwrapOr {
  InnerType: boolean,
  Default: false,
}
```
