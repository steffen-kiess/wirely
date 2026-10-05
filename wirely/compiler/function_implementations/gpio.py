"""Compiler-only configuration predicates."""


def is_valid_active_state(*, context):
    return context.arguments['value'] in ('High', 'Low')


def is_valid_drive(*, context):
    return context.arguments['value'] in ('PushPull', 'OpenDrain', 'OpenSource')


def is_valid_bias(*, context):
    return context.arguments['value'] in ('AsIs', 'Disable', 'PullDown', 'PullUp')
