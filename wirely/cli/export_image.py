"""Diagram image export command."""

import argparse
import importlib.util
import os
import sys


def run(args: argparse.Namespace) -> int:
    if importlib.util.find_spec('PySide6') is None:
        print("Error: wirely export-image requires the editor extra (pip install 'wirely[editor]')",
              file=sys.stderr)
        return 2
    # Render without a display or platform theme.
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication

    from ..editor import export
    from ..editor.graph_view import FONT_FAMILY, font_available

    sources = []
    for filename in args.input_filename:
        try:
            with open(filename, encoding='utf-8') as file:
                sources.append((filename, file.read()))
        except OSError as error:
            print(f'Error: {error}', file=sys.stderr)
            return 1
    QApplication.instance() or QApplication(sys.argv[:1])
    if not font_available():
        print(f'Warning: font {FONT_FAMILY} is not installed; the image uses a fallback font', file=sys.stderr)
    scope = tuple(args.scope.split('.')) if args.scope else ()
    try:
        export.export_image(sources, scope, args.output, scale=args.scale)
    except export.ExportError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0
