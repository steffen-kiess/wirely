import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QTextCursor, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from wirely.compiler.outline import Node, State
from wirely.editor.app import MainWindow
from wirely.editor.grid import STEP, from_pixels


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


def wheel(view, steps):
    viewport = view.viewport()
    position = QPointF(viewport.rect().center())
    QApplication.sendEvent(viewport, QWheelEvent(
        position, viewport.mapToGlobal(position), QPoint(), QPoint(0, 120 * steps), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))


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

    # The wheel zooms; zooming in and out again restores the scale.
    scale = view.transform().m11()
    wheel(view, 1)
    checks.assertGreater(view.transform().m11(), scale)
    wheel(view, -1)
    checks.assertAlmostEqual(view.transform().m11(), scale)

    # Dragging a node writes its position; one undo removes it again.
    printer = scene.items_by_id[Node(('printer',))]
    start = at(printer.mapToScene(20, 8))
    x, y = from_pixels(printer.pos().x() + 40, printer.pos().y() + 60)
    expected = f'position(x = {x}, y = {y})'
    drag(view, start, start + QPoint(40, 60))
    checks.assertIn(expected, document.text())
    window.tabs.setCurrentWidget(tab)
    window.undo()
    checks.assertEqual(document.text(), source)

    # Geometry and ports share the grid; moving a selection at different zoom
    # levels preserves its relative placement and is one undoable edit.
    for graphic in scene.items_by_id.values():
        for value in (graphic.width, graphic.height, graphic.header):
            checks.assertEqual(value % STEP, 0)
        for socket in graphic.sockets.values():
            checks.assertEqual(socket.scenePos().x() % STEP, 0)
            checks.assertEqual(socket.scenePos().y() % STEP, 0)
    for zoom, delta in ((0.6, QPoint(-37, 53)), (1.8, QPoint(-37, 53)), (2, QPoint(54, 0))):
        view.resetTransform()
        view.scale(zoom, zoom)
        view.centerOn(scene.itemsBoundingRect().center())
        scene.clearSelection()
        if zoom == 2:
            # Snap the group's displacement even for odd and even origins.
            scene.items_by_id[Node(('source',))].moveBy(STEP, 0)
        before = {item_id: graphic.pos() for item_id, graphic in scene.items_by_id.items()}
        for graphic in scene.items_by_id.values():
            graphic.setSelected(True)
        printer = scene.items_by_id[Node(('printer',))]
        start = at(printer.mapToScene(20, 8))
        scene_delta = view.mapToScene(start + delta) - view.mapToScene(start)
        dx, dy = from_pixels(scene_delta.x(), scene_delta.y())
        drag(view, start, start + delta)
        for item_id, position in before.items():
            graphic = scene.items_by_id[item_id]
            checks.assertEqual(graphic.pos(), position + QPointF(dx * STEP, dy * STEP))
            x, y = from_pixels(graphic.pos().x(), graphic.pos().y())
            checks.assertIn(f'position(x = {x}, y = {y})', document.text())
        window.undo()
        checks.assertEqual(document.text(), source)
    view.resetTransform()
    view.centerOn(scene.itemsBoundingRect().center())
    scene.clearSelection()

    # Both creation paths convert pixel positions to snapped grid coordinates.
    position = QPointF(-2.7 * STEP, 4.3 * STEP)
    with patch('wirely.editor.app.NodePalette') as palette:
        palette.return_value.exec.return_value = True
        palette.return_value.selected_type.return_value = 'Std.DebugEventPrinter'
        window.add_node(tab, position)
    checks.assertIn('position(x = -3.0, y = 4.0)', document.text())
    window.undo()
    with patch('wirely.editor.app.DeclarationDialog') as dialog:
        dialog.return_value.exec.return_value = True
        dialog.return_value.declaration.return_value = 'state extra: boolean = false;'
        window.add_declaration(tab, 'state', position)
    checks.assertIn('position(x = -3.0, y = 4.0)', document.text())
    state = scene.items_by_id[State(('extra',))]
    old = state.sockets[('outputs', 'old')].scenePos()
    new = state.sockets[('outputs', 'new')].scenePos()
    checks.assertEqual(new - old, QPointF(0, STEP))
    checks.assertEqual((old.x() % STEP, old.y() % STEP, new.y() % STEP), (0, 0, 0))
    window.undo()
    checks.assertEqual(document.text(), source)

    # Loading fractional positions snaps for display without editing source.
    fractional = '#[position(x = -2.7, y = 4.3)]\n' + source
    cursor = QTextCursor(document.text_document)
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.insertText(fractional)
    window.session.refresh()
    checks.assertEqual(scene.items_by_id[Node(('source',))].pos(), QPointF(-3 * STEP, 4 * STEP))
    checks.assertEqual(document.text(), fractional)
    window.undo()
    checks.assertEqual(document.text(), source)

    # Background dots stay on scene grid intersections at different scales
    # and offsets, including negative coordinates.
    for scale, left in ((1, -72), (2, -63)):
        image = QImage(72, 72, QImage.Format.Format_ARGB32)
        image.fill(QColor('white'))
        painter = QPainter(image)
        scene.render(painter, QRectF(0, 0, 72, 72), QRectF(left, -72, 72 / scale, 72 / scale))
        painter.end()
        dot_x = round((-54 - left) * scale)
        dot_y = round(18 * scale)
        checks.assertEqual(image.pixelColor(dot_x, dot_y), QColor('#d3d3d3'))
        checks.assertEqual(image.pixelColor(dot_x + 5, dot_y + 5), QColor('white'))

    # Dragging from an output to an input connects them.
    source_socket = scene.items_by_id[Node(('source',))].sockets[('outputs', None)]
    printer_socket = scene.items_by_id[Node(('printer',))].sockets[('inputs', None)]
    drag(view, at(source_socket.scenePos()), at(printer_socket.scenePos()))
    checks.assertIn('connect source -> printer;', document.text())
    checks.assertEqual(len(scene.edge_graphics), 1)

    # Edges follow snapped positions immediately, before a source edit.
    printer = scene.items_by_id[Node(('printer',))]
    position = printer.pos()
    printer.setPos(position + QPointF(1.4 * STEP, -0.6 * STEP))
    edge = scene.edge_graphics[0]
    checks.assertEqual(edge.path().pointAtPercent(0), edge.source.socket_position('outputs', edge.edge.source[1]))
    checks.assertEqual(edge.path().pointAtPercent(1), edge.target.socket_position('inputs', edge.edge.target[1]))
    printer.setPos(position)

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
