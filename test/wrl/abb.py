import wirely

inp_count = 2
inp = [engine.get_native_state('inp{}'.format(i)) for i in range(inp_count)]


def set(*args):
    if len(args) != inp_count:
        raise Exception('len(args) != inp_count')
    for i, value in enumerate(args):
        inp[i].set_value(value)


flags = [
    'ManualOperation',
    'BlockActive',
    'ForcedOperation',
    'FrostAlarm',
    'RainAlarm',
    'WindAlarm',
    'AutomaticSunProtection',
    'AutomaticHeatingCooling',
    'DriveError',
    'DriveInMotion',
    'DriveDown',
    'DriveUp',
    'SendReceiveDelayActive',
]

values = [
    None, 0x0000, 0xffff,
    *(1 << bit for bit in range(16)),
    0xdda4, 0xa72c, 0xf1bf, 0x5c03, 0x9de5, 0xb34f, 0xa1d0,
    0xe785, 0x0c7b, 0xa911, 0x7be5, 0x247e, 0xb5f3, 0xaeec,
    0x7e9b, 0xd698, 0x31b0, 0x9e0f, 0x712f, 0x319c, None,
]
for val in values:
    set(val if val is not None else 0, wirely.Some(val) if val is not None else None)
    engine.step()
    for i, flag in enumerate(flags):
        expected = val is not None and (val & (1 << i)) != 0
        engine.assert_value_is('val0.' + flag, expected)
        if val is not None:
            engine.assert_value_is('val1.' + flag, wirely.Some(expected))
        else:
            engine.assert_value_is('val1.' + flag, None)
    engine.assert_is_idle()


async def run_test():
    pass
