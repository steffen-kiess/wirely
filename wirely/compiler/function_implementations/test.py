"""Compile-time functions selecting test resources when supplied."""

from typing import Protocol


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


def replace_knx_path(*, context):
    if TEST_RESOURCES_SERVICE not in context.services:
        return context.arguments['default']
    return context.services[TEST_RESOURCES_SERVICE].get_knx_path()


def replace_mqtt_uri(*, context):
    if TEST_RESOURCES_SERVICE not in context.services:
        return context.arguments['default']
    return context.services[TEST_RESOURCES_SERVICE].get_mqtt_uri()


def replace_output_path(*, context):
    default = context.arguments['default']
    if TEST_RESOURCES_SERVICE not in context.services:
        return default
    return context.services[TEST_RESOURCES_SERVICE].get_output_path(default)
