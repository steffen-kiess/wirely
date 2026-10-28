"""Load-time checks of the KNX library."""

from unittest import TestCase

import wirely.compiler.compilation
from wirely.compiler import program as program_compiler
from wirely.compiler.sources import standard_library_sources
from wirely.runtime.loading import LoadedProgram
from wirely.runtime.procedures import LoadError


checks = TestCase()
libraries = standard_library_sources(['std', 'knx'])


def load(source):
    graph = wirely.compiler.compilation.compile_strs([*libraries, ('<knx-loading>', source)])
    assert graph is not None
    return LoadedProgram.load(program_compiler.compile_nocatch(graph))


connection = 'node bus: KNX.Connection { UnixPath: "/run/knxnet" }'


def node(name, node_type, address):
    source = ('DebugValueSource { Type: Option[boolean], Initial: None }' if node_type == 'ValueOptionProvider'
              else 'DebugEventSource { Type: boolean }')
    return f'''
        node {name}_input: Std.{source}
        node {name}: KNX.{node_type} {{ GroupAddress: "{address}", Type: boolean }}
        connect {name}_input -> {name};
        connect bus -> {name}.Bus;
    '''


def check_providers():
    with checks.assertRaisesRegex(LoadError, 'already has a ValueOptionProvider for group address 1/2/3'):
        load(connection + node('a', 'ValueOptionProvider', '1/2/3')
             + node('b', 'ValueOptionProvider', '1/2/3'))
    load(connection + node('a', 'ValueOptionProvider', '1/2/3')
         + node('b', 'EventSender', '1/2/3') + node('c', 'EventSender', '1/2/3'))


async def run_test():
    check_providers()
