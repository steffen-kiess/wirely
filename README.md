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
programs.

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

Install optional integrations as needed:

```
python3 -m pip install 'wirely[knx]'           # KNX
python3 -m pip install 'wirely[mqtt]'          # MQTT
python3 -m pip install 'wirely[two-n]'         # 2N devices
python3 -m pip install 'wirely[graphviz]'      # show-graph
python3 -m pip install 'wirely[gpio]'          # GPIO
python3 -m pip install 'wirely[all]'           # all optional integrations
```

The `test` extra adds dependencies used only by the test suite. Install
`wirely[all,test]` to include both integrations and test dependencies.

Development checkout
--------------------

Create a virtual environment with all dependencies, then use the
checkout launchers:

```
tools/setup-venv
tools/wirely compile test/simple.wrl
tools/run-tests
```

The launchers use `venv/bin/python` when available. Otherwise, they warn and
use the current Python interpreter. `tools/wirely` accepts the same arguments
as `wirely` and runs the source in this checkout from any working directory.
The test suite also requires `knxd` and `mosquitto`.
