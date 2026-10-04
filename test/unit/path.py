import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import compile_strs, _standard_library_sources, model as blocks
from wirely.compiler import program as program_compiler
from wirely.compiler.native_functions import native_function_impls
from wirely.compiler.parser import parse_str


checks = TestCase()
engine.step()
for name, expected in {
    'Joined': os.path.join('base', 'child'),
    'Relative': os.path.join('base', '../child'),
    'EmptyBase': 'child',
    'EmptyPath': os.path.join('base', ''),
    'BothEmpty': '',
    'BareParent': '.',
    'RelativeParent': str(Path('base/../child').parent),
    'EmptyParent': '.',
    'TrailingParent': str(Path('base/child/').parent),
    'MissingParent': str(Path('nonexistent/directory/file').parent),
    'Source': os.path.abspath('test/unit/path.wrl'),
}.items():
    assert engine.program.instances['paths'].parameters[name] == expected, name


def graph(*sources):
    result = compile_strs(_standard_library_sources(['path']) + list(sources))
    assert result is not None
    return result


def evaluate(compiled):
    return program_compiler.compile_nocatch(compiled)


def value(program, name):
    node, parameter = name.rsplit('.', 1)
    return program.instances[node].parameters[parameter]


root = Path.cwd().anchor
# Inject host paths as parameters, avoiding platform-specific string escaping.
with patch.dict(native_function_impls, {
    ('Root',): lambda *, context: root,
    ('Absolute',): lambda *, context: str(Path.cwd() / 'missing' / 'child'),
}):
    result = evaluate(graph(('<paths>', '''
native function Root(): String;
native function Absolute(): String;
node type Capture {
  parameter RootParent: String = Path.Parent(Root());
  parameter AbsoluteJoin: String = Path.Join("relative", Absolute());
  parameter AbsoluteParent: String = Path.Parent(Absolute());
}
node capture: Capture {}
''')))
    assert value(result, 'capture.RootParent') == root
    assert value(result, 'capture.AbsoluteJoin') == str(Path.cwd() / 'missing' / 'child')
    assert value(result, 'capture.AbsoluteParent') == str(Path.cwd() / 'missing')


compiled = graph(
    ('relative/../library/types.wrl', '''
constant LibraryLocation: String = Path.SourceFile();
namespace Library {
  node type Defaults {
    parameter Location: String = Path.SourceFile();
    node type Capture { parameter File: String; }
    node capture: Capture { File: Location }
  }
}
node type Forward {
  parameter Location: String;
  node inner: Library.Defaults { Location: Location }
}
'''),
    (str(Path.cwd() / 'absolute/main.wrl'), '''
node type Capture {
  parameter File: String = Path.SourceFile();
  parameter LibraryFile: String;
}
node capture: Capture { LibraryFile: LibraryLocation }
node defaults: Library.Defaults {}
node forward: Forward { Location: Path.SourceFile() }
'''),
)
expected_library = os.path.abspath('library/types.wrl')
expected_main = os.path.abspath('absolute/main.wrl')
previous_directory = os.getcwd()
with tempfile.TemporaryDirectory() as directory:
    try:
        os.chdir(directory)
        result = evaluate(compiled)
    finally:
        os.chdir(previous_directory)
assert value(result, 'capture.File') == expected_main
assert value(result, 'capture.LibraryFile') == expected_library
assert value(result, 'defaults.capture.File') == expected_library
assert value(result, 'forward.inner.capture.File') == expected_main

# Source positions retain their captured path when reconstructed or reused.
position = parse_str('constant File: String = Path.SourceFile();',
                     filename='relative/../source.wrl')[0].source_position
with tempfile.TemporaryDirectory() as directory:
    try:
        os.chdir(directory)
        copied = blocks.SourcePosition(
            filename=position.filename, meta=position.meta,
            input_data=position.input_data, source_path=position.source_path,
        )
    finally:
        os.chdir(previous_directory)
assert copied.source_path == os.path.abspath('source.wrl')
assert copied.filename == 'relative/../source.wrl'

with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / 'target'
    target.mkdir()
    link = Path(directory) / 'link'
    link.symlink_to(target, target_is_directory=True)
    filename = link / 'missing.wrl'
    result = evaluate(graph((str(filename), '''
node type Capture { parameter File: String = Path.SourceFile(); }
node capture: Capture {}
''')))
    assert value(result, 'capture.File') == str(filename)

for filename in (None, '', '<test>'):
    compiled = graph((filename, 'constant File: String = Path.SourceFile();'))
    with checks.assertRaises(blocks.ProgramError) as caught:
        program_compiler.compile_nocatch(compiled)
    error = caught.exception
    assert 'filesystem source location' in str(error)
    assert error.source_element.source_position.filename == filename
    assert error.source_element.source_position.start_line == 1
    assert isinstance(error.source_element, blocks.FunctionCall)

# Skipped calls do not load or execute the path implementation.
with patch.dict(native_function_impls, {('IsEmpty',): lambda *, context: False}):
    compiled = graph(('<test>', '''
native function IsEmpty(path: String): boolean;
constant File: String = if true { "skipped" } else { Path.SourceFile() };
assert true || IsEmpty(Path.SourceFile());
assert !(false && IsEmpty(Path.SourceFile()));
node type Unused { constant File: String = Path.SourceFile(); }
'''))
with patch('wirely.compiler.native_functions.importlib.import_module') as importer:
    program_compiler.compile_nocatch(compiled)
    importer.assert_not_called()
