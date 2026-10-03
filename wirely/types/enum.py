import enum


# TODO: Move enums somewhere else?
# See Matter Application Cluster Specification
class ThermostatSystemMode(enum.Enum):
    Off = 0
    Auto = 1
    Cool = 3
    Heat = 4
    EmergencyHeat = 5
    Precooling = 6
    FanOnly = 7
    Dry = 8
    Sleep = 9
