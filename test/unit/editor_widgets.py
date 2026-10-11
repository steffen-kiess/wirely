import math
import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QTextCursor, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from wirely.compiler.editing import apply_edits, set_parameter_binding
from wirely.compiler.outline import Node, State
from wirely.editor.app import MainWindow
from wirely.editor import graph_model
from wirely.editor.graph_view import GraphScene
from wirely.editor.grid import STEP, from_pixels, snap
from wirely.editor.program import Program
from wirely.editor.sync import Document


checks = TestCase()
application = QApplication.instance() or QApplication([])

# Source edits keep the order of insertions at the same offset, as
# apply_edits() does: binding a parameter after one without a comma.
binding_source = 'node receiver: KNX.EventReceiver {\n  GroupAddress: "1/2/3"\n}\n'
document = Document('main.wrl', binding_source)
edits = set_parameter_binding([('main.wrl', binding_source)], Node(('receiver',)), 'Type', 'boolean')
document.apply(edits)
checks.assertEqual(document.text(), apply_edits([('main.wrl', binding_source)], edits)[0][1])
checks.assertEqual(document.text(),
                   'node receiver: KNX.EventReceiver {\n  GroupAddress: "1/2/3",\n  Type: boolean\n}\n')

# Connections to a multi input end at separate points within its row. Ordered
# inputs number them in source order; unordered ones sort them by source
# position and refuse duplicates.
fan_source = '''node type First {
  input Values: multi value boolean;
  default output: value boolean;
  node constant: Std.Constant { Type: boolean, Value: false }
  connect constant -> default output;
}
#[Diagram.Position(X = 0, Y = 0)]
node a: Std.Constant { Type: boolean, Value: true }
#[Diagram.Position(X = 0, Y = 4)]
node b: Std.Constant { Type: boolean, Value: false }
#[Diagram.Position(X = 0, Y = 8)]
node c: Std.Constant { Type: boolean, Value: true }
#[Diagram.Position(X = 14, Y = 0)]
node first: First {}
connect c, a, b -> first.Values;
#[Diagram.Position(X = 14, Y = 6)]
node all: Std.And {}
connect c, a, b, a -> all;
'''
fan_scene = GraphScene()
fan_scene.set_model(graph_model.build(Program([('main.wrl', fan_source)]), ()))
fan_items = fan_scene.items_by_id


ordered = [edge for edge in fan_scene.edge_graphics if edge.edge.target[0] == Node(('first',))]
checks.assertEqual([(edge.edge.source[0].path[0], edge.number()) for edge in ordered],
                   [('c', 1), ('a', 2), ('b', 3)])
checks.assertEqual([edge.path().currentPosition().y() for edge in ordered],
                   sorted(edge.path().currentPosition().y() for edge in ordered))
values = fan_items[Node(('first',))].sockets[('inputs', 'Values')]
checks.assertTrue(all(abs(edge.path().currentPosition().y() - values.scenePos().y()) < STEP / 2
                      for edge in ordered))
unordered = [edge for edge in fan_scene.edge_graphics if edge.edge.target[0] == Node(('all',))]
checks.assertEqual([edge.number() for edge in unordered], [None] * 4)
by_end = sorted(unordered, key=lambda edge: edge.path().currentPosition().y())
checks.assertEqual([edge.edge.source[0].path[0] for edge in by_end], ['a', 'a', 'b', 'c'])
checks.assertEqual(len({edge.path().currentPosition().y() for edge in unordered}), 4)
all_input = fan_items[Node(('all',))].sockets[('inputs', None)]
checks.assertTrue(fan_scene.duplicate(fan_items[Node(('b',))].sockets[('outputs', None)], all_input))
checks.assertTrue(all_input.socket.unordered and not values.socket.unordered)
# Moving a source reorders the ends at an unordered input.
fan_items[Node(('c',))].setPos(0, -10 * STEP)
by_end = sorted(unordered, key=lambda edge: edge.path().currentPosition().y())
checks.assertEqual([edge.edge.source[0].path[0] for edge in by_end], ['c', 'a', 'a', 'b'])

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
        application.processEvents()
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    application.processEvents()


