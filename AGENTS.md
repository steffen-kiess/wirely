# Repository guide

## Layout

`wirely/` contains the Python CLI tools and services, `.wrl` parser,
compiler, engine, standard libraries in `lib/`, Python native node types
in `native/`, and the graphical editor in `editor/`, whose `program`,
`graph_model`, and `layout` modules do not depend on Qt. From a checkout,
use `tools/wirely` for the main CLI. `test/` contains compiler fixtures
and runtime/component tests, with `wirely/test.py` as their test runner.
`examples/` contains sample programs and device configuration.

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
follow the [Wirely style guide](doc/wirely-style.md). Tag Wirely code blocks
in Markdown as `wirely`.

When changing a node type, check its `.wrl` declaration in `wirely/lib/`, its
Python implementation in `wirely/native/`, and any paired fixture in
`test/`. Keep names, parameters, and value types aligned.

## Documentation

Omit declarations marked `#[Internal]` (or `#![Internal]`) and members of
internal namespaces or types from `doc/libraries.md`.

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

Run the full test suite with `tools/run-tests`. This is the preferred local
test command for most work.

Run one named test with `venv/bin/python -m wirely.test unit/types`. Replace
`unit/types` with a name in `test_names` in `wirely/test.py`. The runner also
accepts paths from its `to_compile` and `negative_tests` lists, for example
`venv/bin/python -m wirely.test test/compiler/syntax.wrl`.

Add end-to-end feature tests to `test/wrl/` and runtime, Python API, or
infrastructure unit/component tests to `test/unit/`. Additions or substantive
changes to `test/wrl/` require careful review to preserve coverage; routine
import or path adjustments are exceptions.

Test compiler behavior in `test/compiler/` with `.wrl` fixtures and Wirely
`assert` statements. Register these fixtures in `to_compile` in
`wirely/test.py`. Keep this group free of Python files. Add compiler
rejection cases to `test/negative/`.
Do not test compiler representations such as ASTs, graphs, or inferred usage,
or compiler evaluation mechanics through Python callbacks. Consolidate
cases that check the same behavior while preserving distinct boundaries.

Runtime/component tests normally pair `.wrl` and `.py` files. The runner
compiles the program, creates an engine, then loads the Python assertions.
Register them in `test_names` using names such as `wrl/knx` or `unit/types`.
Use compilation as setup when needed to test runtime or native Python APIs;
keep language checks in compiler fixtures.

Negative fixtures in `test/negative/` use `// E: message`, `// H: message`,
and `// W: message` to match diagnostics on that line; each `^` before the
letter targets one line above. For syntax errors, match `Syntax error` or a
fragment of the parser message. Start independent cases with a standalone
`// TEST` or `// TEST: label`. Each case needs expected diagnostics and
compiles separately. Line numbers and caret annotations refer to the
original file; declarations stay within their case.

For cases with traversal-dependent diagnostics, add `// SE: message`,
`// SH: message`, or `// SW: message` expectations. With
`WIRELY_COMPILER_SHUFFLE_SEED` set, these replace that case's ordinary
expectations and match anywhere within the case in the same file. Keep the
ordinary annotations for exact matching without a seed. Both forms require
matching diagnostic kinds and counts; use specific, stable message fragments.

Negative cases load no libraries by default. Use `// LIBRARIES: std, matter`
to select library filenames without `.wrl`, including any dependencies.
A directive before the first `// TEST` sets the file default; one inside a
case replaces it for that case only. `// LIBRARIES:` selects no libraries.
In an unmarked file, the directive applies to its single case.

## Working practices

Prefer a focused test for the affected node type, then run the full suite when
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
