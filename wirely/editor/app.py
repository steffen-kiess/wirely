"""Main window of the editor: tabs with source files and scope graphs."""

import dataclasses
import html
import os
import signal
import sys

import lark
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QInputDialog, QMainWindow, QMenu, QMessageBox,
    QTreeWidget, QTreeWidgetItem,
)

from wirely.compiler import ast
from wirely.compiler.outline import Connection, Node, NodeType

from . import graph_model
from .graph_view import EdgeGraphic, GraphTab, ItemGraphic
from .graph_model import Unresolved
from .grid import from_pixels, snap
from .palette import DeclarationDialog, NodePalette
from .program import renamed_path
from .properties import PropertiesPanel
from .sync import Session, python_offset
from .text_view import CodeEditor
from .workspace import WorkspaceManager

_EDIT_ERRORS = (ValueError, TypeError, KeyError, ast.ProgramError, lark.exceptions.LarkError)


def run(filenames):
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName('Wirely')
    window = MainWindow(filenames)
    window.show()
    # Let Ctrl+C in the terminal end the program; Python's handler would only
    # run once Qt returns to Python code.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    return application.exec()


class MainWindow(QMainWindow):
    def __init__(self, filenames, parent=None):
        super().__init__(parent)
        self.session = Session(filenames, self)
        self.session.refreshed.connect(self.refresh_views)
        self.session.external_change.connect(self._external_change)
        self.text_tabs = {}
        self.graph_tabs = []
        self._linked_target = None
        self.snapping = True

        self.workspaces = WorkspaceManager(self)
        self.workspaces.activated.connect(self._view_activated)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.itemActivated.connect(self._tree_activated)
        dock = QDockWidget('Program', self)
        dock.setObjectName('program')
        dock.setWidget(self.tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.properties = PropertiesPanel(self)
        self.properties.rename_requested.connect(self.rename)
        self.properties.node_type_requested.connect(
            lambda node, type_text: self.edit(lambda: self.program.set_node_type(node, type_text)))
        self.properties.parameter_requested.connect(
            lambda node, name, value: self.edit(lambda: self.program.set_parameter(node, name, value)))
        dock = QDockWidget('Properties', self)
        dock.setObjectName('properties')
        dock.setWidget(self.properties)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self._create_menus()
        for document in self.session.documents.values():
            document.text_document.modificationChanged.connect(self._update_titles)
            self.open_text(document.filename)
        self.open_graph(())
        self.resize(1400, 900)
        self.refresh_views()

    @property
    def program(self):
        return self.session.program

    def _create_menus(self, host=None):
        host = host or self

        def current():
            return self.workspaces.current_widget(host)

        file_menu = host.menuBar().addMenu('&File')
        self._action(file_menu, '&Open…', QKeySequence.StandardKey.Open, lambda: self.open_file(host))
        self._action(file_menu, '&Save', QKeySequence.StandardKey.Save, lambda: self.save_current(host))
        self._action(file_menu, 'Save &All', 'Ctrl+Shift+S', self.save_all)
        self._action(file_menu, '&Close Tab', QKeySequence.StandardKey.Close,
                     lambda: self.close_tab(current()))
        file_menu.addSeparator()
        self._action(file_menu, '&Quit', QKeySequence.StandardKey.Quit, self.close)
        edit_menu = host.menuBar().addMenu('&Edit')
        self._action(edit_menu, '&Undo', QKeySequence.StandardKey.Undo, lambda: self.undo(host))
        self._action(edit_menu, '&Redo', QKeySequence.StandardKey.Redo, lambda: self.redo(host))
        view_menu = host.menuBar().addMenu('&View')
        self._action(view_menu, 'Program &Graph', 'Ctrl+G', lambda: self.open_graph((), host=host))
        self._action(view_menu, '&Source of Selection', 'Ctrl+E', lambda: self.show_selection_source(host))
        self._action(view_menu, 'Show &Cursor in Graph', 'Ctrl+Shift+E', lambda: self.show_cursor_in_graph(host))
        host.snap_action = QAction('Snap to Grid', host)
        host.snap_action.setCheckable(True)
        host.snap_action.setChecked(self.snapping)
        host.snap_action.toggled.connect(self._set_snapping)
        view_menu.addAction(host.snap_action)
        view_menu.addSeparator()
        split_right = self._action(view_menu, 'Split Right', None, lambda: self.workspaces.split(current(), 'right'))
        split_below = self._action(view_menu, 'Split Below', None, lambda: self.workspaces.split(current(), 'bottom'))
        detach = self._action(view_menu, 'Detach Tab', None, lambda: self.workspaces.detach(current()))
        return_tab = None
        if host is not self:
            return_tab = self._action(view_menu, 'Return to Main Window', None,
                                      lambda: self.workspaces.return_tab(current()))

        def update_actions():
            has_tab = current() is not None
            can_split = has_tab and host.workspace.active_group.count() > 1
            split_right.setEnabled(can_split)
            split_below.setEnabled(can_split)
            detach.setEnabled(has_tab)
            if return_tab is not None:
                return_tab.setEnabled(has_tab)
        view_menu.aboutToShow.connect(update_actions)

    def _action(self, menu, text, shortcut, slot):
        action = QAction(text, menu)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    def _set_snapping(self, enabled):
        self.snapping = enabled
        for tab in self.graph_tabs:
            tab.graph_scene.snapping = enabled
        for host in self.workspaces.hosts():
            action = host.snap_action
            action.blockSignals(True)
            action.setChecked(enabled)
            action.blockSignals(False)

    def _creation_position(self, position):
        coordinates = from_pixels(position.x(), position.y())
        return tuple(map(snap, coordinates)) if self.snapping else coordinates

    # Tabs

    def open_text(self, filename, focus=False, host=None):
        editor = self.text_tabs.get(filename)
        if editor is None:
            editor = CodeEditor(self.session.documents[filename], self)
            editor.element_cursor.connect(lambda offset, filename=filename: self._text_cursor(filename, offset))
            editor.graph_requested.connect(lambda filename=filename: self.show_in_graph(filename))
            self.text_tabs[filename] = editor
            self.workspaces.add(editor, host)
            editor.set_diagnostics(self._file_diagnostics(filename))
            self._link_text(self._linked_target)
            self._update_titles()
        if focus:
            self.workspaces.activate(editor)
        return editor

    def open_graph(self, path, focus=True, host=None):
        path = tuple(path)
        for tab in self.graph_tabs:
            if tab.path == path:
                if focus:
                    self.workspaces.activate(tab)
                return tab
        tab = GraphTab(path, self)
        scene = tab.graph_scene
        scene.snapping = self.snapping
        scene.check_connection = lambda source, target, replacing, tab=tab: self.program.can_connect(
            tab.path, source, target, replacing=replacing)
        scene.moved.connect(lambda positions: self.edit(lambda: self.program.move(positions)))
        scene.connect_requested.connect(
            lambda source, target, tab=tab: self.edit(lambda: self.program.connect(tab.path, source, target)))
        scene.reconnect_requested.connect(
            lambda connection, target: self.edit(lambda: self.program.reconnect(connection, target=target)))
        scene.disconnect_requested.connect(lambda connection: self.edit(lambda: self.program.delete([connection])))
        scene.item_activated.connect(self._item_activated)
        scene.status.connect(lambda message: self._status(message, 8000))
        scene.selectionChanged.connect(lambda tab=tab: self._graph_selection(tab))
        tab.view.context_menu.connect(lambda graphic, position, tab=tab: self._graph_menu(tab, graphic, position))
        tab.banner.linkActivated.connect(self._open_link)
        for key, slot in ((QKeySequence.StandardKey.Delete, lambda tab=tab: self.delete_selection(tab)),
                          ('Backspace', lambda tab=tab: self.delete_selection(tab)),
                          ('F2', lambda tab=tab: self.rename_selection(tab)),
                          ('Ctrl+Shift+A', lambda tab=tab: self.add_node(tab, None))):
            shortcut = QShortcut(QKeySequence(key), tab.view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self.graph_tabs.append(tab)
        self.workspaces.add(tab, host)
        self._refresh_graph(tab)
        self._update_titles()
        if focus:
            self.workspaces.activate(tab)
        return tab

    def close_tab(self, widget):
        if widget is None:
            return
        if isinstance(widget, GraphTab):
            self.graph_tabs.remove(widget)
        else:
            del self.text_tabs[widget.document_record.filename]
        group = self.workspaces.group_of(widget)
        group.removeTab(group.indexOf(widget))
        widget.setParent(None)
        widget.deleteLater()
        group.workspace.clean()
        host = group.workspace.host
        if host not in self.workspaces.hosts():
            host = self
        self.workspaces.select_group(host.workspace.active_group)
        self._update_titles()

    def _view_activated(self, widget):
        target = None
        editable = False
        if isinstance(widget, GraphTab):
            targets = widget.graph_scene.selected_ids()
            target = targets[0] if len(targets) == 1 else None
            editable = widget.state == 'live'
        elif isinstance(widget, CodeEditor):
            filename = widget.document_record.filename
            editable = filename not in self.program.parse_errors
            found = self.program.element_at(filename,
                                            python_offset(widget.document_record.text(), widget.textCursor().position()))
            target = found[1] if editable and found is not None else None
        self.properties.show_element(self.program, target, editable)
        if widget is not None:
            group = self.workspaces.group_of(widget)
            host = group.workspace.host
            if host is not self:
                host.setWindowTitle(f'{group.tabText(group.indexOf(widget))} – Wirely (Window {host.number})')

    def _update_titles(self, *_):
        if self.workspaces.shutting_down:
            return
        for filename, editor in self.text_tabs.items():
            modified = '*' if self.session.documents[filename].is_modified else ''
            group = self.workspaces.group_of(editor)
            index = group.indexOf(editor)
            group.setTabText(index, os.path.basename(filename) + modified)
            group.setTabToolTip(index, filename)
        for tab in self.graph_tabs:
            group = self.workspaces.group_of(tab)
            index = group.indexOf(tab)
            group.setTabText(index, f'◇ {_scope_label(tab.path)}')
            group.setTabToolTip(index, f'Graph of {_scope_label(tab.path)}')
        modified = any(document.is_modified for document in self.session.documents.values())
        names = ', '.join(os.path.basename(filename) for filename in self.session.documents)
        self.setWindowTitle(f'{names}{" *" if modified else ""} – Wirely')
        for host in self.workspaces.windows:
            title = host.workspace.active_group.tabText(host.workspace.active_group.currentIndex())
            host.setWindowTitle(f'{title} – Wirely (Window {host.number})')
        self._fill_tree()

    def _status(self, message, timeout=0):
        for host in self.workspaces.hosts():
            host.statusBar().showMessage(message, timeout)

    # Refresh

    def refresh_views(self):
        if self.workspaces.shutting_down:
            return
        for filename, editor in self.text_tabs.items():
            editor.set_diagnostics(self._file_diagnostics(filename))
        for tab in self.graph_tabs:
            self._refresh_graph(tab)
        self._link_text(self._linked_target)
        self._update_titles()
        current = self.workspaces.current_widget()
        if isinstance(current, GraphTab):
            self._view_activated(current)
        elif self.properties.target is not None:
            self.properties.show_element(self.program, self.properties.target, not self.program.parse_errors)
        errors = self.program.parse_errors
        if errors:
            filename, error = next(iter(errors.items()))
            self._status(f'{os.path.basename(filename)}:{error.line}: syntax error')
        else:
            count = sum(1 for entry in self.program.analysis.diagnostics
                        if entry.diagnostic.filename in self.session.documents
                        and entry.diagnostic.kind in ('error', 'internal_error'))
            self._status(f'{count} error{"s" if count != 1 else ""}' if count else 'No errors')

    def _refresh_graph(self, tab):
        program = self.program
        previous = tab.model.filenames if tab.model is not None else ()
        state = program.scope_state(tab.path, previous)
        model = graph_model.build(program, tab.path) if state == 'live' else None
        banner = ''
        if state == 'frozen':
            links = []
            for filename, error in program.parse_errors.items():
                message = html.escape(error.message.strip().splitlines()[0])
                links.append(f'<a href="{html.escape(filename)}#{error.start or 0}">'
                             f'{html.escape(os.path.basename(filename))}:{error.line}</a>: {message}')
            banner = '<b>Read-only:</b> the source does not parse. ' + '<br>'.join(links)
        elif state == 'missing':
            banner = f'<b>{html.escape(_scope_label(tab.path))}</b> no longer exists.'
        tab.update_graph(model, state, banner)

    def _file_diagnostics(self, filename):
        return [entry.diagnostic for entry in self.program.analysis.diagnostics
                if entry.diagnostic.filename == filename]

    def _fill_tree(self):
        expanded = {self.tree.topLevelItem(index).text(0): self.tree.topLevelItem(index).isExpanded()
                    for index in range(self.tree.topLevelItemCount())}
        self.tree.clear()
        files = QTreeWidgetItem(self.tree, ['Files'])
        for filename, document in self.session.documents.items():
            item = QTreeWidgetItem(files, [os.path.basename(filename) + ('*' if document.is_modified else '')])
            item.setToolTip(0, filename)
            item.setData(0, Qt.ItemDataRole.UserRole, ('file', filename))
        graphs = QTreeWidgetItem(self.tree, ['Graphs'])
        paths = list(self.program.user_scopes())
        paths += [tab.path for tab in self.graph_tabs if tab.path not in paths]
        for path in paths:
            item = QTreeWidgetItem(graphs, [_scope_label(path)])
            item.setData(0, Qt.ItemDataRole.UserRole, ('graph', path))
        for item in (files, graphs):
            item.setExpanded(expanded.get(item.text(0), True))

    def _tree_activated(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        kind, value = data
        if kind == 'file':
            self.open_text(value, focus=True, host=self)
        else:
            self.open_graph(value, host=self)

    # Edits

    def edit(self, make_edits):
        """Apply the edits returned by make_edits, or report why there are none."""
        try:
            edits = make_edits()
        except _EDIT_ERRORS as error:
            self._status(f'Cannot edit: {error}', 10000)
            # Move dragged items back.
            for tab in self.graph_tabs:
                self._refresh_graph(tab)
            return False
        if not edits:
            for tab in self.graph_tabs:
                self._refresh_graph(tab)
            return False
        self.session.apply(edits)
        return True

    def rename(self, target, new_name):
        if self._refuse_while_broken('Renaming'):
            return
        new_name = new_name.strip()
        try:
            edits = self.program.rename(target, new_name)
        except _EDIT_ERRORS as error:
            self._status(f'Cannot rename: {error}', 10000)
            self.refresh_views()
            return
        if isinstance(target, NodeType):
            for tab in self.graph_tabs:
                tab.path = renamed_path(tab.path, target, new_name)
        if self.properties.target == target:
            self.properties.target = _renamed_id(target, new_name)
        self.edit(lambda: edits)

    def delete_selection(self, tab):
        if tab.state != 'live' or self._refuse_while_broken('Deleting'):
            return
        targets = tab.graph_scene.selected_ids()
        if targets:
            self.edit(lambda: self.program.delete(targets))

    def rename_selection(self, tab):
        targets = [target for target in tab.graph_scene.selected_ids() if not isinstance(target, Connection)]
        if tab.state != 'live' or len(targets) != 1:
            return
        target = targets[0]
        new_name, accepted = QInputDialog.getText(tab.window(), 'Rename', 'New name:', text=target.path[-1])
        if accepted and new_name.strip() and new_name.strip() != target.path[-1]:
            self.rename(target, new_name)

    def add_node(self, tab, position):
        if tab.state != 'live':
            return
        if position is None:
            position = tab.view.mapToScene(tab.view.viewport().rect().center())
        palette = NodePalette(self.program.analysis.node_types(tab.path), tab.window())
        if palette.exec() and palette.selected_type():
            type_text = palette.selected_type()
            self.edit(lambda: self.program.add_node(tab.path, type_text, self._creation_position(position)))

    def add_declaration(self, tab, kind, position):
        # Public parameters and ports use PascalCase, the others snake_case.
        base = {'parameter': 'NewParameter', 'input': 'NewInput', 'output': 'NewOutput'}.get(kind, f'new_{kind}')
        name = self.program.fresh_name(tab.path, base)
        dialog = DeclarationDialog(kind, name, list(self.program.analysis.type_names(tab.path)), tab.window())
        while dialog.exec():
            try:
                declaration = dialog.declaration()
            except _EDIT_ERRORS as error:
                QMessageBox.warning(tab.window(), f'Add {kind}', str(error))
                continue
            self.edit(lambda: self.program.add_declaration(tab.path, declaration,
                                                          self._creation_position(position)))
            return

    def _refuse_while_broken(self, action):
        # These edits update references in all files, which must parse.
        if self.program.parse_errors:
            self._status(f'{action} needs all files to parse.', 10000)
            return True
        return False

    def undo(self, host=None):
        widget = self.workspaces.current_widget(host)
        if isinstance(widget, CodeEditor):
            widget.undo()
        elif isinstance(widget, GraphTab):
            self.session.undo(self._graph_files(widget))

    def redo(self, host=None):
        widget = self.workspaces.current_widget(host)
        if isinstance(widget, CodeEditor):
            widget.redo()
        elif isinstance(widget, GraphTab):
            self.session.redo(self._graph_files(widget))

    def _graph_files(self, tab):
        filenames = tab.model.filenames if tab.model is not None else ()
        return [filename for filename in filenames if filename in self.session.documents] \
            or list(self.session.documents)

    # Files

    def open_file(self, host=None):
        filename, _ = QFileDialog.getOpenFileName(host or self, 'Open', '', 'Wirely sources (*.wrl);;All files (*)')
        if filename:
            document = self.session.add_file(filename)
            document.text_document.modificationChanged.connect(self._update_titles)
            self.open_text(document.filename, focus=True, host=host)

    def save_current(self, host=None):
        widget = self.workspaces.current_widget(host)
        if isinstance(widget, CodeEditor):
            self.session.save(widget.document_record)
        elif isinstance(widget, GraphTab):
            for filename in self._graph_files(widget):
                if self.session.documents[filename].is_modified:
                    self.session.save(self.session.documents[filename])
        self._update_titles()

    def save_all(self):
        for document in self.session.documents.values():
            if document.is_modified:
                self.session.save(document)
        self._update_titles()

    def closeEvent(self, event):
        modified = [document for document in self.session.documents.values() if document.is_modified]
        if modified:
            names = ', '.join(os.path.basename(document.filename) for document in modified)
            answer = QMessageBox.question(
                self, 'Unsaved changes', f'Save changes to {names}?',
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                for document in modified:
                    self.session.save(document)
        self.session.shutdown()
        self.workspaces.shutdown()
        event.accept()

    def _external_change(self, document):
        answer = QMessageBox.question(
            self, 'File changed', f'{document.filename} changed on disk. Reload it and discard your changes?')
        if answer == QMessageBox.StandardButton.Yes:
            self.session.reload(document)

    # Selection and navigation

    def _graph_selection(self, tab):
        try:
            targets = tab.graph_scene.selected_ids()
        except RuntimeError:
            # The scene was deleted with its tab.
            return
        target = targets[0] if len(targets) == 1 else None
        if self.workspaces.active_view is tab:
            self.properties.show_element(self.program, target, tab.state == 'live')
        self._link_text(target if tab.state == 'live' else None)

    def _link_text(self, target):
        """Highlight the source of a graph element in the text tabs, without moving them."""
        self._linked_target = target
        location = self.program.span_of(target) if target is not None else None
        for filename, editor in self.text_tabs.items():
            editor.set_link_highlight(location[1] if location is not None and location[0] == filename else None)

    def _text_cursor(self, filename, offset):
        # Source edits also move inactive text cursors. Only user navigation
        # in the active source tab should highlight graph items.
        if self.workspaces.active_view is not self.text_tabs.get(filename) \
                or filename in self.program.parse_errors:
            return
        found = self.program.element_at(filename, offset)
        scope, target = found if found is not None else (None, None)
        for tab in self.graph_tabs:
            tab.graph_scene.set_link_highlight(target if tab.path == scope else None)
        if target is not None:
            self.properties.show_element(self.program, target, True)

    def show_cursor_in_graph(self, host=None):
        widget = self.workspaces.current_widget(host)
        if isinstance(widget, CodeEditor):
            self.show_in_graph(widget.document_record.filename)

    def show_in_graph(self, filename):
        """Select the element at the cursor of a text tab in the graph of its scope."""
        editor = self.text_tabs[filename]
        found = self.program.element_at(filename, python_offset(editor.document_record.text(),
                                                                editor.textCursor().position()))
        if found is None:
            self._status('No graph element at the cursor', 5000)
            return
        scope, target = found
        if isinstance(target, NodeType):
            self.open_graph(target.path)
            return
        tab = self.open_graph(scope)
        graphic = tab.graph_scene.select_id(target)
        if graphic is not None:
            tab.view.ensureVisible(graphic)
        self._graph_selection(tab)

    def _item_activated(self, model_item):
        node_type = model_item.node_type
        if node_type is not None and self.program.is_user_node_type(node_type):
            self.open_graph(node_type.path)
        else:
            self.show_source(model_item.id)

    def show_source(self, target):
        location = self.program.span_of(target) if not isinstance(target, Unresolved) else None
        if location is None:
            return
        filename, span = location
        self.open_text(filename, focus=True).reveal(span)

    def show_selection_source(self, host=None):
        widget = self.workspaces.current_widget(host)
        if isinstance(widget, GraphTab):
            targets = widget.graph_scene.selected_ids()
            if targets:
                self.show_source(targets[0])

    def _open_link(self, link):
        filename, _, offset = link.rpartition('#')
        if filename in self.session.documents:
            self.open_text(filename, focus=True).go_to(int(offset or 0))

    def _graph_menu(self, tab, graphic, position):
        menu = QMenu(self)
        live = tab.state == 'live'
        if isinstance(graphic, ItemGraphic) and not isinstance(graphic.model.id, Unresolved):
            target = graphic.model.id
            node_type = graphic.model.node_type
            if isinstance(target, Node) and node_type is not None and self.program.is_user_node_type(node_type):
                menu.addAction('Open Node Type', lambda: self.open_graph(node_type.path))
            menu.addAction('Show Source', lambda: self.show_source(target))
            menu.addAction('Rename…', lambda: self.rename_selection(tab)).setEnabled(live)
            menu.addAction('Delete', lambda: self.delete_selection(tab)).setEnabled(live)
        elif isinstance(graphic, EdgeGraphic) and graphic.edge.id is not None:
            target = graphic.edge.id
            menu.addAction('Show Source', lambda: self.show_source(target))
            menu.addAction('Delete Connection', lambda: self.delete_selection(tab)).setEnabled(live)
        elif graphic is None:
            menu.addAction('Add Node…', lambda: self.add_node(tab, position)).setEnabled(live)
            add = menu.addMenu('Add')
            add.setEnabled(live)
            kinds = ['state', 'temporary', 'constant', 'parameter']
            if tab.model is not None and tab.model.kind == 'node_type':
                kinds += ['input', 'output']
            for kind in kinds:
                add.addAction(kind.capitalize() + '…',
                              lambda kind=kind: self.add_declaration(tab, kind, position))
        if not menu.isEmpty():
            menu.exec(tab.view.mapToGlobal(tab.view.mapFromScene(QPointF(position))))


def _scope_label(path):
    return '.'.join(path) if path else 'Program'


def _renamed_id(target, new_name):
    return dataclasses.replace(target, path=target.path[:-1] + (new_name,))
