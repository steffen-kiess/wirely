"""Graphical editor command."""

import importlib.util
import sys


def run(args):
    if importlib.util.find_spec('PySide6') is None:
        print("Error: wirely edit requires the editor extra (pip install 'wirely[editor]')", file=sys.stderr)
        return 2
    from ..editor import app
    return app.run(args.input_filename)
