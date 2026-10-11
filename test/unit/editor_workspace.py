import math
import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QSplitter

from wirely.compiler.outline import Node
from wirely.editor.app import MainWindow
from wirely.editor.workspace import TabGroup


checks = TestCase()
application = QApplication.instance() or QApplication([])
source = '''node type Inner {}
node source: Std.DebugEventSource { Type: boolean }
node printer: Std.DebugEventPrinter { Type: boolean }
'''
other_source = 'state flag: boolean = false;\n' + '// scroll\n' * 100


def action(host, menu_name, label):
    menu = next(action.menu() for action in host.menuBar().actions() if action.text() == menu_name)
    return next(action for action in menu.actions() if action.text() == label)


def insert(editor, text):
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(text)


def move_mouse(bar, global_position):
    local = bar.mapFromGlobal(global_position)
    QApplication.sendEvent(bar, QMouseEvent(
        QMouseEvent.Type.MouseMove, local, global_position, Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def drag_tab(manager, widget, destination, *, cancel=False):
    group = manager.group_of(widget)
    bar = group.tabBar()
    start = bar.tabRect(group.indexOf(widget)).center()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    move_mouse(bar, destination)
    if cancel:
        QTest.keyClick(bar, Qt.Key.Key_Escape)
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       bar.mapFromGlobal(destination))
    application.processEvents()


def scene_center(view):
    return view.viewportTransform().inverted()[0].map(
        QPointF(view.viewport().width() / 2, view.viewport().height() / 2))


def check_center(view, expected):
    delta = (scene_center(view) - expected) * math.exp(view.zoom)
    # Allow floating-point roundoff at the one-pixel boundary.
    checks.assertLessEqual(abs(delta.x()), 1 + 1e-9)
    checks.assertLessEqual(abs(delta.y()), 1 + 1e-9)


with patch('sys.excepthook') as signal_errors, tempfile.TemporaryDirectory() as directory:
    filename = os.path.join(directory, 'main.wrl')
    other_filename = os.path.join(directory, 'other.wrl')
    for path, text in ((filename, source), (other_filename, other_source)):
        with open(path, 'w', encoding='utf-8') as file:
            file.write(text)
    window = MainWindow([filename, other_filename])
    window.resize(1200, 800)
    window.move(50, 50)
    window.show()
    application.processEvents()
    manager = window.workspaces
    graph = window.graph_tabs[0]
    editor = window.text_tabs[filename]
    other_editor = window.text_tabs[other_filename]
    first = window.workspace.active_group

    # Splits move the same widgets, support nesting, and collapse when emptied.
    graph.view.set_zoom(math.log(1.4))
    graphic = graph.graph_scene.items_by_id[Node(('printer',))]
    graphic.setSelected(True)
    scale = graph.view.transform().m11()
    center = scene_center(graph.view)
    action(window, '&View', 'Split Right').trigger()
    application.processEvents()
    right = manager.group_of(graph)
    checks.assertIsNot(right, first)
    checks.assertEqual(window.workspace.root.orientation(), Qt.Orientation.Horizontal)
    checks.assertEqual(graph.view.transform().m11(), scale)
    checks.assertEqual(graph.zoom_label.text(), '140%')
    check_center(graph.view, center)
    center = scene_center(graph.view)
    window.workspace.root.setSizes([700, 300])
    application.processEvents()
    check_center(graph.view, center)
    checks.assertEqual(graph.graph_scene.selected_ids(), [Node(('printer',))])
    inner = window.open_graph(('Inner',))
    checks.assertEqual(inner.zoom_label.text(), '100%')
    inner.view.set_zoom(math.log(0.6))
    checks.assertEqual(inner.zoom_label.text(), '60%')
    checks.assertEqual(graph.zoom_label.text(), '140%')
    action(window, '&View', 'Split Below').trigger()
    application.processEvents()
    below = manager.group_of(inner)
    checks.assertEqual(below.parentWidget().orientation(), Qt.Orientation.Vertical)
    checks.assertEqual(len(window.workspace.groups()), 3)
    manager.move(inner, first)
    checks.assertEqual(len(window.workspace.groups()), 2)
    manager.move(graph, first)
    checks.assertIs(window.workspace.root, first)
    checks.assertEqual(len(window.workspace.findChildren(QSplitter)), 0)
    window.close_tab(inner)
    application.processEvents()

    # Real tab-bar events reorder tabs; Escape restores the original order.
    manager.activate(editor)
    start_index = first.indexOf(editor)
    destination = first.tabBar().mapToGlobal(first.tabBar().tabRect(first.indexOf(graph)).center())
    drag_tab(manager, editor, destination, cancel=True)
    checks.assertEqual(first.indexOf(editor), start_index)
    drag_tab(manager, editor, destination)
    checks.assertNotEqual(first.indexOf(editor), start_index)

    # Dragging onto an edge previews and creates a split; the centre moves a tab.
    destination = first.mapToGlobal(QPoint(first.width() - 5, first.height() // 2))
    checks.assertEqual(manager.destination(destination)[1], 'right')
    manager.preview(destination)
    checks.assertTrue(manager.rubber_band.isVisible())
    checks.assertLess(manager.rubber_band.width(), first.width())
    drag_tab(manager, graph, destination)
    right = manager.group_of(graph)
    checks.assertIsNot(right, first)
    centre = right.mapToGlobal(right.rect().center())
    drag_tab(manager, other_editor, centre)
    checks.assertIs(manager.group_of(other_editor), right)
    manager.move(other_editor, first)

    # Both orientations and the tab insertion target are detected.
    for edge, point in (
        ('left', QPoint(5, right.height() // 2)),
        ('top', QPoint(right.width() // 2, right.tabBar().height() + 5)),
        ('bottom', QPoint(right.width() // 2, right.height() - 5)),
    ):
        checks.assertEqual(manager.destination(right.mapToGlobal(point))[1], edge)
    insertion = first.tabBar().mapToGlobal(first.tabBar().tabRect(0).center())
    checks.assertEqual(manager.destination(insertion), (first, None, 0))

    # Dropping onto a sidebar cancels rather than detaching the tab.
    drag_tab(manager, graph, window.tree.mapToGlobal(window.tree.rect().center()))
    checks.assertIs(manager.group_of(graph), right)
    checks.assertEqual(manager.windows, [])

    # Drag-out cancellation leaves the view in place; release creates a window.
    graph.graph_scene.select_id(Node(('printer',)))
    outside = QPoint(1500, 100)
    drag_tab(manager, graph, outside, cancel=True)
    checks.assertEqual(manager.windows, [])
    checks.assertIs(manager.group_of(graph), right)
    center = scene_center(graph.view)
    drag_tab(manager, graph, outside)
    checks.assertEqual(len(manager.windows), 1)
    detached = manager.windows[0]
    checks.assertIs(graph.window(), detached)
    checks.assertEqual(graph.view.transform().m11(), scale)
    checks.assertEqual(graph.zoom_label.text(), '140%')
    check_center(graph.view, center)
    center = scene_center(graph.view)
    detached.resize(1200, 900)
    application.processEvents()
    check_center(graph.view, center)
    checks.assertEqual(graph.graph_scene.selected_ids(), [Node(('printer',))])
    checks.assertIs(window.workspace.root, first)
    checks.assertIs(window.open_graph(()), graph)
    checks.assertIs(manager.active_host, detached)

    # All hosts share the toggle, including graphs and windows opened later.
    checks.assertTrue(action(detached, '&View', 'Snap to Grid').isChecked())
    action(detached, '&View', 'Snap to Grid').trigger()
    checks.assertFalse(action(window, '&View', 'Snap to Grid').isChecked())
    checks.assertFalse(graph.graph_scene.snapping)
    inner = window.open_graph(('Inner',))
    checks.assertFalse(inner.graph_scene.snapping)
    extra_window = manager.detach(inner)
    checks.assertFalse(action(extra_window, '&View', 'Snap to Grid').isChecked())
    action(window, '&View', 'Snap to Grid').trigger()
    checks.assertTrue(action(detached, '&View', 'Snap to Grid').isChecked())
    checks.assertTrue(action(extra_window, '&View', 'Snap to Grid').isChecked())
    checks.assertTrue(inner.graph_scene.snapping)
    window.close_tab(inner)
    manager.activate(graph)

    # Tabs can be dragged into an existing detached group and back to its
    # original pane without replacing the view or moving its source cursor.
    cursor = editor.textCursor()
    cursor.setPosition(source.index('printer'))
    editor.setTextCursor(cursor)
    position = editor.textCursor().position()
    target = detached.workspace.active_group
    drag_tab(manager, editor, target.mapToGlobal(target.rect().center()))
    checks.assertIs(editor.window(), detached)
    checks.assertEqual(editor.textCursor().position(), position)
    drag_tab(manager, editor, first.mapToGlobal(first.rect().center()))
    checks.assertIs(editor.window(), window)
    checks.assertEqual(editor.textCursor().position(), position)

    # Selection crosses windows without moving focus, and updates Properties.
    manager.activate(graph)
    application.processEvents()
    graph.graph_scene.clearSelection()
    graphic.setSelected(True)
    checks.assertEqual(window.properties.target, Node(('printer',)))
    checks.assertIs(application.focusWidget(), graph.view)
    checks.assertTrue(editor._link_selections)

    # Clicking Properties must retain the detached graph's inspected element.
    manager.activate(other_editor)
    application.processEvents()
    manager.activate(graph)
    application.processEvents()
    field = window.properties.widget().findChild(QLineEdit)
    window.activateWindow()
    field.setFocus()
    application.processEvents()
    checks.assertEqual(window.properties.target, Node(('printer',)))

    # Independent window actions operate on their own active views.
    insert(editor, '// first edit\n')
    insert(other_editor, '// second edit\n')
    window.session.refresh()
    manager.activate(editor)
    manager.move(other_editor, detached.workspace.active_group)
    action(detached, '&Edit', '&Undo').trigger()
    checks.assertEqual(other_editor.document_record.text(), other_source)
    checks.assertIn('// first edit', editor.document_record.text())
    action(detached, '&Edit', '&Redo').trigger()
    checks.assertIn('// second edit', other_editor.document_record.text())
    application.processEvents()
    QTest.keySequence(other_editor, action(detached, '&File', '&Save').shortcut())
    application.processEvents()
    checks.assertFalse(other_editor.document_record.is_modified)
    checks.assertTrue(editor.document_record.is_modified)
    group = manager.group_of(editor)
    checks.assertTrue(group.tabText(group.indexOf(editor)).endswith('*'))
    action(window, '&File', '&Save').trigger()
    checks.assertFalse(editor.document_record.is_modified)
    checks.assertEqual(graph.state, 'live')
    checks.assertEqual(detached.statusBar().currentMessage(), window.statusBar().currentMessage())

    # A source parse failure freezes the graph in another window.
    insert(editor, 'node')
    window.session.refresh()
    checks.assertEqual(graph.state, 'frozen')
    action(window, '&Edit', '&Undo').trigger()
    window.session.refresh()
    checks.assertEqual(graph.state, 'live')

    # Closing a detached window returns all tabs in their visual order.
    cursor_position = other_editor.textCursor().position()
    other_editor.verticalScrollBar().setValue(40)
    scroll_position = other_editor.verticalScrollBar().value()
    checks.assertGreater(scroll_position, 0)
    manager.split(other_editor, 'bottom')
    order = [group.widget(index) for group in detached.workspace.groups() for index in range(group.count())]
    center = scene_center(graph.view)
    detached.close()
    application.processEvents()
    checks.assertEqual(manager.windows, [])
    checks.assertEqual([first.widget(index) for index in range(first.count())][-2:], order)
    checks.assertEqual(other_editor.textCursor().position(), cursor_position)
    checks.assertEqual(other_editor.verticalScrollBar().value(), scroll_position)
    checks.assertIs(graph.window(), window)
    checks.assertEqual(graph.view.zoom, math.log(1.4))
    checks.assertEqual(graph.zoom_label.text(), '140%')
    check_center(graph.view, center)

    # Individual return and close actions clean up empty detached windows.
    detached = manager.detach(other_editor)
    action(detached, '&View', 'Return to Main Window').trigger()
    checks.assertEqual(manager.windows, [])
    detached = manager.detach(other_editor)
    application.processEvents()
    QTest.keySequence(other_editor, action(detached, '&File', '&Close Tab').shortcut())
    application.processEvents()
    checks.assertEqual(manager.windows, [])
    checks.assertNotIn(other_filename, window.text_tabs)
    checks.assertIn(other_filename, window.session.documents)
    other_editor = window.open_text(other_filename, focus=True)

    # New views open in the initiating window; existing ones are activated.
    detached = manager.detach(graph)
    inner = window.open_graph(('Inner',), host=detached)
    checks.assertIs(inner.window(), detached)
    checks.assertIs(window.open_text(filename, focus=True, host=detached), editor)
    checks.assertIs(editor.window(), window)

    # Quit asks once for the whole session. Cancel leaves all windows intact.
    insert(editor, '// unsaved\n')
    with patch('wirely.editor.app.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel) as prompt:
        action(detached, '&File', '&Quit').trigger()
        checks.assertEqual(prompt.call_count, 1)
    checks.assertTrue(window.isVisible())
    checks.assertTrue(detached.isVisible())
    checks.assertFalse(manager.shutting_down)
    with patch('wirely.editor.app.QMessageBox.question', return_value=QMessageBox.StandardButton.Save) as prompt:
        action(detached, '&File', '&Quit').trigger()
        checks.assertEqual(prompt.call_count, 1)
    checks.assertFalse(window.isVisible())
    checks.assertEqual(manager.windows, [])
    with open(filename, encoding='utf-8') as file:
        checks.assertIn('// unsaved', file.read())
    window.deleteLater()
    application.processEvents()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    checks.assertEqual(signal_errors.call_args_list, [])

    # Discard closes every host without returning tabs or saving the document.
    window = MainWindow([filename])
    window.show()
    window.close_tab(window.text_tabs[filename])
    detached = window.workspaces.detach(window.graph_tabs[0])
    checks.assertEqual(len(window.workspace.groups()), 1)
    checks.assertEqual(window.workspace.active_group.count(), 0)
    editor = window.open_text(filename, focus=True, host=window)
    insert(editor, '// discard\n')
    with patch('wirely.editor.app.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard) as prompt:
        window.close()
        checks.assertEqual(prompt.call_count, 1)
    checks.assertFalse(detached.isVisible())
    with open(filename, encoding='utf-8') as file:
        checks.assertNotIn('// discard', file.read())
    window.deleteLater()
    application.processEvents()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    checks.assertEqual(signal_errors.call_args_list, [])


async def run_test():
    pass
