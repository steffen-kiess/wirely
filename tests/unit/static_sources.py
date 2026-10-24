import unittest


checks = unittest.TestCase()


def check_static_sources():
    program = engine.program
    run_count, _, _ = engine.step()
    assert run_count == 4, run_count
    engine.assert_value_is('limit_twice', 24)
    engine.assert_value_is('forward_twice', 24)
    engine.assert_value_is('forward', 12)
    engine.assert_value_is('other', 5)
    engine.assert_value_is('sum', 10)
    engine.assert_value_is('all', True)
    # Static sources never change, so later steps run nothing.
    engine.assert_is_idle()

    # Equal values share one constant.
    limit = program.symbols.variable_aliases['forward'].constant
    assert [constant.value for constant in program.constants].count(12) == 1
    procedure = next(proc for schedule in program.procs.values() for proc in schedule
                     if proc.node == 'limit_twice')
    source, = procedure.inputs
    assert source.constant is limit
    with checks.assertRaises(ValueError):
        engine.get_variable('forward')


check_static_sources()


async def run_test():
    pass
