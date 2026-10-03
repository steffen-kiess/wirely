# Repository guide

## Layout

`wirely/` contains the Python CLI tools and services, `.wrl` parser,
compiler, engine, standard libraries in `lib/`, and Python native blocks in
`native/`. From a checkout, use `tools/wirely` for the main CLI.
`test/` contains paired `.wrl` programs and Python assertions, with
`wirely/test.py` as their test runner. `examples/` contains sample block
programs and device configuration.

## Setup

Run commands from the repository root so local `wirely` imports resolve.
After setup, use `venv/bin/python` for direct Python commands; the
`tools/wirely` and `tools/run-tests` launchers use the venv when available.

Run `tools/setup-venv` to create `venv/` and install the core, integration,
test, and PEX dependencies. The launcher runs Wirely from the checkout.
Dependency declarations are in `pyproject.toml`.

## Coding style

For new and modified Python code, including extensionless scripts in
`tools/`, follow the [Python style guide](doc/python-style.md). Do not
reformat unrelated code. Limit lines in Markdown files to 80 characters
unless that would cause problems, for example with links.

For new and modified `.wrl` code, including examples in documentation,
follow the [Wirely style guide](doc/wirely-style.md).

When changing a block, check its `.wrl` declaration in `wirely/lib/`, its
Python implementation in `wirely/native/`, and any paired fixture in
`test/`. Keep names, parameters, and value types aligned.

## Documentation

Always keep documentation as short as possible. Avoid duplicating
information documented elsewhere when it belongs there.

Describe the behavior after the change, not the change itself. Do not
mention old behavior except in migration guides or similar documents.

Write for the reader's task: explain what to do and the behavior needed for
correct use. Omit implementation details, validation machinery, and
explanations of ordinary Python behavior unless the reader needs them.
Describe valid usage rather than listing invalid forms or checks. Document
errors only when readers need to handle or recover from them. Review every
added sentence for practical value to the intended reader.

## Tests

Run the full block suite with `tools/run-tests`. This is the preferred local
test command for most work.

Run one named test with `venv/bin/python -m wirely.test unit/types`. Replace
`unit/types` with a name in `test_names` in `wirely/test.py`. The runner also
accepts paths from its `to_compile` and `negative_tests` lists, for example
`venv/bin/python -m wirely.test test/unit/parser.wrl`.

Add end-to-end feature tests to `test/wrl/` and unit/component tests to
`test/unit/`. Additions or substantive changes to `test/wrl/` require
careful review to preserve coverage; routine import or path adjustments
are exceptions. Tests in `test/unit/` can be edited more freely. Add
compiler rejection cases to `test/negative/`.

A runnable test normally has paired `.wrl` and `.py` files in its group.
The runner compiles the block program, creates an engine, then loads the
Python file for assertions. Add new runnable tests to `test_names` in
`wirely/test.py` using names such as `wrl/knx` or `unit/types`.

Negative fixtures in `test/negative/` use `// E: message`, `// H: message`,
and `// W: message` to match diagnostics on that line; each `^` before the
letter targets one line above. For syntax errors, match `Syntax error` or a
fragment of the parser message. Start independent cases with a standalone
`// TEST` or `// TEST: label`. Each case needs expected diagnostics and
compiles separately with the default libraries. Line numbers and caret
annotations refer to the original file; declarations stay within their case.

## Working practices

Prefer a focused test for the affected block, then run the full suite when
its external services are available. State clearly when a required
dependency prevents a test from running.

## Commit messages

Use a short, imperative subject. For substantive changes, add a blank line
and concise paragraphs explaining what changed, why, and any non-obvious
behavior or compatibility impact. Make messages self-contained, with detail
based on the change rather than surrounding commits. Use one-line messages
only for trivial changes or when there is nothing useful to add.
Limit commit message lines to 72 characters unless this causes problems,
e.g. with links.

Do not describe tests executed or other validation performed. Omit
approaches discussed but not implemented, and statements about behavior
that remains unchanged.
