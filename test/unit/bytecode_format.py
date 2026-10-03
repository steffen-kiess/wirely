from pathlib import Path

from wirely.compiler import compile_strs
from wirely.compiler import program as program_compiler


document = Path(__file__).resolve().parents[2] / 'doc' / 'bytecode-format.md'
source = []
in_code = False
block_count = 0
for line in document.read_text().splitlines(keepends=True):
    marker = line.strip()
    if marker == '```wirely':
        assert not in_code, 'Nested Wirely code fence'
        in_code = True
        block_count += 1
        source.append('\n')
    elif in_code and marker == '```':
        in_code = False
        source.append('\n')
    else:
        # Keep diagnostics at their original Markdown line numbers.
        source.append(line if in_code else '\n')
assert not in_code, 'Unclosed Wirely code fence'
assert block_count, 'No Wirely definitions found'

compiled = compile_strs([(str(document), ''.join(source))])
assert compiled is not None
assert program_compiler.compile(compiled) is not None
sizes = {
    compiled.struct_types[name].value_type.size
    for name in ('SimpleType', 'EnumType', 'CompositeType')
}
assert sizes == {16}, f'Type records must all occupy 16 bytes, got {sizes}'
