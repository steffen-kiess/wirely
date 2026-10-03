import enum

import wirely
import wirely.compiler.parser
from wirely.compiler.types import parse_value


mode_type = engine.program.instances['off'].parameters['Type']._type
modes = mode_type.python_type
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
    'settings.mode.': modes.Heat,
    'delay': modes.Off,
}.items():
    engine.assert_value_is(name, member)
assert engine.program.instances['modes'].parameters['Value'] == (modes.Off, modes.Cool)
engine.step()
engine.assert_value_is('delay', modes.Heat)

for member in modes:
    declarations = wirely.compiler.parser.parse_str(
        f'parameter Mode: ThermostatSystemModeEnum = ThermostatSystemModeEnum.{member.name};',
        filename='<enum-test>',
    )
    assert parse_value(mode_type, declarations[0].default_value) is member

# TypeEnum resolves members of its own Python enum, not a thermostat-specific table.
class OtherMode(enum.Enum):
    Heat = 1


other_type = wirely.types.TypeEnum('OtherMode', OtherMode, underlying_type=wirely.types.uint8)
declarations = wirely.compiler.parser.parse_str(
    'parameter Mode: OtherMode = OtherMode.Heat;', filename='<enum-test>',
)
assert parse_value(other_type, declarations[0].default_value) is OtherMode.Heat
assert other_type.parse('Heat') is OtherMode.Heat


class SignedMode(enum.Enum):
    """Enum members exercising a signed underlying integer."""

    Negative = -2
    Positive = 300


signed_type = wirely.types.TypeEnum('SignedMode', SignedMode, underlying_type=wirely.types.int16)
assert (signed_type.size, signed_type.data_size, signed_type.alignment) == (2, 2, 2)
assert signed_type.serialize(SignedMode.Negative) == bytes.fromhex('fe ff')
assert signed_type.deserialize(bytes.fromhex('2c 01')) is SignedMode.Positive
try:
    wirely.types.TypeEnum('TooSmall', SignedMode, underlying_type=wirely.types.uint8)
except ValueError:
    pass
else:
    raise AssertionError('Enum members outside the underlying range were accepted')
