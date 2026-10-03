import enum

import wirely
import wirely.parser
from wirely.compiler_types import parse_value


mode_type = wirely.types.ThermostatSystemMode
modes = wirely.enum.ThermostatSystemMode
engine.step()
for name, member in {
    'off': modes.Off,
    'auto': modes.Auto,
    'cool': modes.Cool,
    'heat': modes.Heat,
    'emergency_heat': modes.EmergencyHeat,
    'precooling': modes.Precooling,
    'fan_only': modes.FanOnly,
    'dry': modes.Dry,
    'sleep': modes.Sleep,
    'present': wirely.Some(modes.Heat),
    'modes': (modes.Off, modes.Cool),
    'settings.mode.': modes.Heat,
    'delay': modes.Off,
}.items():
    engine.assert_value_is(name, member)
engine.step()
engine.assert_value_is('delay', modes.Heat)

for member in modes:
    declarations = wirely.parser.parse_str(
        f'parameter Mode : ThermostatSystemModeEnum = ThermostatSystemModeEnum.{member.name};',
        filename='<enum-test>',
    )
    assert parse_value(mode_type, declarations[0].default_value) is member

# TypeEnum resolves members of its own Python enum, not a thermostat-specific table.
class OtherMode(enum.Enum):
    Heat = 1


other_type = wirely.types.TypeEnum('OtherMode', OtherMode)
declarations = wirely.parser.parse_str(
    'parameter Mode : OtherMode = OtherMode.Heat;', filename='<enum-test>',
)
assert parse_value(other_type, declarations[0].default_value) is OtherMode.Heat
assert other_type.parse('Heat') is OtherMode.Heat
