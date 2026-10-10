"""Node graph view of one scope.

The view draws a GraphModel and reports gestures as signals; it never edits
text itself. Items keep their identity across models by outline ID, so
selection survives refreshes.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene, QGraphicsView, QLabel, QStyle,
    QVBoxLayout, QWidget,
)

from wirely.compiler.outline import Connection

from . import layout as _layout
from .graph_model import Unresolved, layout_edges
from .grid import STEP, from_pixels, size

PADDING = 8
ROW_HEIGHT = STEP
SOCKET_RADIUS = 5
MIN_WIDTH = 110
COLUMN_GAP = 24
# Zoom per eighth of a degree of wheel rotation; one notch of 120 zooms by about 20%.
ZOOM_RATE = 0.0015

_KIND_COLORS = {
    'node': '#4a78b0', 'state': '#a0522d', 'temporary': '#6b8e23', 'constant': '#7b68a6',
    'parameter': '#7b68a6', 'input': '#2e8b8b', 'output': '#2e8b8b', 'procedure': '#b07a2a',
    'unresolved': '#999999',
}
_PORT_COLORS = {'value': '#3c8dd8', 'event': '#e0a020', 'resource': '#9a5cc6', None: '#999999'}
_ERROR_COLOR = QColor('#e0301e')


class SocketGraphic(QGraphicsItem):
    def __init__(self, item, socket, side, row):
        super().__init__(item)
        self.item = item
        self.socket = socket
        self.side = side
        self.row = row
        self.highlight = None
        self.setAcceptHoverEvents(True)
        self.setToolTip(_socket_tooltip(socket))

    def boundingRect(self):
        radius = SOCKET_RADIUS + 3
        return QRectF(-radius, -radius, 2 * radius, 2 * radius)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(_PORT_COLORS.get(self.socket.kind, '#999999'))
        pen = QPen(_ERROR_COLOR if not self.socket.resolved else color.darker(140), 1.5)
        if self.highlight == 'candidate':
            pen = QPen(QColor('#30b030'), 3)
        elif self.highlight == 'refused':
            pen = QPen(_ERROR_COLOR, 3)
        painter.setPen(pen)
        painter.setBrush(color if self.socket.resolved else QColor('#dddddd'))
        if self.socket.is_list:
            painter.drawRect(QRectF(-SOCKET_RADIUS, -SOCKET_RADIUS, 2 * SOCKET_RADIUS, 2 * SOCKET_RADIUS))
        else:
            painter.drawEllipse(QPointF(0, 0), SOCKET_RADIUS, SOCKET_RADIUS)

    def set_highlight(self, highlight):
        if self.highlight != highlight:
            self.highlight = highlight
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.scene().interactive:
            self.scene().start_connection(self, event.scenePos())
            event.accept()
            return
        super().mousePressEvent(event)


class ItemGraphic(QGraphicsObject):
    def __init__(self, model_item, fonts):
        super().__init__()
        self.edges = []
        self.sockets = {}
        self.fonts = fonts
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.set_model(model_item)

    def set_model(self, model_item):
        self.prepareGeometryChange()
        self.model = model_item
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not isinstance(model_item.id, Unresolved))
        title, subtitle, label = self.fonts
        rows = max(len(model_item.inputs), len(model_item.outputs))
        left = max((label.horizontalAdvance(socket.label) for socket in model_item.inputs), default=0)
        right = max((label.horizontalAdvance(socket.label) for socket in model_item.outputs), default=0)
        width = max(MIN_WIDTH, title.horizontalAdvance(model_item.title) + 2 * PADDING + 16,
                    subtitle.horizontalAdvance(model_item.subtitle) + 2 * PADDING,
                    left + right + 2 * PADDING + COLUMN_GAP)
        self.width = size(width / STEP) * STEP
        self.header = size((PADDING + title.height() + subtitle.height() + 4) / STEP) * STEP
        self.height = self.header + (rows + 1) * STEP
        for socket in self.sockets.values():
            socket.setParentItem(None)
            if socket.scene() is not None:
                socket.scene().removeItem(socket)
        self.sockets = {}
        for side, sockets, x in (('inputs', model_item.inputs, 0), ('outputs', model_item.outputs, self.width)):
            for row, socket in enumerate(sockets):
                graphic = SocketGraphic(self, socket, side, row)
                graphic.setPos(x, self.header + (row + 1) * STEP)
                self.sockets[(side, socket.key)] = graphic
        messages = [diagnostic.message for diagnostic in model_item.diagnostics]
        self.setToolTip('\n\n'.join(messages) if messages else '')
        self.update()

    def socket_position(self, side, key):
        socket = self.sockets.get((side, key))
        if socket is None:
            return self.mapToScene(QPointF(0 if side == 'inputs' else self.width, self.header))
        return socket.scenePos()

    def boundingRect(self):
        return QRectF(-SOCKET_RADIUS - 3, -12, self.width + 2 * SOCKET_RADIUS + 6, self.height + 15)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.scene().palette()
        model_item = self.model
        rect = QRectF(0, 0, self.width, self.height)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        border = palette.color(QPalette.ColorRole.Highlight) if selected else QColor(
            _KIND_COLORS.get(model_item.kind, '#888888'))
        if model_item.diagnostics:
            border = _ERROR_COLOR if not selected else border
        painter.setPen(QPen(border, 2.5 if selected else 1.5,
                            Qt.PenStyle.DashLine if model_item.kind == 'unresolved' else Qt.PenStyle.SolidLine))
        painter.setBrush(palette.color(QPalette.ColorRole.Base))
        painter.drawRoundedRect(rect, 6, 6)
        header = QRectF(0, 0, self.width, self.header - 2)
        color = QColor(_KIND_COLORS.get(model_item.kind, '#888888'))
        color.setAlpha(45)
        path = QPainterPath()
        path.addRoundedRect(header, 6, 6)
        painter.fillPath(path, color)

        title, subtitle, label = self.fonts
        text_color = palette.color(QPalette.ColorRole.Text)
        painter.setPen(text_color)
        painter.setFont(self.scene().title_font)
        painter.drawText(QRectF(PADDING, PADDING / 2, self.width - 2 * PADDING, title.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, model_item.title)
        painter.setFont(self.scene().subtitle_font)
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(QRectF(PADDING, PADDING / 2 + title.height(), self.width - 2 * PADDING, subtitle.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         subtitle.elidedText(model_item.subtitle, Qt.TextElideMode.ElideRight,
                                             self.width - 2 * PADDING))
        painter.setFont(self.scene().label_font)
        for side, sockets in (('inputs', model_item.inputs), ('outputs', model_item.outputs)):
            for row, socket in enumerate(sockets):
                painter.setPen(text_color if socket.resolved else _ERROR_COLOR)
                top = self.header + (row + 1) * STEP - ROW_HEIGHT / 2
                alignment = Qt.AlignmentFlag.AlignLeft if side == 'inputs' else Qt.AlignmentFlag.AlignRight
                painter.drawText(QRectF(PADDING, top, self.width - 2 * PADDING, ROW_HEIGHT),
                                 alignment | Qt.AlignmentFlag.AlignVCenter, socket.label)
        if model_item.diagnostics:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ERROR_COLOR)
            painter.drawEllipse(QPointF(self.width - 2, -2), 8, 8)
            painter.setPen(QColor('white'))
            painter.setFont(self.scene().title_font)
            painter.drawText(QRectF(self.width - 10, -10, 16, 16), Qt.AlignmentFlag.AlignCenter, '!')

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            # Round a drag's displacement so half-step ties preserve group spacing.
            positions = getattr(self.scene(), '_press_positions', None)
            origin = positions.get(self.model.id, QPointF()) if positions else QPointF()
            delta = value - origin
            x, y = from_pixels(delta.x(), delta.y())
            return origin + QPointF(x * STEP, y * STEP)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update_path()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        self.scene().item_activated.emit(self.model)
        event.accept()


class EdgeGraphic(QGraphicsPathItem):
    def __init__(self, edge, source, target):
        super().__init__()
        self.source = source
        self.target = target
        self.set_model(edge)
        self.setZValue(-1)

    def set_model(self, edge):
        self.edge = edge
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, edge.kind == 'connect')
        messages = [diagnostic.message for diagnostic in edge.diagnostics]
        self.setToolTip('\n\n'.join(messages) if messages else f'{edge.source[1] or ""} → {edge.target[1] or ""}')
        self.update_path()

    def update_path(self):
        start = self.source.socket_position('outputs', self.edge.source[1])
        end = self.target.socket_position('inputs', self.edge.target[1])
        self.setPath(edge_path(start, end, self.source, self.target))

    def paint(self, painter, option, widget=None):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        socket = self.source.sockets.get(('outputs', self.edge.source[1]))
        color = QColor(_PORT_COLORS.get(socket.socket.kind if socket else None, '#999999'))
        if self.edge.diagnostics:
            color = _ERROR_COLOR
        if selected:
            color = self.scene().palette().color(QPalette.ColorRole.Highlight)
        pen = QPen(color, 3 if selected else 1.8)
        if self.edge.kind == 'reference':
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

    def shape(self):
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())


def edge_path(start, end, source=None, target=None):
    """A cubic curve leaving and entering horizontally; backwards edges go around below."""
    path = QPainterPath(start)
    dx = end.x() - start.x()
    if dx >= 30 or source is None or target is None:
        handle = max(abs(dx) / 2, 40)
        path.cubicTo(start + QPointF(handle, 0), end - QPointF(handle, 0), end)
        return path
    bottom = max(source.sceneBoundingRect().bottom(), target.sceneBoundingRect().bottom()) + 25
    path.cubicTo(start + QPointF(60, 0), QPointF(start.x() + 60, bottom), QPointF(start.x(), bottom))
    path.lineTo(QPointF(end.x(), bottom))
    path.cubicTo(QPointF(end.x() - 60, bottom), end - QPointF(60, 0), end)
    return path


class GraphScene(QGraphicsScene):
    """Scene of one scope's graph.

    Signals carry outline IDs and connect references as in connect statements.
    """

    moved = Signal(object)
    connect_requested = Signal(str, str)
    reconnect_requested = Signal(object, str)
    disconnect_requested = Signal(object)
    item_activated = Signal(object)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title_font = QFont()
        self.title_font.setBold(True)
        self.subtitle_font = QFont()
        self.subtitle_font.setPointSizeF(max(6.0, self.subtitle_font.pointSizeF() - 1.5))
        self.label_font = QFont()
        self.label_font.setPointSizeF(max(6.0, self.label_font.pointSizeF() - 1))
        self.fonts = (QFontMetricsF(self.title_font), QFontMetricsF(self.subtitle_font),
                      QFontMetricsF(self.label_font))
        self.items_by_id = {}
        self.edge_graphics = []
        self.model = None
        self.interactive = True
        self.check_connection = None
        self._drag = None
        self._press_positions = None

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        painter.save()
        pen = QPen(QColor('#d3d3d3'), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        pen.setCosmetic(True)
        painter.setPen(pen)
        left = math.ceil(rect.left() / STEP)
        right = math.floor(rect.right() / STEP)
        top = math.ceil(rect.top() / STEP)
        bottom = math.floor(rect.bottom() / STEP)
        for y in range(top, bottom + 1):
            painter.drawPoints([QPointF(x * STEP, y * STEP) for x in range(left, right + 1)])
        painter.restore()

    def set_model(self, model):
        """Show a model, reusing the graphics of items with the same ID."""
        self.model = model
        old = self.items_by_id
        self.items_by_id = {}
        for model_item in model.items:
            graphic = old.pop(model_item.id, None)
            if graphic is None:
                graphic = ItemGraphic(model_item, self.fonts)
                self.addItem(graphic)
            else:
                graphic.set_model(model_item)
            graphic.edges = []
            self.items_by_id[model_item.id] = graphic
        for graphic in old.values():
            self.removeItem(graphic)
        for graphic in self.edge_graphics:
            self.removeItem(graphic)
        self.edge_graphics = []
        for edge in model.edges:
            source = self.items_by_id.get(edge.source_item)
            target = self.items_by_id.get(edge.target_item)
            if source is None or target is None:
                continue
            graphic = EdgeGraphic(edge, source, target)
            self.addItem(graphic)
            source.edges.append(graphic)
            target.edges.append(graphic)
            self.edge_graphics.append(graphic)

        positions = _layout.layout(
            [_layout.LayoutItem(model_item.id, graphic.width / STEP, graphic.height / STEP, model_item.position,
                                'first' if model_item.kind == 'input' else
                                'last' if model_item.kind == 'output' else None)
             for model_item, graphic in ((item, self.items_by_id[item.id]) for item in model.items)],
            layout_edges(model))
        for item_id, (x, y) in positions.items():
            self.items_by_id[item_id].setPos(x * STEP, y * STEP)
        for graphic in self.edge_graphics:
            graphic.update_path()

    def selected_ids(self):
        ids = []
        for graphic in self.selectedItems():
            if isinstance(graphic, ItemGraphic) and not isinstance(graphic.model.id, Unresolved):
                ids.append(graphic.model.id)
            elif isinstance(graphic, EdgeGraphic) and graphic.edge.id is not None:
                ids.append(graphic.edge.id)
        return ids

    def select_id(self, element_id):
        """Select the item or edge with an ID, or nothing for None."""
        self.blockSignals(True)
        try:
            self.clearSelection()
            graphic = self.items_by_id.get(element_id)
            if graphic is None and isinstance(element_id, Connection):
                graphic = next((edge for edge in self.edge_graphics if edge.edge.id == element_id), None)
            if graphic is not None:
                graphic.setSelected(True)
        finally:
            self.blockSignals(False)
        return graphic

    def start_connection(self, socket, position):
        """Start dragging a connection from a socket.

        Dragging from an input that has exactly one connection, and is not a
        multi input, picks up that connection.
        """
        model_item = socket.item.model
        picked = None
        if socket.side == 'inputs' and not socket.socket.is_list:
            connected = [edge for edge in socket.item.edges
                         if edge.target is socket.item and edge.edge.target[1] == socket.socket.key]
            if len(connected) == 1 and connected[0].edge.kind == 'connect':
                picked = connected[0]
            elif connected:
                self.status.emit('Only connections from connect statements can be moved.')
                return
        if picked is not None:
            anchor = picked.source.sockets.get(('outputs', picked.edge.source[1]))
            if anchor is None:
                return
            picked.setVisible(False)
        else:
            anchor = socket
        if isinstance(model_item.id, Unresolved) and picked is None:
            return
        path = QGraphicsPathItem()
        path.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight), 2, Qt.PenStyle.DashLine))
        path.setZValue(10)
        self.addItem(path)
        self._drag = _ConnectionDrag(anchor, picked, path)
        self._update_drag(position)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self._update_drag(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self._drag is None:
            self._press_positions = {graphic.model.id: graphic.pos() for graphic in self.selectedItems()
                                     if isinstance(graphic, ItemGraphic)}

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            drag = self._drag
            self._drag = None
            self._finish_drag(drag, event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if self._press_positions:
            moved = {}
            for graphic in self.selectedItems():
                if isinstance(graphic, ItemGraphic) and graphic.model.id in self._press_positions \
                        and graphic.pos() != self._press_positions[graphic.model.id]:
                    moved[graphic.model.id] = from_pixels(graphic.pos().x(), graphic.pos().y())
            self._press_positions = None
            if moved:
                self.moved.emit(moved)

    def _socket_at(self, position):
        for graphic in self.items(position):
            if isinstance(graphic, SocketGraphic):
                return graphic
        return None

    def _check(self, drag, socket):
        """Return (source, target, check) for dropping a drag on a socket, or None."""
        anchor = drag.anchor
        if socket is None or socket is anchor or socket.side == anchor.side:
            return None
        if anchor.side == 'outputs':
            source, target = anchor.socket.reference, socket.socket.reference
        else:
            source, target = socket.socket.reference, anchor.socket.reference
        replacing = drag.picked.edge.id if drag.picked is not None else None
        key = (source, target, replacing)
        if key not in drag.checks:
            drag.checks[key] = self.check_connection(source, target, replacing)
        return source, target, drag.checks[key]

    def _update_drag(self, position):
        drag = self._drag
        start = drag.anchor.scenePos()
        socket = self._socket_at(position)
        if drag.hovered is not None and drag.hovered is not socket:
            drag.hovered.set_highlight(None)
        drag.hovered = socket
        result = self._check(drag, socket)
        if result is not None:
            _, _, check = result
            socket.set_highlight('candidate' if check.allowed else 'refused')
            self.status.emit('' if check.allowed else check.message)
            position = socket.scenePos()
        if drag.anchor.side == 'outputs':
            drag.path.setPath(edge_path(start, position))
        else:
            drag.path.setPath(edge_path(position, start))

    def _finish_drag(self, drag, position):
        self.removeItem(drag.path)
        if drag.hovered is not None:
            drag.hovered.set_highlight(None)
        if drag.picked is not None:
            drag.picked.setVisible(True)
        socket = self._socket_at(position)
        result = self._check(drag, socket)
        if result is None:
            if drag.picked is not None and socket is None:
                self.disconnect_requested.emit(drag.picked.edge.id)
            return
        source, target, check = result
        if not check.allowed:
            self.status.emit(check.message)
            return
        if drag.picked is not None:
            if socket is not drag.picked.target.sockets.get(('inputs', drag.picked.edge.target[1])):
                self.reconnect_requested.emit(drag.picked.edge.id, target)
            return
        self.connect_requested.emit(source, target)


class _ConnectionDrag:
    def __init__(self, anchor, picked, path):
        self.anchor = anchor
        self.picked = picked
        self.path = path
        self.hovered = None
        self.checks = {}


class GraphView(QGraphicsView):
    """Zoom with the wheel, pan with the middle mouse button, select with a rubber band."""

    context_menu = Signal(object, QPointF)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self._pan = None

    def wheelEvent(self, event):
        # Some touchpads only report pixel deltas.
        delta = event.angleDelta().y() or event.pixelDelta().y()
        factor = math.exp(ZOOM_RATE * delta)
        if 0.15 <= self.transform().m11() * factor <= 4:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            delta = event.position() - self._pan
            self._pan = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        position = self.mapToScene(event.pos())
        graphic = self.itemAt(event.pos())
        while graphic is not None and not isinstance(graphic, (ItemGraphic, EdgeGraphic)):
            graphic = graphic.parentItem()
        if graphic is not None and not graphic.isSelected():
            self.scene().clearSelection()
            graphic.setSelected(True)
        self.context_menu.emit(graphic, position)

    def fit(self):
        rect = self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self.setSceneRect(rect.united(QRectF(self.mapToScene(self.viewport().rect()).boundingRect())))


class GraphTab(QWidget):
    """A graph tab: the view and a banner explaining why it is read-only."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = tuple(path)
        self.model = None
        self.state = None
        self.graph_scene = GraphScene(self)
        self.view = GraphView(self.graph_scene, self)
        self.setFocusProxy(self.view)
        self.banner = QLabel(self)
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.banner.setStyleSheet('QLabel { background: #fff3cd; color: #5c4400; padding: 6px; }')
        self.banner.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.banner)
        layout.addWidget(self.view)
        self._fitted = False

    def update_graph(self, model, state, banner_html=''):
        """Show a model in state 'live', or keep the last one for 'frozen' and 'missing'."""
        self.state = state
        if state == 'live' and model is not None:
            self.model = model
            self.graph_scene.set_model(model)
            if not self._fitted:
                self._fitted = True
                self.view.fit()
                self.view.centerOn(self.graph_scene.itemsBoundingRect().center())
        live = state == 'live'
        self.graph_scene.interactive = live
        self.view.setInteractive(live)
        for graphic in self.graph_scene.items():
            if graphic.parentItem() is None:
                graphic.setOpacity(1.0 if live else 0.4)
        self.banner.setText(banner_html)
        self.banner.setVisible(bool(banner_html))
        if self.model is not None:
            self.view.fit()


def _socket_tooltip(socket):
    parts = [socket.reference]
    if socket.kind is not None:
        parts.append(f'{"multi " if socket.is_list else ""}{socket.kind} {socket.type_text or "?"}')
    if not socket.resolved:
        parts.append('(unresolved)')
    return '\n'.join(parts)
