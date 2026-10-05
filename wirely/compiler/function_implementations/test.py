"""Compile-time functions selecting test resources when supplied."""

from typing import Protocol


TEST_RESOURCES_SERVICE = 'wirely.test_resources'


class TestResources(Protocol):
    """Resources shared by one test compilation and execution."""

    def get_knx_path(self) -> str:
        """Request a KNX fixture and return its stable absolute socket path."""
        ...


def replace_knx_path(*, context):
    if TEST_RESOURCES_SERVICE not in context.services:
        return context.arguments['default']
    return context.services[TEST_RESOURCES_SERVICE].get_knx_path()
