"""Graphical editor command."""

import argparse
import importlib.util
import sys


def run(args: argparse.Namespace) -> int:
    if importlib.util.find_spec('PySide6') is None:
        print("Error: wirely edit requires the editor extra (pip install 'wirely[editor]')", file=sys.stderr)
        return 2
    from ..editor import app
    return app.run(args.input_filename)
