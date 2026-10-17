"""Movable tab groups sharing one editor session across windows."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, TypeAlias, cast

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QMenu, QRubberBand, QSplitter, QTabBar, QTabWidget, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from .app import MainWindow

Host: TypeAlias = 'MainWindow | DetachedWindow'


class TabBar(QTabBar):
    def __init__(self, group: TabGroup) -> None:
        super().__init__(group)
        self.group = group
        self.pressed: QWidget | None = None
        self.start: QPoint | None = None
        self.original_index: int | None = None
        self.dragging = False
        self.external = False
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def _menu(self, position: QPoint) -> None:
        widget = self.group.widget(self.tabAt(position))
        if widget is not None:
            # Older PySide6 stubs give the QPoint mapping overloads QPointF results.
            self.group.workspace.manager.tab_menu(widget, cast(QPoint, self.mapToGlobal(position)))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.tabAt(event.position().toPoint())
            self.pressed = self.group.widget(index)
            self.original_index = index
            self.start = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.pressed is None or not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        position = event.globalPosition().toPoint()
        assert self.start is not None
        if not self.dragging and (position - self.start).manhattanLength() < QApplication.startDragDistance():
            return
        self.dragging = True
        self.group.workspace.manager.drag_bar = self
        if not self.external and self.rect().contains(event.position().toPoint()):
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.moveTab(self.group.indexOf(self.pressed), index)
        else:
            self.external = True
            self.group.workspace.manager.preview(position)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        widget = self.pressed
        external = self.external
        self.pressed = None
        self.dragging = False
        self.external = False
        manager = self.group.workspace.manager
        manager.clear_preview()
        manager.drag_bar = None
        super().mouseReleaseEvent(event)
        if widget is not None and external and event.button() == Qt.MouseButton.LeftButton:
            manager.drop(widget, event.globalPosition().toPoint())

    def cancel_drag(self) -> None:
        if self.pressed is not None:
            assert self.original_index is not None
            self.moveTab(self.group.indexOf(self.pressed), self.original_index)
        self.pressed = None
        self.dragging = False
        self.external = False
        self.group.workspace.manager.clear_preview()
        self.group.workspace.manager.drag_bar = None


class TabGroup(QTabWidget):
    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.setTabBar(TabBar(self))
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(lambda index: workspace.manager.editor.close_tab(self.widget(index)))
        self.currentChanged.connect(lambda _: workspace.manager.select_group(self))


class Workspace(QWidget):
    def __init__(self, manager: WorkspaceManager, host: Host) -> None:
        super().__init__(host)
        self.manager = manager
        self.host = host
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.root: QWidget = TabGroup(self)
        self.box.addWidget(self.root)
        self.active_group = self.root

    def groups(self) -> list[TabGroup]:
        def walk(widget: QWidget) -> Iterator[TabGroup]:
            if isinstance(widget, TabGroup):
                yield widget
            else:
                assert isinstance(widget, QSplitter)
                for index in range(widget.count()):
                    child = widget.widget(index)
                    assert child is not None
                    yield from walk(child)
        return list(walk(self.root))

    def split(self, group: TabGroup, edge: str) -> TabGroup:
        orientation = Qt.Orientation.Horizontal if edge in ('left', 'right') else Qt.Orientation.Vertical
        parent = group.parentWidget()
        index = parent.indexOf(group) if isinstance(parent, QSplitter) else None
        sizes = parent.sizes() if isinstance(parent, QSplitter) else None
        split = QSplitter(orientation, self)
        split.setChildrenCollapsible(False)
        if index is None:
            self.box.replaceWidget(group, split)
            self.root = split
        else:
            assert isinstance(parent, QSplitter)
            parent.replaceWidget(index, split)
        new = TabGroup(self)
        for widget in ((new, group) if edge in ('left', 'top') else (group, new)):
            split.addWidget(widget)
        split.setSizes([1, 1])
        if sizes is not None:
            assert isinstance(parent, QSplitter)
            parent.setSizes(sizes)
        return new

    def clean(self) -> None:
        for group in self.groups():
            if group.count() or group is self.root:
                continue
            parent = group.parentWidget()
            group.setParent(None)
            group.deleteLater()
            while isinstance(parent, QSplitter) and parent.count() == 1:
                survivor = parent.widget(0)
                assert survivor is not None
                outer = parent.parentWidget()
                if isinstance(outer, QSplitter):
                    sizes = outer.sizes()
                    outer.replaceWidget(outer.indexOf(parent), survivor)
                    outer.setSizes(sizes)
                else:
                    self.box.replaceWidget(parent, survivor)
                    self.root = survivor
                    survivor.setParent(self)
                parent.setParent(None)
                parent.deleteLater()
                parent = outer
        groups = self.groups()
        if self.active_group not in groups:
            self.active_group = groups[0]
        if self.host is not self.manager.editor and not any(group.count() for group in groups):
            self.host.close()


class DetachedWindow(QMainWindow):
    snap_action: QAction

    def __init__(self, manager: WorkspaceManager, number: int) -> None:
        super().__init__(manager.editor, Qt.WindowType.Window)
        self.manager = manager
        self.number = number
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.workspace = Workspace(manager, self)
        self.setCentralWidget(self.workspace)
        self.resize(900, 700)
        manager.editor._create_menus(self)
        self.statusBar().showMessage(manager.editor.statusBar().currentMessage())

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.manager.shutting_down and self in self.manager.windows:
            self.manager.return_window(self)
        event.accept()


class WorkspaceManager(QObject):
    activated = Signal(object)

    def __init__(self, editor: MainWindow) -> None:
        super().__init__(editor)
        self.editor = editor
        self.windows: list[DetachedWindow] = []
        self.active_host: Host = editor
        self.active_view: QWidget | None = None
        self.shutting_down = False
        self.moving = False
        self.drag_bar: TabBar | None = None
        self.window_focus: QWidget | None = None
        self.rubber_band: QRubberBand | None = None
        self.next_window = 1
        editor.workspace = Workspace(self, editor)
        editor.setCentralWidget(editor.workspace)
        application = QApplication.instance()
        assert isinstance(application, QApplication)
        application.focusChanged.connect(self._focus_changed)
        application.installEventFilter(self)

    def hosts(self) -> list[Host]:
        return [self.editor, *self.windows]

    def group_of(self, widget: QWidget | None) -> TabGroup | None:
        if widget is None:
            return None
        parent = widget.parentWidget()
        while parent is not None and not isinstance(parent, TabGroup):
            parent = parent.parentWidget()
        return parent

    def current_widget(self, host: Host | None = None) -> QWidget | None:
        return (host or self.active_host).workspace.active_group.currentWidget()

    def select_group(self, group: TabGroup) -> None:
        if self.moving or self.shutting_down:
            return
        group.workspace.active_group = group
        if self.active_host is group.workspace.host and self.active_view is group.currentWidget():
            return
        self.active_host = group.workspace.host
        self.active_view = group.currentWidget()
        self.activated.emit(self.active_view)

    def _focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        restored = new is self.window_focus
        self.window_focus = None
        group = self.group_of(new)
        if not restored and group is not None and group.workspace.manager is self:
            self.select_group(group)

    def shutdown(self) -> None:
        self.shutting_down = True
        self.clear_preview()
        application = QApplication.instance()
        assert isinstance(application, QApplication)
        application.focusChanged.disconnect(self._focus_changed)
        application.removeEventFilter(self)
        for host in list(self.windows):
            host.close()
        self.windows.clear()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(watched, QWidget):
            group = self.group_of(watched)
            if group is not None and group.workspace.manager is self:
                if event.type() == QEvent.Type.FocusIn:
                    assert isinstance(event, QFocusEvent)
                    if event.reason() == Qt.FocusReason.ActiveWindowFocusReason:
                        # Restoring a window's previous focus must not retarget
                        # Properties when the user is clicking the inspector.
                        self.window_focus = watched
                elif event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress):
                    self.select_group(group)
        if event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            if event.key() == Qt.Key.Key_Escape and self.drag_bar is not None:
                self.drag_bar.cancel_drag()
                return True
        return False

    def activate(self, widget: QWidget) -> None:
        group = self.group_of(widget)
        assert group is not None
        group.setCurrentWidget(widget)
        self.select_group(group)
        host = group.workspace.host
        if host.isVisible():
            if host.isMinimized():
                host.showNormal()
            else:
                host.raise_()
            host.activateWindow()
        widget.setFocus()

    def add(self, widget: QWidget, host: Host | None = None) -> TabGroup:
        group = (host or self.active_host).workspace.active_group
        group.addTab(widget, '')
        return group

    def move(self, widget: QWidget, target: TabGroup, index: int | None = None, *, clean: bool = True) -> None:
        source = self.group_of(widget)
        assert source is not None
        source_index = source.indexOf(widget)
        if source is target and index is not None and source_index < index:
            index -= 1
        title = source.tabText(source_index)
        tooltip = source.tabToolTip(source_index)
        self.clear_preview()
        self.moving = True
        try:
            source.removeTab(source_index)
            index = target.count() if index is None else min(index, target.count())
            target.insertTab(index, widget, title)
            target.setTabToolTip(index, tooltip)
            target.setCurrentWidget(widget)
            if clean:
                source.workspace.clean()
        finally:
            self.moving = False
        self.activate(widget)
        self.editor._update_titles()

    def split(self, widget: QWidget | None, edge: str, group: TabGroup | None = None) -> None:
        if widget is None:
            return
        group = group or self.group_of(widget)
        assert group is not None
        if group is self.group_of(widget) and group.count() == 1:
            return
        self.move(widget, group.workspace.split(group, edge))

    def detach(self, widget: QWidget | None, position: QPoint | None = None) -> DetachedWindow | None:
        if widget is None:
            return None
        host = DetachedWindow(self, self.next_window)
        self.next_window += 1
        self.windows.append(host)
        if position is not None:
            host.move(position)
        self.move(widget, host.workspace.active_group)
        host.show()
        self.activate(widget)
        return host

    def return_window(self, host: DetachedWindow) -> None:
        target = self.editor.workspace.active_group
        widgets = [group.widget(index) for group in host.workspace.groups() for index in range(group.count())]
        self.windows.remove(host)
        for widget in widgets:
            assert widget is not None
            self.move(widget, target, clean=False)
        if self.active_host is host:
            self.select_group(target)

    def return_tab(self, widget: QWidget | None) -> None:
        assert widget is not None
        self.move(widget, self.editor.workspace.active_group)

    def tab_menu(self, widget: QWidget, position: QPoint) -> None:
        source = self.group_of(widget)
        assert source is not None
        menu = QMenu(source)
        for label, edge in (('Split Right', 'right'), ('Split Below', 'bottom')):
            def split(edge: str = edge) -> None:
                self.split(widget, edge)
            menu.addAction(label, split).setEnabled(source.count() > 1)
        move = menu.addMenu('Move To')
        for host in self.hosts():
            if host is self.editor:
                name = 'Main Window'
            else:
                assert isinstance(host, DetachedWindow)
                name = f'Window {host.number}'
            for number, group in enumerate(host.workspace.groups(), 1):
                if group is not source:
                    def move_to(group: TabGroup = group) -> None:
                        self.move(widget, group)
                    move.addAction(f'{name}, Pane {number}', move_to)
        move.setEnabled(not move.isEmpty())
        menu.addAction('Detach', lambda: self.detach(widget, position))
        if source.workspace.host is not self.editor:
            menu.addAction('Return to Main Window', lambda: self.return_tab(widget))
        menu.addSeparator()
        menu.addAction('Close Tab', lambda: self.editor.close_tab(widget))
        menu.exec(position)
        menu.deleteLater()

    def host_at(self, position: QPoint) -> Host | None:
        host = QApplication.topLevelAt(position)
        if host is not None:
            return cast(Host, host) if host in self.hosts() else None
        # Some Qt platform plugins cannot identify a top-level window at a
        # global point. Prefer the active host, then the most recently opened.
        candidates = [self.active_host, *reversed(self.hosts())]
        return next((host for host in candidates if host.isVisible() and host.frameGeometry().contains(position)),
                    None)

    def destination(self, position: QPoint) -> tuple[TabGroup | None, str | None, int | None]:
        host = self.host_at(position)
        if host is None:
            return None, None, None
        for group in host.workspace.groups():
            local = cast(QPoint, group.mapFromGlobal(position))
            if not group.rect().contains(local):
                continue
            bar = group.tabBar()
            bar_position = cast(QPoint, bar.mapFromGlobal(position))
            if bar.rect().contains(bar_position):
                index = bar.tabAt(bar_position)
                return group, None, group.count() if index < 0 else index
            rect = group.rect()
            x, y = local.x() / max(1, rect.width()), local.y() / max(1, rect.height())
            distance, edge = min((x, 'left'), (1 - x, 'right'), (y, 'top'), (1 - y, 'bottom'))
            return group, edge if distance < 0.25 else None, None
        return None, None, None

    def preview(self, position: QPoint) -> None:
        self.clear_preview()
        group, edge, index = self.destination(position)
        if group is None:
            return
        rect = group.rect()
        if edge in ('left', 'right'):
            width = rect.width() // 2
            rect = QRect(0 if edge == 'left' else width, 0, width, rect.height())
        elif edge in ('top', 'bottom'):
            height = rect.height() // 2
            rect = QRect(0, 0 if edge == 'top' else height, rect.width(), height)
        elif index is not None:
            bar = group.tabBar()
            x = bar.tabRect(index).left() if index < group.count() else bar.width()
            rect = QRect(bar.mapTo(group, QPoint(x, 0)), bar.size())
            rect.setWidth(3)
        self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, group)
        self.rubber_band.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.rubber_band.setGeometry(rect)
        self.rubber_band.show()

    def clear_preview(self) -> None:
        if self.rubber_band is not None:
            self.rubber_band.hide()
            self.rubber_band.deleteLater()
            self.rubber_band = None

    def drop(self, widget: QWidget, position: QPoint) -> None:
        group, edge, index = self.destination(position)
        if group is not None:
            if edge is None:
                self.move(widget, group, index)
            else:
                self.split(widget, edge, group)
        elif self.host_at(position) is None:
            self.detach(widget, position)
