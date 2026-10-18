from unittest import TestCase

import lark

from wirely.compiler.parser import grammar, opts


async def run_test():
    with TestCase().assertNoLogs(lark.logger, level='WARNING'):
        lark.Lark(grammar, **dict(opts, debug=True))
