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

When changing a block, check its `.wrl` declaration in `wirely/lib/`, its
Python implementation in `wirely/native/`, and any paired fixture in
`test/`. Keep names, parameters, and value types aligned.

## Tests

Run the full block suite with `tools/run-tests`. This is the preferred local
test command for most work.

Run one named test with `venv/bin/python -m wirely.test types`. Replace
`types` with a name in `test_names` in `wirely/test.py`. The runner also
accepts paths from its `to_compile` and `negative_tests` lists, for example
`venv/bin/python -m wirely.test test/parser.wrl`.

A runnable test normally has both `test/<name>.wrl` and `test/<name>.py`.
The runner compiles the block program, creates an engine, then loads the
Python file for assertions. Add new runnable tests to `test_names` in
`wirely/test.py`.

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
