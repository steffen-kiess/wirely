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
    QApplication, QDockWidget, QFileDialog, QInputDialog, QMainWindow, QMenu, QMessageBox, QTabWidget,
    QTreeWidget, QTreeWidgetItem,
)

from wirely.compiler import ast
from wirely.compiler.outline import Connection, Node, NodeType

from . import graph_model
from .graph_view import EdgeGraphic, GraphTab, ItemGraphic
from .graph_model import Unresolved
from .grid import from_pixels
from .palette import DeclarationDialog, NodePalette
from .program import renamed_path
from .properties import PropertiesPanel
from .sync import Session
from .text_view import CodeEditor

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

        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(lambda index: self.close_tab(self.tabs.widget(index)))
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)

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

    def _create_menus(self):
        file_menu = self.menuBar().addMenu('&File')
        self._action(file_menu, '&Open…', QKeySequence.StandardKey.Open, self.open_file)
        self._action(file_menu, '&Save', QKeySequence.StandardKey.Save, self.save_current)
        self._action(file_menu, 'Save &All', 'Ctrl+Shift+S', self.save_all)
        self._action(file_menu, '&Close Tab', QKeySequence.StandardKey.Close,
                     lambda: self.close_tab(self.tabs.currentWidget()))
        file_menu.addSeparator()
        self._action(file_menu, '&Quit', QKeySequence.StandardKey.Quit, self.close)
        edit_menu = self.menuBar().addMenu('&Edit')
        self._action(edit_menu, '&Undo', QKeySequence.StandardKey.Undo, self.undo)
        self._action(edit_menu, '&Redo', QKeySequence.StandardKey.Redo, self.redo)
        view_menu = self.menuBar().addMenu('&View')
        self._action(view_menu, 'Program &Graph', 'Ctrl+G', lambda: self.open_graph(()))
        self._action(view_menu, '&Source of Selection', 'Ctrl+E', self.show_selection_source)

    def _action(self, menu, text, shortcut, slot):
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # Tabs

    def open_text(self, filename, focus=False):
        editor = self.text_tabs.get(filename)
        if editor is None:
            editor = CodeEditor(self.session.documents[filename], self)
            editor.element_cursor.connect(lambda offset, filename=filename: self._text_cursor(filename, offset))
            self.text_tabs[filename] = editor
            self.tabs.addTab(editor, '')
            editor.set_diagnostics(self._file_diagnostics(filename))
            self._update_titles()
        if focus:
            self.tabs.setCurrentWidget(editor)
        return editor

    def open_graph(self, path, focus=True):
        path = tuple(path)
        for tab in self.graph_tabs:
            if tab.path == path:
                if focus:
                    self.tabs.setCurrentWidget(tab)
                return tab
        tab = GraphTab(path, self)
        scene = tab.graph_scene
        scene.check_connection = lambda source, target, replacing, tab=tab: self.program.can_connect(
            tab.path, source, target, replacing=replacing)
        scene.moved.connect(lambda positions: self.edit(lambda: self.program.move(positions)))
        scene.connect_requested.connect(
            lambda source, target, tab=tab: self.edit(lambda: self.program.connect(tab.path, source, target)))
        scene.reconnect_requested.connect(
            lambda connection, target: self.edit(lambda: self.program.reconnect(connection, target=target)))
        scene.disconnect_requested.connect(lambda connection: self.edit(lambda: self.program.delete([connection])))
        scene.item_activated.connect(self._item_activated)
        scene.status.connect(lambda message: self.statusBar().showMessage(message, 8000))
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
        self.tabs.addTab(tab, '')
        self._refresh_graph(tab)
        self._update_titles()
        if focus:
            self.tabs.setCurrentWidget(tab)
        return tab

    def close_tab(self, widget):
        if widget is None:
            return
        if isinstance(widget, GraphTab):
            self.graph_tabs.remove(widget)
        else:
            del self.text_tabs[widget.document_record.filename]
        self.tabs.removeTab(self.tabs.indexOf(widget))
        widget.deleteLater()

    def _tab_changed(self, index):
        widget = self.tabs.widget(index)
        if isinstance(widget, GraphTab):
            self._graph_selection(widget)

    def _update_titles(self, *_):
        for filename, editor in self.text_tabs.items():
            modified = '*' if self.session.documents[filename].is_modified else ''
            index = self.tabs.indexOf(editor)
            self.tabs.setTabText(index, os.path.basename(filename) + modified)
            self.tabs.setTabToolTip(index, filename)
        for tab in self.graph_tabs:
            index = self.tabs.indexOf(tab)
            self.tabs.setTabText(index, f'◇ {_scope_label(tab.path)}')
            self.tabs.setTabToolTip(index, f'Graph of {_scope_label(tab.path)}')
        modified = any(document.is_modified for document in self.session.documents.values())
        names = ', '.join(os.path.basename(filename) for filename in self.session.documents)
        self.setWindowTitle(f'{names}{" *" if modified else ""} – Wirely')
        self._fill_tree()

    # Refresh

    def refresh_views(self):
        for filename, editor in self.text_tabs.items():
            editor.set_diagnostics(self._file_diagnostics(filename))
        for tab in self.graph_tabs:
            self._refresh_graph(tab)
        self._update_titles()
        current = self.tabs.currentWidget()
        if isinstance(current, GraphTab):
            self._graph_selection(current)
        elif self.properties.target is not None:
            self.properties.show_element(self.program, self.properties.target, not self.program.parse_errors)
        errors = self.program.parse_errors
        if errors:
            filename, error = next(iter(errors.items()))
            self.statusBar().showMessage(f'{os.path.basename(filename)}:{error.line}: syntax error')
        else:
            count = sum(1 for entry in self.program.analysis.diagnostics
                        if entry.diagnostic.filename in self.session.documents
                        and entry.diagnostic.kind in ('error', 'internal_error'))
            self.statusBar().showMessage(f'{count} error{"s" if count != 1 else ""}' if count else 'No errors')

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
            self.open_text(value, focus=True)
        else:
            self.open_graph(value)

    # Edits

    def edit(self, make_edits):
        """Apply the edits returned by make_edits, or report why there are none."""
        try:
            edits = make_edits()
        except _EDIT_ERRORS as error:
            self.statusBar().showMessage(f'Cannot edit: {error}', 10000)
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
            self.statusBar().showMessage(f'Cannot rename: {error}', 10000)
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
        new_name, accepted = QInputDialog.getText(self, 'Rename', 'New name:', text=target.path[-1])
        if accepted and new_name.strip() and new_name.strip() != target.path[-1]:
            self.rename(target, new_name)

    def add_node(self, tab, position):
        if tab.state != 'live':
            return
        if position is None:
            position = tab.view.mapToScene(tab.view.viewport().rect().center())
        palette = NodePalette(self.program.analysis.node_types(tab.path), self)
        if palette.exec() and palette.selected_type():
            type_text = palette.selected_type()
            self.edit(lambda: self.program.add_node(tab.path, type_text, from_pixels(position.x(), position.y())))

    def add_declaration(self, tab, kind, position):
        # Public parameters and ports use PascalCase, the others snake_case.
        base = {'parameter': 'NewParameter', 'input': 'NewInput', 'output': 'NewOutput'}.get(kind, f'new_{kind}')
        name = self.program.fresh_name(tab.path, base)
        dialog = DeclarationDialog(kind, name, list(self.program.analysis.type_names(tab.path)), self)
        while dialog.exec():
            try:
                declaration = dialog.declaration()
            except _EDIT_ERRORS as error:
                QMessageBox.warning(self, f'Add {kind}', str(error))
                continue
            self.edit(lambda: self.program.add_declaration(tab.path, declaration,
                                                          from_pixels(position.x(), position.y())))
            return

    def _refuse_while_broken(self, action):
        # These edits update references in all files, which must parse.
        if self.program.parse_errors:
            self.statusBar().showMessage(f'{action} needs all files to parse.', 10000)
            return True
        return False

    def undo(self):
        widget = self.tabs.currentWidget()
        if isinstance(widget, CodeEditor):
            widget.undo()
        elif isinstance(widget, GraphTab):
            self.session.undo(self._graph_files(widget))

    def redo(self):
        widget = self.tabs.currentWidget()
        if isinstance(widget, CodeEditor):
            widget.redo()
        elif isinstance(widget, GraphTab):
            self.session.redo(self._graph_files(widget))

    def _graph_files(self, tab):
        filenames = tab.model.filenames if tab.model is not None else ()
        return [filename for filename in filenames if filename in self.session.documents] \
            or list(self.session.documents)

    # Files

    def open_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Open', '', 'Wirely sources (*.wrl);;All files (*)')
        if filename:
            document = self.session.add_file(filename)
            document.text_document.modificationChanged.connect(self._update_titles)
            self.open_text(document.filename, focus=True)

    def save_current(self):
        widget = self.tabs.currentWidget()
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
        if self.tabs.currentWidget() is tab:
            self.properties.show_element(self.program, target, tab.state == 'live')
        if target is not None and tab.state == 'live':
            location = self.program.span_of(target)
            if location is not None and location[0] in self.text_tabs:
                self.text_tabs[location[0]].reveal(location[1])

    def _text_cursor(self, filename, offset):
        if filename in self.program.parse_errors:
            return
        found = self.program.element_at(filename, offset)
        if found is None:
            return
        scope, target = found
        for tab in self.graph_tabs:
            if tab.path == scope and tab.state == 'live':
                graphic = tab.graph_scene.select_id(target)
                if graphic is not None:
                    tab.view.ensureVisible(graphic)
        if self.tabs.currentWidget() is self.text_tabs.get(filename):
            self.properties.show_element(self.program, target, True)

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

    def show_selection_source(self):
        widget = self.tabs.currentWidget()
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
