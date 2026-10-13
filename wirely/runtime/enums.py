"""Python enums for the library enum types that native code uses.

Member numbers match the declarations in wirely/lib/.
"""

import enum


class GPIOActiveState(enum.IntEnum):
    High = 1
    Low = 2


class GPIODrive(enum.IntEnum):
    PushPull = 1
    OpenDrain = 2
    OpenSource = 3


class GPIOBias(enum.IntEnum):
    AsIs = 1
    Disable = 2
    PullDown = 3
    PullUp = 4


class KNXProtocol(enum.IntEnum):
    TCP = 1
    UDP = 2
    Unix = 3
    Auto = 4


class MQTTFormat(enum.IntEnum):
    Default = 1
    SnakeCase = 2


class StdEdgeType(enum.IntEnum):
    Rising = 1
    Falling = 2
    Both = 3


# Python enums by enum type name.
native_enum_types = {
    'GPIO.ActiveState': GPIOActiveState,
    'GPIO.Drive': GPIODrive,
    'GPIO.Bias': GPIOBias,
    'KNX.Protocol': KNXProtocol,
    'MQTT.Format': MQTTFormat,
    'Std.EdgeType': StdEdgeType,
}
