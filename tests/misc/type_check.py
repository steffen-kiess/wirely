import subprocess
import sys
from pathlib import Path


async def run_test():
    # mypy reads its settings from pyproject.toml in the repository root.
    repository_root = Path(__file__).resolve().parent.parent.parent
    subprocess.run([sys.executable, '-m', 'mypy'], cwd=repository_root, check=True)
