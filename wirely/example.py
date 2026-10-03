import asyncio
import sys
import os

import wirely as blocks
from wirely import program_compiler
from wirely.util import check_parameters

# TODO: Where should these imports be done?
import wirely.compiler
import wirely.run

fn = os.path.dirname(__file__) + '/../examples/test.wrl'
global_block_type = blocks.compiler.compile_files([fn])
if global_block_type is None:
    sys.exit(1)

prog = program_compiler.compile(global_block_type)
if prog is None:
    sys.exit(1)

eng = blocks.Engine(prog)

# eng.debug = True

inp1_inst = eng.get_native_state('inp1')
inp2_inst = eng.get_native_state('inp2')

if True:
    inp1_inst.set_value(True)
    inp2_inst.set_value(True)
    eng.step()
    eng.step()

    inp2_inst.set_value(False)

asyncio.run(blocks.run.run(eng))
