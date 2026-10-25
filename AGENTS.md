# Repository guide

## Layout

`wirely/` contains the Python CLI tools and services, `.wrl` parser,
compiler, engine, standard libraries in `lib/`, Python native
implementations in `native/`, and the graphical editor in `editor/`, whose
`program`, `graph_model`, and `layout` modules do not depend on Qt. From a
checkout, use `tools/wirely` for the main CLI. `tests/` contains compiler fixtures
and runtime/component tests; `tests/infra/` contains the test runner and
helpers for tests.
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
`tests/`. Keep names, parameters, and value types aligned.

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

Read the [test guide](doc/tests.md) for test structure, authoring conventions,
and execution commands.

Add slow tests only when they provide substantial, distinct coverage that
cannot be obtained with a cheaper test. Keep fixtures small and avoid
repeated compilation, large datasets, or subprocess startup unless needed
to test the behavior. If the coverage does not justify the runtime cost,
do not add the test.

Additions or substantive changes to `tests/wrl/` or `tests/misc/` require careful
review to preserve coverage; routine import or path adjustments are exceptions.

Test compiler behavior with `.wrl` fixtures and Wirely `assert` statements.
Keep `tests/compiler/` free of Python files. Do not test compiler
representations such as ASTs, graphs, or inferred usage, or compiler
evaluation mechanics through Python callbacks. Use compilation as setup
when needed to test runtime or native Python APIs; keep language checks in
compiler fixtures. Consolidate cases that check the same behavior while
preserving distinct boundaries.

## Working practices

When restructuring code, preserve existing behavior and comments, including
TODOs, explanations, and debugging notes, unless the change gives a concrete
reason to remove or update them. Moving or indenting code is not such a
reason. Remove TODOs resolved by the change; retain unresolved ones with
the relevant code. Do not shorten comments in ways that lose their meaning.

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
