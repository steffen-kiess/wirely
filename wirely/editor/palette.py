"""Choosing node types and entering new declarations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget,
)

from wirely.compiler import editing
from wirely.compiler.analysis import NodeTypeEntry


class NodePalette(QDialog):
    """A filterable list of the node types available in a scope."""

    def __init__(self, entries: Iterable[NodeTypeEntry], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Add node')
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText('Filter node types')
        self.list = QListWidget(self)
        for entry in entries:
            item = QListWidgetItem(entry.type_text, self.list)
            item.setData(Qt.ItemDataRole.UserRole, entry.type_text)
            item.setToolTip(_describe(entry))
        self.list.setCurrentRow(0)
        layout = QVBoxLayout(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.list)
        self.filter.textChanged.connect(self._filter)
        self.filter.returnPressed.connect(self.accept)
        self.list.itemActivated.connect(self.accept)
        self.resize(420, 480)

    def selected_type(self) -> str | None:
        item = self.list.currentItem()
        return None if item is None or item.isHidden() else item.data(Qt.ItemDataRole.UserRole)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.filter.hasFocus():
            self.list.keyPressEvent(event)
            return
        super().keyPressEvent(event)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        first = None
        for row in range(self.list.count()):
            item = self.list.item(row)
            visible = all(word in item.text().lower() for word in words)
            item.setHidden(not visible)
            if visible and first is None:
                first = item
        if first is not None:
            self.list.setCurrentItem(first)


def _describe(entry: NodeTypeEntry) -> str:
    lines = [entry.type_text]
    for parameter in entry.parameters:
        optional = '?' if parameter.optional or parameter.default_text is not None else ''
        lines.append(f'parameter {parameter.name}{optional}: {parameter.type_text}')
    for port in entry.ports or ():
        name = port.name or f'default {port.direction}'
        lines.append(f'{port.direction} {name}: {"multi " if port.is_list else ""}{port.kind} {port.type}')
    return '\n'.join(lines)


class DeclarationDialog(QDialog):
    """Enter a state, binding, constant, parameter, or port declaration.

    kind is one of those names, or 'input' or 'output' for ports.
    """

    def __init__(self, kind: str, name: str, type_names: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(f'Add {kind}')
        form = QFormLayout(self)
        self.name = QLineEdit(name, self)
        form.addRow('Name', self.name)
        self.port_kind = None
        self.is_list = None
        if kind in ('input', 'output', 'binding'):
            self.port_kind = QComboBox(self)
            self.port_kind.addItems(['value', 'event', 'resource'])
            form.addRow('Kind', self.port_kind)
            self.is_list = QCheckBox('multi', self)
            if kind == 'input':
                form.addRow('', self.is_list)
        self.type = QComboBox(self)
        self.type.setEditable(True)
        self.type.addItems(type_names)
        self.type.setCurrentText('boolean' if 'boolean' in type_names else '')
        form.addRow('Type', self.type)
        self.value = None
        self.optional = None
        if kind in ('state', 'constant', 'parameter'):
            self.value = QLineEdit(self)
            label = {'state': 'Initial value', 'constant': 'Value', 'parameter': 'Default'}[kind]
            form.addRow(label, self.value)
        if kind == 'parameter':
            self.optional = QCheckBox('optional', self)
            form.addRow('', self.optional)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
                                   self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def declaration(self) -> str:
        """Return the declaration text; raises ValueError for invalid input."""
        name = self.name.text().strip()
        type_text = self.type.currentText().strip()
        value = self.value.text().strip() if self.value is not None else ''
        if self.kind == 'state':
            return editing.format_state(name, type_text, value)
        if self.kind == 'binding':
            assert self.port_kind is not None
            return editing.format_binding(name, self.port_kind.currentText(), type_text)
        if self.kind == 'constant':
            return editing.format_constant(name, type_text, value)
        if self.kind == 'parameter':
            assert self.optional is not None
            return editing.format_parameter(name, type_text, value or None, self.optional.isChecked())
        assert self.port_kind is not None and self.is_list is not None
        return editing.format_port(self.kind, name or None, self.port_kind.currentText(), type_text,
                                   self.is_list.isChecked())
