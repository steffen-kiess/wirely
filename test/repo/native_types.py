import subprocess
import sys
from pathlib import Path


# The generated Python enums and structs match the standard library.
root = Path(__file__).resolve().parents[2]
result = subprocess.run([sys.executable, str(root / 'tools/generate-native-types'), '--check'],
                        cwd=root, capture_output=True, text=True)
assert result.returncode == 0, result.stdout + result.stderr


async def run_test():
    pass
