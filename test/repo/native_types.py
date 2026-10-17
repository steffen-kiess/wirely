import importlib.machinery
import importlib.util
from pathlib import Path


# The generated Python files match the libraries, without obsolete contract modules. The
# script is loaded as a module so that it uses the interpreter running the tests.
root = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('generate_native_types', str(root / 'tools/generate-native-types'))
generator = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(generator)
files = generator.generated_files()
outdated = [str(path.relative_to(root)) for path, content in files.items()
            if not path.exists() or path.read_text(encoding='utf-8') != content]
outdated += [str(path.relative_to(root)) for path in generator.obsolete_files(files)]
assert not outdated, f'Out of date, run tools/generate-native-types: {outdated}'


async def run_test():
    pass
