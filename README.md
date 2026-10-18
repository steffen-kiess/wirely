Wirely
======

Wirely is a node-based language and runtime for low-level home automation.
It connects signals from KNX group addresses, MQTT topics, and other device
interfaces through nodes that process values and apply logic. For example,
it can listen for boolean values on two KNX group addresses and publish
`first_value AND NOT second_value` to a third. Wirely works directly with
these signals; it does not model higher-level concepts such as rooms,
as Home Assistant does.

See the [language documentation](doc/language.md) for how to write Wirely
programs and the [changelog](CHANGELOG.md) for release notes and migration
steps.

Installation
------------

Wirely requires Python 3.11 or newer. Install the core package with:

```
python3 -m pip install wirely
```

The core install includes the compiler and command-line interface. The
installed `wirely` command and `python3 -m wirely` provide the same
subcommands. For example:

```
wirely compile program.wrl
python3 -m wirely compile program.wrl
```

Use `wirely compile --dump-program program.wrl` to print the compiled
runtime program, including its variables, configuration, and procedures.
Add `--timings` to print the duration of each compiler stage.

Install optional integrations as needed:

```
python3 -m pip install 'wirely[knx]'           # KNX
python3 -m pip install 'wirely[mqtt]'          # MQTT
python3 -m pip install 'wirely[two-n]'         # 2N devices
python3 -m pip install 'wirely[graphviz]'      # show-graph
python3 -m pip install 'wirely[gpio]'          # GPIO
python3 -m pip install 'wirely[all]'           # all optional integrations
python3 -m pip install 'wirely[editor]'        # graphical editor
```

Set `WIRELY_VERBOSE=1` to print node creation, connection setup, and each
value sent or received by the KNX, MQTT, 2N, CSV, and Process nodes.

`wirely edit program.wrl` opens a program in a graphical editor with tabs
for its source files and diagrams. Edits in either view update the other.
Right-click a tab to split the view, move it to another pane, or detach it
into a window. You can also drag tabs between panes, onto pane edges to
split, or outside the editor to detach. Closing a detached window returns
its tabs to the main window. Layouts last for the current session.
A diagram is read-only while its source has syntax errors. In a diagram:

- Drag a node to store its position in the source; right-click it and choose
  Reset to Automatic Position to remove it again. Use View → Snap to Grid
  to toggle snapping for all diagrams. A selected group snaps its movement
  while keeping its relative positions.
- Drag from a port to another port to connect them. Drag a connection away
  from an input to move or remove it.
- Press Delete to remove the selection and F2 to rename it.
- Right-click the background to add nodes and declarations.
- Double-click a node to open the diagram of its node type.
- Press Ctrl+E to show the source of the selection, and Ctrl+Shift+E in the
  source to show the element at the cursor in its diagram.

`wirely export-image program.wrl -o diagram.pdf` draws the program diagram as
the editor shows it, as a PDF or PNG file. Use `--scope Name` for the diagram
of a node type and `--scale` for the PNG resolution. Diagrams use the DejaVu
Sans font, or a fallback font if it is not installed. See
[diagram layout](doc/diagram-layout.md) for how layouts are stored.

The `test` extra adds dependencies used only by the test suite. Install
`wirely[all,test]` to include both integrations and test dependencies.

Development checkout
--------------------

Create a virtual environment with all dependencies, then use the
checkout launchers:

```
tools/setup-venv
tools/wirely compile test/compiler/simple.wrl
tools/run-tests
```

The launchers use `venv/bin/python` when available. Otherwise, they warn and
use the current Python interpreter. `tools/wirely` accepts the same arguments
as `wirely` and runs the source in this checkout from any working directory.
The test suite also requires `knxd` and `mosquitto`.

Pass test names to run a selection, and add `--timings` to list durations
slowest first:

```
tools/run-tests --timings unit/types wrl/equality
```

Add `--processes 4` to run the tests in four worker processes; see the
[test guide](doc/tests.md) for the options.

To check that compilation does not depend on declaration order, set
`WIRELY_COMPILER_SHUFFLE_SEED` to an integer. The compiler then shuffles
declarations, node parameters, procedure ports, `uses` bindings, and open
enum members with that seed; connections keep their relative order. Also set `WIRELY_COMPILER_SHUFFLE_LAYOUT=1` to shuffle
fields of structs and members of closed enums that are not native, which
changes the runtime representation.
Negative tests may report different diagnostics in this mode.

```
WIRELY_COMPILER_SHUFFLE_SEED=1 tools/run-tests
```

Test dependency and Python versions in temporary environments:

```
tools/run-tests-dependencies --versions min --python min
tools/run-tests-dependencies --versions max --python max
tools/run-tests-dependencies --versions all-min --python 3.11
```

`--versions` defaults to `min`: minimum direct dependencies, with latest
compatible transitive dependencies. `all-min` also minimizes transitive
dependencies; `max` selects the newest compatible dependency set.

`--python` accepts a uv version request or an executable name/path and
defaults to the interpreter running the script. `min` selects the latest
compatible patch of the oldest supported CPython minor; `max` selects the
newest compatible stable CPython available through the installed uv.
uv can download missing interpreters. `tools/run-tests --python PATH` runs
tests using a specified Python executable.

Test names, `--timings`, and `--keep-going` work as for `tools/run-tests`:

```
tools/run-tests-dependencies --versions min --python min misc/type_check
```
