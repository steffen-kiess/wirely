import os
import re
import tempfile
from unittest import TestCase

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from wirely.editor.export import ExportError, export_image


checks = TestCase()
application = QApplication.instance() or QApplication([])

sources = [('main.wrl', '''node type Forward {
  default input: value boolean;
  default output: value boolean;
  connect default input -> default output;
}
node source: Std.Constant { Type: boolean, Value: true }
node forward: Forward {}
connect source -> forward;
''')]

with tempfile.TemporaryDirectory() as directory:
    pdf = os.path.join(directory, 'graph.pdf')
    png = os.path.join(directory, 'graph.png')

    # The PDF page and the PNG cover the same area, the PNG at its scale.
    export_image(sources, (), pdf)
    with open(pdf, 'rb') as file:
        content = file.read()
    checks.assertTrue(content.startswith(b'%PDF'))
    width, height = map(float, re.search(rb'/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]', content).groups())
    export_image(sources, (), png, scale=3)
    image = QImage(png)
    checks.assertEqual((image.width(), image.height()), (round(width * 3), round(height * 3)))
    # The background is white, without grid dots.
    checks.assertEqual({image.pixelColor(x, y).name() for x in range(40) for y in range(40)},
                       {QColor('white').name()})

    export_image(sources, ('Forward',), png)
    checks.assertFalse(QImage(png).isNull())

    for scope, output, sources_ in (
            (('Missing',), png, sources),
            ((), os.path.join(directory, 'graph.svg'), sources),
            ((), png, [('main.wrl', 'node broken')])):
        with checks.assertRaises(ExportError):
            export_image(sources_, scope, output)


async def run_test():
    pass
