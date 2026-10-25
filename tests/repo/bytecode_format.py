import difflib
import re
from pathlib import Path


# The Wirely blocks of the binary format specification, joined by blank lines, are the declarations of
# wirely/program/format.wrl after its leading file comment.
root = Path(__file__).resolve().parents[2]
document = (root / 'doc/bytecode-format.md').read_text(encoding='utf-8')
expected = '\n'.join(re.findall(r'^```wirely\n(.*?)^```$', document, re.MULTILINE | re.DOTALL))
comment, _, declarations = (root / 'wirely/program/format.wrl').read_text(encoding='utf-8').partition('\n\n')
assert all(line.startswith('//') for line in comment.splitlines()), 'format.wrl must start with a file comment'
diff = ''.join(difflib.unified_diff(
    expected.splitlines(keepends=True), declarations.splitlines(keepends=True),
    fromfile='doc/bytecode-format.md', tofile='wirely/program/format.wrl'))
assert not diff, f'The Wirely blocks of doc/bytecode-format.md and wirely/program/format.wrl differ:\n{diff}'


async def run_test():
    pass
