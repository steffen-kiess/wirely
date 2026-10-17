"""Compile-time functions selecting test resources when supplied."""

from typing import cast, TYPE_CHECKING, Protocol

from wirely import UInt16
from wirely.compiler.contracts import test as contracts

from ..native_functions import FunctionServices, RegisteredConstantFunction, constant_function


if TYPE_CHECKING:
    from wirely.two_n.fake_server import ServerInfo


TEST_RESOURCES_SERVICE = 'wirely.test_resources'

native_functions: dict[str, RegisteredConstantFunction] = {}


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


@constant_function(native_functions, contracts.ReplaceKNXPath)
def replace_knx_path(default: str, *, services: FunctionServices) -> str:
    if TEST_RESOURCES_SERVICE not in services.services:
        return default
    return cast(TestResources, services.services[TEST_RESOURCES_SERVICE]).get_knx_path()


@constant_function(native_functions, contracts.ReplaceMQTTURI)
def replace_mqtt_uri(default: str, *, services: FunctionServices) -> str:
    if TEST_RESOURCES_SERVICE not in services.services:
        return default
    return cast(TestResources, services.services[TEST_RESOURCES_SERVICE]).get_mqtt_uri()


@constant_function(native_functions, contracts.ReplaceOutputPath)
def replace_output_path(default: str, *, services: FunctionServices) -> str:
    if TEST_RESOURCES_SERVICE not in services.services:
        return default
    return cast(TestResources, services.services[TEST_RESOURCES_SERVICE]).get_output_path(default)


@constant_function(native_functions, contracts.ReplaceTwoNHost)
def replace_twon_host(default: str, *, services: FunctionServices) -> str:
    if TEST_RESOURCES_SERVICE not in services.services:
        return default
    return cast(TestResources, services.services[TEST_RESOURCES_SERVICE]).get_twon_info().host


@constant_function(native_functions, contracts.ReplaceTwoNPort)
def replace_twon_port(default: UInt16, *, services: FunctionServices) -> UInt16:
    if TEST_RESOURCES_SERVICE not in services.services:
        return default
    return UInt16(cast(TestResources, services.services[TEST_RESOURCES_SERVICE]).get_twon_info().port)


@constant_function(native_functions, contracts.ReplaceTwoNTLSA)
def replace_twon_tlsa(default: tuple[str, ...], *, services: FunctionServices) -> tuple[str, ...]:
    if TEST_RESOURCES_SERVICE not in services.services:
        return default
    return (cast(TestResources, services.services[TEST_RESOURCES_SERVICE]).get_twon_info().server.tlsa,)
