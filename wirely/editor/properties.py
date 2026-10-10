"""Panel showing and editing the selected element."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from wirely.compiler.outline import Connection, Constant, Node, NodeType, Parameter, Port, Procedure, State, \
    Temporary

_RENAMEABLE = (Node, State, Temporary, Constant, Parameter, Port, Procedure, NodeType)


class PropertiesPanel(QScrollArea):
    """Edits of the panel are reported as signals with the element's ID."""

    rename_requested = Signal(object, str)
    node_type_requested = Signal(object, str)
    parameter_requested = Signal(object, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.target = None
        self._show_empty()

    def show_element(self, program, target, editable):
        """Show an element of the program, or nothing for None."""
        self.target = target
        records = program.outline.find(target) if target is not None else ()
        if len(records) != 1:
            self._show_empty()
            return
        record = records[0]
        widget = QWidget()
        form = QFormLayout(widget)
        if isinstance(target, Connection):
            form.addRow('Connection', QLabel(f'{record.source.text} → {record.target.text}'))
        else:
            name = record.name if record.name is not None else f'default {record.direction}'
            field = QLineEdit(name)
            renameable = isinstance(target, _RENAMEABLE) and not (isinstance(target, Port) and record.name is None)
            field.setReadOnly(not (editable and renameable))
            field.editingFinished.connect(lambda: self._changed(field, name, self.rename_requested, target))
            form.addRow('Name', field)
        if isinstance(target, Node):
            type_field = QLineEdit(record.type_text)
            type_field.setReadOnly(not editable)
            type_field.editingFinished.connect(
                lambda: self._changed(type_field, record.type_text, self.node_type_requested, target))
            form.addRow('Type', type_field)
            bindings = {binding.name: binding.value_text for binding in record.parameters}
            parameters = program.analysis.parameters(target) or ()
            names = [parameter.name for parameter in parameters]
            names += [name for name in bindings if name not in names]
            if names:
                form.addRow(QLabel('<b>Parameters</b>'))
            for parameter_name in names:
                parameter = next((entry for entry in parameters if entry.name == parameter_name), None)
                value = QLineEdit(bindings.get(parameter_name, ''))
                value.setReadOnly(not editable)
                if parameter is not None:
                    value.setPlaceholderText('optional' if parameter.optional else 'required')
                    value.setToolTip(str(parameter.type))
                value.editingFinished.connect(
                    lambda value=value, parameter_name=parameter_name: self._changed(
                        value, bindings.get(parameter_name, ''), self.parameter_requested, target, parameter_name))
                form.addRow(parameter_name, value)
        elif hasattr(record, 'type_text') and record.type_text is not None:
            form.addRow('Type', QLabel(record.type_text))
        for label, attribute in (('Value', 'value_text'), ('Initial', 'initial_text'), ('Default', 'default_text')):
            text = getattr(record, attribute, None)
            if text is not None:
                form.addRow(label, QLabel(text))
        if not editable:
            form.addRow(QLabel('<i>Read-only while the source does not parse.</i>'))
        self.setWidget(widget)

    def _changed(self, field, old, signal, target, *arguments):
        if field.parentWidget() is not self.widget():
            return
        text = field.text()
        if text.strip() != old.strip() and not field.isReadOnly():
            signal.emit(target, *arguments, text)

    def setWidget(self, widget):
        # Fields of a replaced widget report editingFinished when they lose focus.
        old = self.takeWidget()
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        super().setWidget(widget)

    def _show_empty(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel('<i>Nothing selected</i>'))
        layout.addStretch()
        self.setWidget(widget)
