"""Compile-time functions selecting test resources when supplied."""

from typing import cast, TYPE_CHECKING, Protocol

from ..native_functions import NativeFunctionContext


if TYPE_CHECKING:
    from wirely.two_n.fake_server import ServerInfo


TEST_RESOURCES_SERVICE = 'wirely.test_resources'


class TestResources(Protocol):
    """Resources shared by one test compilation and execution."""

    def get_knx_path(self) -> str:
        """Request a KNX fixture and return its stable absolute socket path."""
        ...

    def get_mqtt_uri(self) -> str:
        """Request an MQTT fixture and return its stable broker URI."""
        ...

    def get_output_path(self, default: str) -> str:
        """Return a path for the default's basename in the test directory."""
        ...

    def get_twon_info(self) -> 'ServerInfo':
        """Request a TwoN fixture and return its cached server information."""
        ...


def replace_knx_path(*, context: NativeFunctionContext) -> str:
    default, = context.arguments
    if TEST_RESOURCES_SERVICE not in context.services:
        return default
    return cast(TestResources, context.services[TEST_RESOURCES_SERVICE]).get_knx_path()


def replace_mqtt_uri(*, context: NativeFunctionContext) -> str:
    default, = context.arguments
    if TEST_RESOURCES_SERVICE not in context.services:
        return default
    return cast(TestResources, context.services[TEST_RESOURCES_SERVICE]).get_mqtt_uri()


def replace_output_path(*, context: NativeFunctionContext) -> str:
    default, = context.arguments
    if TEST_RESOURCES_SERVICE not in context.services:
        return default
    return cast(TestResources, context.services[TEST_RESOURCES_SERVICE]).get_output_path(default)


def replace_twon_host(*, context: NativeFunctionContext) -> str:
    default, = context.arguments
    if TEST_RESOURCES_SERVICE not in context.services:
        return default
    return cast(TestResources, context.services[TEST_RESOURCES_SERVICE]).get_twon_info().host


def replace_twon_port(*, context: NativeFunctionContext) -> int:
    default, = context.arguments
    if TEST_RESOURCES_SERVICE not in context.services:
        return default
    return cast(TestResources, context.services[TEST_RESOURCES_SERVICE]).get_twon_info().port


def replace_twon_tlsa(*, context: NativeFunctionContext) -> tuple[str]:
    default, = context.arguments
    if TEST_RESOURCES_SERVICE not in context.services:
        return default
    return (cast(TestResources, context.services[TEST_RESOURCES_SERVICE]).get_twon_info().server.tlsa,)
