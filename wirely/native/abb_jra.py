import enum

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


class JRAStatus(enum.IntFlag):
    # See JRAS_xxx1_PH_EN_V1-4_2CDC506051D0204.pdf, page 106

    # Low byte
    ManualOperation = (1 << 0)
    BlockActive = (1 << 1)
    ForcedOperation = (1 << 2)
    FrostAlarm = (1 << 3)
    RainAlarm = (1 << 4)
    WindAlarm = (1 << 5)
    AutomaticSunProtection = (1 << 6)
    AutomaticHeatingCooling = (1 << 7)

    # High byte
    DriveError = (1 << 8)
    DriveInMotion = (1 << 9)
    DriveDown = (1 << 10)
    DriveUp = (1 << 11)
    SendReceiveDelayActive = (1 << 12)
    # Unassigned13 = (1 << 13)
    # Unassigned14 = (1 << 14)
    # Unassigned15 = (1 << 15)

@native_block_type(native_block_types, has_state=False)
class InterpretStatusInformation:
    @native_procedure
    @staticmethod
    def run(*, context):
        val = context.inputs['input']
        val = JRAStatus(val)

        for flag in JRAStatus:
            name = flag.name
            res = bool(val & flag)
            context.set_result(name, res)

@native_block_type(native_block_types, has_state=False)
class InterpretStatusInformationOption:
    @native_procedure
    @staticmethod
    def run(*, context):
        val = context.inputs['input']
        if val is not None:
            val = JRAStatus(val.value)

        for flag in JRAStatus:
            name = flag.name
            if val is None:
                res = None
            else:
                res = blocks.Some(bool(val & flag))
            context.set_result(name, res)
