import os
import tempfile
from unittest import TestCase

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from wirely.compiler.outline import Node
from wirely.editor.app import MainWindow


checks = TestCase()
application = QApplication.instance() or QApplication([])

source = '''node source: Std.DebugEventSource { Type: boolean }
node printer: Std.DebugEventPrinter { Type: boolean }
'''


def drag(view, start, end):
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, 6):
        position = start + (end - start) * step / 5
        QApplication.sendEvent(viewport, QMouseEvent(
            QMouseEvent.Type.MouseMove, position, viewport.mapToGlobal(position), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    application.processEvents()


with tempfile.TemporaryDirectory() as directory:
    filename = os.path.join(directory, 'main.wrl')
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(source)
    window = MainWindow([filename])
    window.resize(1200, 800)
    window.show()
    application.processEvents()
    tab = window.graph_tabs[0]
    scene = tab.graph_scene
    view = tab.view
    document = window.session.documents[filename]

    def at(position):
        return view.mapFromScene(position)

    # Dragging a node writes its position; one undo removes it again.
    printer = scene.items_by_id[Node(('printer',))]
    start = at(printer.mapToScene(20, 8))
    expected = f'position(x = {round(printer.pos().x() + 40)}, y = {round(printer.pos().y() + 60)})'
    drag(view, start, start + QPoint(40, 60))
    checks.assertIn(expected, document.text())
    window.tabs.setCurrentWidget(tab)
    window.undo()
    checks.assertEqual(document.text(), source)

    # Dragging from an output to an input connects them.
    source_socket = scene.items_by_id[Node(('source',))].sockets[('outputs', None)]
    printer_socket = scene.items_by_id[Node(('printer',))].sockets[('inputs', None)]
    drag(view, at(source_socket.scenePos()), at(printer_socket.scenePos()))
    checks.assertIn('connect source -> printer;', document.text())
    checks.assertEqual(len(scene.edge_graphics), 1)

    # The graph is read-only while the source does not parse.
    cursor = QTextCursor(document.text_document)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText('node')
    window.session.refresh()
    checks.assertEqual((tab.state, view.isInteractive(), tab.banner.isVisible()), ('frozen', False, True))
    document.text_document.undo()
    window.session.refresh()
    checks.assertEqual((tab.state, view.isInteractive()), ('live', True))

    window.save_all()
    with open(filename, encoding='utf-8') as file:
        checks.assertEqual(file.read(), document.text())
    window.close()
