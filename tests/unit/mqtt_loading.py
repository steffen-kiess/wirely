"""Load-time checks of the MQTT library."""

from unittest import TestCase

import wirely.compiler.compilation
from wirely.compiler import program as program_compiler
from wirely.compiler.sources import standard_library_sources
from wirely.mqtt import settings
from wirely.runtime.loading import LoadedProgram
from wirely.runtime.procedures import LoadError
from wirely.types.time import TICKS_PER_SECOND, Duration


checks = TestCase()
libraries = standard_library_sources(['std', 'mqtt'])


def load(source):
    graph = wirely.compiler.compilation.compile_strs([*libraries, ('<mqtt-loading>', source)])
    assert graph is not None
    return LoadedProgram.load(program_compiler.compile_nocatch(graph))


def provider(name, last_will):
    return f'''
        node {name}_input: Std.DebugValueSource {{ Type: Option[boolean], Initial: None }}
        node {name}: MQTT.ValueOptionProvider {{ Topic: "{name}", Type: boolean, LastWill: {last_will} }}
        connect {name}_input -> {name};
        connect server -> {name}.Broker;
    '''


def check_last_will():
    with checks.assertRaisesRegex(LoadError, 'requires a ClientID'):
        load('node server: MQTT.Connection { URI: "mqtt://localhost" }' + provider('a', 'None'))
    with checks.assertRaisesRegex(LoadError, 'already has a LastWill'):
        load('node server: MQTT.Connection { URI: "mqtt://localhost", ClientID: "test" }'
             + provider('a', 'None') + provider('b', 'Some(false)'))
    load('node server: MQTT.Connection { URI: "mqtt://localhost", ClientID: "test" }' + provider('a', 'None'))


def check_settings():
    # The compiler rejects these values, so compiled programs cannot pass them to the native initializer.
    for uri in ['localhost', 'http://localhost', 'mqtt://localhost/path', 'mqtt+unix:']:
        with checks.assertRaises(ValueError):
            settings.parse_uri(uri)
    with checks.assertRaisesRegex(ValueError, 'must not be zero'):
        settings.keep_alive_seconds(Duration(ticks=0))
    with checks.assertRaisesRegex(ValueError, 'longer than 65535 seconds'):
        settings.keep_alive_seconds(Duration(ticks=65535 * TICKS_PER_SECOND + 1))
    assert settings.keep_alive_seconds(Duration(ticks=1)) == 1
    assert settings.keep_alive_seconds(Duration(ticks=65535 * TICKS_PER_SECOND)) == 65535
    with checks.assertRaisesRegex(ValueError, 'must not be empty'):
        settings.check_client_id('')
    for topic in ['', 'a/+', '#', 'a\0b', 'x' * 65536]:
        with checks.assertRaises(ValueError):
            settings.check_topic(topic)
    settings.check_topic('x' * 65535)


async def run_test():
    check_last_will()
    check_settings()
