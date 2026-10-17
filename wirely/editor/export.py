"""Export of diagrams as PDF or PNG images, as the editor draws them.

The output does not depend on UI settings: it uses a fixed light palette, the
fixed diagram font, and no grid.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from PySide6.QtCore import QMarginsF, QRectF, QSizeF
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPalette, QPdfWriter
from PySide6.QtWidgets import QApplication

from wirely.compiler.editing import Sources

from . import graph_model
from .graph_view import GraphScene
from .program import Program

# Space around the items, in scene units.
MARGIN = 18
FORMATS = ('.pdf', '.png')

_COLORS = {
    QPalette.ColorRole.Base: '#ffffff',
    QPalette.ColorRole.Text: '#000000',
    QPalette.ColorRole.PlaceholderText: '#7f7f7f',
    QPalette.ColorRole.Highlight: '#308cc6',
}


class ExportError(Exception):
    pass


def export_image(sources: Sources, scope: Sequence[str], output: str, *, scale: float = 4) -> None:
    """Write the diagram of a scope to output, a .pdf or .png file.

    sources are (filename, text) pairs; scale is the PNG pixels per scene unit.
    """
    extension = os.path.splitext(output)[1].lower()
    if extension not in FORMATS:
        raise ExportError(f'Unsupported image format {extension or output!r}; use .pdf or .png')
    program = Program(sources)
    if program.parse_errors:
        filename, error = next(iter(program.parse_errors.items()))
        raise ExportError(f'{filename}:{error.line}: syntax error')
    model = graph_model.build(program, tuple(scope))
    if model is None:
        raise ExportError(f'Scope {".".join(scope) or "Program"} does not exist or is ambiguous')

    QApplication.instance() or QApplication([])
    scene = GraphScene()
    palette = QPalette()
    for role, color in _COLORS.items():
        palette.setColor(role, QColor(color))
    scene.setPalette(palette)
    scene.setBackgroundBrush(QColor('#ffffff'))
    scene.show_grid = False
    scene.set_model(model)
    bounds = scene.itemsBoundingRect().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)

    if extension == '.pdf':
        writer = QPdfWriter(output)
        writer.setResolution(72)
        writer.setPageLayout(QPageLayout(QPageSize(QSizeF(bounds.width(), bounds.height()), QPageSize.Unit.Point),
                                         QPageLayout.Orientation.Portrait, QMarginsF()))
        painter = QPainter(writer)
        scene.render(painter, QRectF(0, 0, bounds.width(), bounds.height()), bounds)
        painter.end()
    else:
        image = QImage(round(bounds.width() * scale), round(bounds.height() * scale), QImage.Format.Format_RGB32)
        image.fill(QColor('#ffffff'))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        scene.render(painter, QRectF(image.rect()), bounds)
        painter.end()
        if not image.save(output, 'PNG'):  # type: ignore[call-overload]
            raise ExportError(f'Cannot write {output}')
