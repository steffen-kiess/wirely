import datetime

import wirely


async def run_test():
    inp = engine.get_native_state('in.create')
    feedback = engine.get_native_state('feedback.create')

    def tst(exp, cmd=None, fdbck=()):
        if exp is not None:
            if exp != 1:
                raise Exception('exp != 1')
            exp = wirely.Some(wirely.Unit())

        if cmd is not None:
            inp.send_event(wirely.Some(cmd))
        if fdbck is not ():
            feedback.set_value(fdbck)
        engine.step()
        engine.assert_value_is('trigger', exp)
        engine.step()
        engine.assert_value_is('trigger', None)
        engine.assert_is_idle()

    engine.step()
    engine.assert_value_is('trigger', None)
    engine.assert_is_idle()

    tst(cmd=True, exp=None)

    tst(fdbck=wirely.Some(False), exp=None)

    tst(cmd=True, exp=1)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.2))
    tst(cmd=True, exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.9))
    tst(cmd=True, exp=1)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
    tst(cmd=False, exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(2))
    tst(fdbck=wirely.Some(True), cmd=False, exp=1)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.8))
    tst(cmd=False, exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.5))
    tst(exp=1)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.8))
    # Still within timeout
    tst(cmd=False, exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.1))
    # Feedback lost during timeout
    tst(fdbck=None, exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3))
    tst(exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.3))
    tst(fdbck=wirely.Some(True), cmd=False, exp=1)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.1))
    # Within timeout
    tst(cmd=False, exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.1))
    # Feedback change should clear pending
    tst(fdbck=wirely.Some(False), exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(0.1))
    # Feedback change should clear pending, event if feedback is changed back
    tst(fdbck=wirely.Some(True), exp=None)

    engine.advance_fake_monotonic_time(wirely.Duration.from_seconds(3))
    # Pending should be clear
    tst(exp=None)
