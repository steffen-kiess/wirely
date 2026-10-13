"""Python types for the library struct types that native code uses.

Field orders match the declarations in wirely/lib/.
"""

from typing import NamedTuple


class MQTTTemplateParts(NamedTuple):
    prefix: str
    suffix: str


class TwoNCredentials(NamedTuple):
    username: str
    password: str


# Python types by struct type name.
native_struct_types = {
    'MQTT.TemplateParts': MQTTTemplateParts,
    'TwoN.Credentials': TwoNCredentials,
}