def wheel(view, steps):
    viewport = view.viewport()
    position = QPointF(viewport.rect().center())
    QApplication.sendEvent(viewport, QWheelEvent(
        position, viewport.mapToGlobal(position), QPoint(), QPoint(0, 120 * steps), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))


def scene_center(view):
    return view.viewportTransform().inverted()[0].map(
        QPointF(view.viewport().width() / 2, view.viewport().height() / 2))


def check_center(view, expected):
    delta = (scene_center(view) - expected) * math.exp(view.zoom)
    checks.assertLessEqual(abs(delta.x()), 1)
    checks.assertLessEqual(abs(delta.y()), 1)


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
    checks.assertEqual(view.zoom, 0)
    checks.assertEqual(tab.zoom_label.text(), '100%')

    def at(position):
        return view.mapFromScene(position)

    # Resizing preserves a panned center even when a small scene fits in
    # the enlarged viewport. Repeated cycles must not accumulate drift.
    for factor in (0.15, 0.6, 1, 1.8, 4):
        view.set_zoom(math.log(factor))
        view.fit()
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().minimum() + 2)
        view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() - 2)
        center = scene_center(view)
        for _ in range(3):
            for width, height in ((1600, 1000), (850, 600), (1200, 800)):
                window.resize(width, height)
                application.processEvents()
                check_center(view, center)
        for banner in ('Read-only graph', ''):
            tab.update_graph(tab.model, 'live', banner)
            application.processEvents()
            check_center(view, center)
        checks.assertEqual(tab.zoom_label.text(), f'{100 * factor:.0f}%')
    view.set_zoom(0)

    # Refreshes and changes to the graph bounds preserve the viewport at
    # different zoom levels, even when the scene is smaller than the view.
    for zoom in (1, 0.15, 0.6, 1.8, 4):
        view.set_zoom(math.log(zoom))
        origin = at(QPointF())
        for _ in range(3):
            window.refresh_views()
            application.processEvents()
            checks.assertEqual(at(QPointF()), origin)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() - 13)
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 11)
        origin = at(QPointF())
        for position in ((100, 100), (-100, -100), (16, 10)):
            window.edit(lambda position=position: window.program.move({Node(('printer',)): position}))
            application.processEvents()
            checks.assertEqual(at(QPointF()), origin)
            window.undo()
            application.processEvents()
            checks.assertEqual(at(QPointF()), origin)
        checks.assertEqual(document.text(), source)
    view.set_zoom(0)
    view.centerOn(scene.itemsBoundingRect().center())

    # User navigation in the active source tab highlights graph items
    # without selecting or scrolling to them; Show in Graph does both.
    editor = window.text_tabs[filename]
    window.workspaces.activate(editor)
    scene.clearSelection()
    with patch.object(view, 'ensureVisible', wraps=view.ensureVisible) as reveal:
        editor.go_to(document.text().index('node source') + 5)
        checks.assertTrue(scene.items_by_id[Node(('source',))].linked)
        editor.go_to(document.text().index('node printer') + 5)
        checks.assertFalse(scene.items_by_id[Node(('source',))].linked)
        checks.assertTrue(scene.items_by_id[Node(('printer',))].linked)
        checks.assertEqual(scene.selected_ids(), [])
        reveal.assert_not_called()
        window.show_cursor_in_graph()
        checks.assertEqual(scene.selected_ids(), [Node(('printer',))])
        reveal.assert_called_with(scene.items_by_id[Node(('printer',))])
    checks.assertIs(window.workspaces.active_view, tab)
    scene.clearSelection()
    view.set_zoom(0)
    view.centerOn(scene.itemsBoundingRect().center())

    # The wheel zooms; zooming in and out again restores the scale.
    scale = view.transform().m11()
    wheel(view, 1)
    checks.assertGreater(view.transform().m11(), scale)
    checks.assertAlmostEqual(view.zoom, 0.18)
    checks.assertAlmostEqual(view.transform().m11(), math.exp(view.zoom))
    checks.assertEqual(tab.zoom_label.text(), '120%')
    wheel(view, -1)
    checks.assertAlmostEqual(view.transform().m11(), scale)
    checks.assertAlmostEqual(view.zoom, 0)
    checks.assertEqual(tab.zoom_label.text(), '100%')
    viewport = view.viewport()
    position = QPointF(viewport.rect().center())
    QApplication.sendEvent(viewport, QWheelEvent(
        position, viewport.mapToGlobal(position), QPoint(0, -40), QPoint(), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
    checks.assertAlmostEqual(view.zoom, -0.06)
    checks.assertAlmostEqual(view.transform().m11(), math.exp(-0.06))
    checks.assertEqual(tab.zoom_label.text(), '94%')
    for factor, steps in ((0.15, -1), (4, 1)):
        view.set_zoom(math.log(factor))
        wheel(view, steps)
        checks.assertEqual(view.zoom, math.log(factor))
        checks.assertAlmostEqual(view.transform().m11(), factor)
        checks.assertEqual(tab.zoom_label.text(), f'{100 * factor:.0f}%')
    view.set_zoom(0)

    # Dragging a node writes its position; one undo removes it again.
    printer = scene.items_by_id[Node(('printer',))]
    start = at(printer.mapToScene(20, 8))
    x, y = map(snap, from_pixels(printer.pos().x() + 40, printer.pos().y() + 60))
    expected = f'Diagram.Position(X = {x:g}, Y = {y:g})'
    origin = at(QPointF())
    drag(view, start, start + QPoint(40, 60))
    checks.assertIn(expected, document.text())
    checks.assertEqual(at(QPointF()), origin)
    # Resetting the selection's positions applies to placed items only.
    scene.clearSelection()
    scene.items_by_id[Node(('source',))].setSelected(True)
    checks.assertEqual(window._placed_selection(tab), [])
    printer.setSelected(True)
    checks.assertEqual(window._placed_selection(tab), [Node(('printer',))])
    window.reset_selection_positions(tab)
    checks.assertNotIn('Diagram.Position', document.text())
    window.workspaces.activate(tab)
    window.undo()
    checks.assertIn(expected, document.text())
    window.undo()
    checks.assertEqual(document.text(), source)

    # Geometry is on the grid and ports half a unit below grid lines; moving
    # a selection at different zoom levels preserves its relative placement
    # and is one undoable edit.
    for graphic in scene.items_by_id.values():
        for value in (graphic.width, graphic.height, graphic.header):
            checks.assertEqual(value % STEP, 0)
        for socket in graphic.sockets.values():
            checks.assertEqual(socket.scenePos().x() % STEP, 0)
            checks.assertEqual(socket.scenePos().y() % STEP, STEP / 2)
    for zoom, delta in ((0.6, QPoint(-37, 53)), (1.8, QPoint(-37, 53)), (2, QPoint(54, 0))):
        view.set_zoom(math.log(zoom))
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
        dx, dy = map(snap, from_pixels(scene_delta.x(), scene_delta.y()))
        origin = at(QPointF())
        drag(view, start, start + delta)
        checks.assertEqual(at(QPointF()), origin)
        for item_id, position in before.items():
            graphic = scene.items_by_id[item_id]
            checks.assertEqual(graphic.pos(), position + QPointF(dx * STEP, dy * STEP))
            x, y = from_pixels(graphic.pos().x(), graphic.pos().y())
            checks.assertIn(f'Diagram.Position(X = {x:g}, Y = {y:g})', document.text())
        window.undo()
        checks.assertEqual(document.text(), source)
    view.set_zoom(0)
    view.centerOn(scene.itemsBoundingRect().center())
    scene.clearSelection()

    # Both creation paths convert pixel positions to snapped grid coordinates.
    position = QPointF(-2.7 * STEP, 4.3 * STEP)
    with patch('wirely.editor.app.NodePalette') as palette:
        palette.return_value.exec.return_value = True
        palette.return_value.selected_type.return_value = 'Std.DebugEventPrinter'
        window.add_node(tab, position)
    checks.assertIn('Diagram.Position(X = -3, Y = 4)', document.text())
    window.undo()
    with patch('wirely.editor.app.DeclarationDialog') as dialog:
        dialog.return_value.exec.return_value = True
        dialog.return_value.declaration.return_value = 'state extra: boolean = false;'
        window.add_declaration(tab, 'state', position)
    checks.assertIn('Diagram.Position(X = -3, Y = 4)', document.text())
    state = scene.items_by_id[State(('extra',))]
    old = state.sockets[('outputs', 'old')].scenePos()
    new = state.sockets[('outputs', 'new')].scenePos()
    checks.assertEqual(new - old, QPointF(0, STEP))
    checks.assertEqual((old.x() % STEP, old.y() % STEP, new.y() % STEP), (0, STEP / 2, STEP / 2))
    window.undo()
    checks.assertEqual(document.text(), source)

    # Creation without snapping preserves fractional coordinates for both
    # nodes and declarations.
    window.snap_action.trigger()
    checks.assertFalse(scene.snapping)
    with patch('wirely.editor.app.NodePalette') as palette:
        palette.return_value.exec.return_value = True
        palette.return_value.selected_type.return_value = 'Std.DebugEventPrinter'
        window.add_node(tab, position)
    checks.assertIn('Diagram.Position(X = -2.7, Y = 4.3)', document.text())
    window.undo()
    with patch('wirely.editor.app.DeclarationDialog') as dialog:
        dialog.return_value.exec.return_value = True
        dialog.return_value.declaration.return_value = 'state extra: boolean = false;'
        window.add_declaration(tab, 'state', position)
    checks.assertIn('Diagram.Position(X = -2.7, Y = 4.3)', document.text())
    checks.assertEqual(scene.items_by_id[State(('extra',))].pos(), position)
    window.undo()
    window.snap_action.trigger()

    # Loading fractional positions preserves them without editing source.
    fractional = '#[Diagram.Position(X = -2.7, Y = 4.3)]\n' + source
    cursor = QTextCursor(document.text_document)
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.insertText(fractional)
    window.session.refresh()
    checks.assertEqual(scene.items_by_id[Node(('source',))].pos(), QPointF(-2.7 * STEP, 4.3 * STEP))
    checks.assertEqual(document.text(), fractional)

    # Toggling is not an edit. Free dragging survives the resulting refresh,
    # and re-enabling snapping leaves the node alone until its next drag.
    origin = scene.items_by_id[Node(('source',))].pos()
    window.snap_action.trigger()
    checks.assertEqual(document.text(), fractional)
    view.centerOn(scene.itemsBoundingRect().center())
    scene.clearSelection()
    graphic = scene.items_by_id[Node(('source',))]
    start = at(graphic.mapToScene(20, 8))
    delta = QPoint(-13, 11)
    movement = view.mapToScene(start + delta) - view.mapToScene(start)
    drag(view, start, start + delta)
    expected_position = origin + movement
    checks.assertAlmostEqual(graphic.pos().x(), expected_position.x())
    checks.assertAlmostEqual(graphic.pos().y(), expected_position.y())
    window.session.refresh()
    checks.assertAlmostEqual(graphic.pos().x(), expected_position.x())
    checks.assertAlmostEqual(graphic.pos().y(), expected_position.y())
    free_text = document.text()
    window.snap_action.trigger()
    checks.assertEqual(document.text(), free_text)
    checks.assertAlmostEqual(graphic.pos().x(), expected_position.x())
    checks.assertAlmostEqual(graphic.pos().y(), expected_position.y())
    window.undo()
    checks.assertEqual(document.text(), fractional)
    checks.assertEqual(graphic.pos(), origin)
    view.centerOn(scene.itemsBoundingRect().center())
    start = at(graphic.mapToScene(20, 8))
    movement = view.mapToScene(start + delta) - view.mapToScene(start)
    x, y = map(snap, from_pixels((origin + movement).x(), (origin + movement).y()))
    drag(view, start, start + delta)
    checks.assertEqual(graphic.pos(), QPointF(x * STEP, y * STEP))
    window.undo()
    checks.assertEqual(document.text(), fractional)

    # Group snapping retains unequal fractional offsets, including negatives.
    window.edit(lambda: window.program.move({Node(('printer',)): (15.2, -0.25)}))
    view.centerOn(scene.itemsBoundingRect().center())
    before = {item_id: graphic.pos() for item_id, graphic in scene.items_by_id.items()}
    for graphic in scene.items_by_id.values():
        graphic.setSelected(True)
    graphic = scene.items_by_id[Node(('source',))]
    start = at(graphic.mapToScene(20, 8))
    delta = QPoint(-37, 53)
    movement = view.mapToScene(start + delta) - view.mapToScene(start)
    dx, dy = map(snap, from_pixels(movement.x(), movement.y()))
    drag(view, start, start + delta)
    for item_id, origin in before.items():
        graphic = scene.items_by_id[item_id]
        checks.assertAlmostEqual(graphic.pos().x(), origin.x() + dx * STEP)
        checks.assertAlmostEqual(graphic.pos().y(), origin.y() + dy * STEP)
    window.undo()
    window.undo()
    checks.assertEqual(document.text(), fractional)
    window.undo()
    checks.assertEqual(document.text(), source)
    scene.clearSelection()
    view.centerOn(scene.itemsBoundingRect().center())

    # Background dots stay on scene grid intersections at different scales
    # and offsets, including negative coordinates.
    dot_areas = []
    for scale, left in ((1, -72), (2, -63), (4, -63)):
        image = QImage(72, 72, QImage.Format.Format_ARGB32)
        image.fill(QColor('white'))
        painter = QPainter(image)
        scene.render(painter, QRectF(0, 0, 72, 72), QRectF(left, left, 72 / scale, 72 / scale))
        painter.end()
        dot_x = round((-54 - left) * scale)
        dot_y = dot_x
        checks.assertEqual(image.pixelColor(dot_x, dot_y), QColor('#d3d3d3'))
        checks.assertEqual(image.pixelColor(dot_x + 5, dot_y + 5), QColor('white'))
        dot_areas.append(sum(image.pixelColor(x, y) != QColor('white')
                             for x in range(dot_x - 4, dot_x + 5)
                             for y in range(dot_y - 4, dot_y + 5)))
    checks.assertGreater(dot_areas[1], dot_areas[0])
    checks.assertGreater(dot_areas[2], dot_areas[1])

    # Dragging from an output to an input connects them.
    source_socket = scene.items_by_id[Node(('source',))].sockets[('outputs', None)]
    printer_socket = scene.items_by_id[Node(('printer',))].sockets[('inputs', None)]
    drag(view, at(source_socket.scenePos()), at(printer_socket.scenePos()))
    checks.assertIn('connect source -> printer;', document.text())
    checks.assertEqual(len(scene.edge_graphics), 1)

    # Edges follow position changes immediately, before a source edit.
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

    # A file loaded with stored positions exercises source cursor changes
    # inside existing metadata, including dragging past the viewport edge.
    positioned_source = ('#[Diagram.Position(X = 0, Y = 0)]\n' + source.replace(
        'node printer:', '#[Diagram.Position(X = 10, Y = 7)]\nnode printer:'))
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(positioned_source)
    window = MainWindow([filename])
    window.show()
    application.processEvents()
    tab = window.graph_tabs[0]
    scene = tab.graph_scene
    view = tab.view
    for zoom in (0.15, 0.6, 1, 1.8, 4):
        view.set_zoom(math.log(zoom))
        printer = scene.items_by_id[Node(('printer',))]
        view.centerOn(printer.sceneBoundingRect().center())
        application.processEvents()
        for destination in (QPoint(5, 5), QPoint(-40, -40)):
            start = view.mapFromScene(printer.mapToScene(20, 8))
            origin = view.viewportTransform().map(QPointF())
            drag(view, start, destination)
            checks.assertEqual(view.viewportTransform().map(QPointF()), origin)
            window.undo()
            checks.assertEqual(view.viewportTransform().map(QPointF()), origin)
            scene.clearSelection()
    checks.assertEqual(window.session.documents[filename].text(), positioned_source)
    window.close()
