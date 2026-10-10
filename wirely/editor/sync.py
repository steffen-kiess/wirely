"""Documents of the program files and their synchronization with the analysis."""

import os

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextDocumentLayout

from .program import Program

REFRESH_DELAY_MS = 200


class Document(QObject):
    """A program file; its QTextDocument holds the current text and undo history."""

    def __init__(self, filename, text, parent=None):
        super().__init__(parent)
        self.filename = filename
        self.uses_crlf = '\r\n' in text
        self.saved_text = text.replace('\r\n', '\n')
        self.text_document = QTextDocument(self)
        self.text_document.setDocumentLayout(QPlainTextDocumentLayout(self.text_document))
        self.text_document.setPlainText(self.saved_text)
        self.text_document.setModified(False)

    def text(self):
        return self.text_document.toPlainText()

    @property
    def is_modified(self):
        return self.text_document.isModified()

    def save(self):
        text = self.text()
        with open(self.filename, 'w', encoding='utf-8', newline='\r\n' if self.uses_crlf else '\n') as file:
            file.write(text)
        self.saved_text = text
        self.text_document.setModified(False)

    def replace_all(self, text):
        """Replace the whole text as one undoable step."""
        cursor = QTextCursor(self.text_document)
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text.replace('\r\n', '\n'))

    def apply(self, edits):
        """Apply TextEdits of this file as one undoable step."""
        text = self.text()
        cursor = QTextCursor(self.text_document)
        cursor.beginEditBlock()
        for edit in sorted(edits, key=lambda edit: (edit.start, edit.end), reverse=True):
            cursor.setPosition(qt_position(text, edit.start))
            cursor.setPosition(qt_position(text, edit.end), QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(edit.text)
        cursor.endEditBlock()


class Session(QObject):
    """The open program: its documents, the analysis, and edit application.

    refreshed is emitted after each analysis. external_change is emitted with
    a Document whose file changed on disk while it has unsaved changes.
    """

    refreshed = Signal()
    external_change = Signal(object)

    def __init__(self, filenames, parent=None):
        super().__init__(parent)
        self.documents = {}
        self._recent = []
        self._redo = []
        self._undoing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_DELAY_MS)
        self._timer.timeout.connect(self.refresh)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._file_changed)
        for filename in filenames:
            self._add(filename)
        self.program = Program(self.sources())

    def add_file(self, filename):
        filename = os.path.normpath(filename)
        if filename not in self.documents:
            self._add(filename)
            self.refresh()
        return self.documents[filename]

    def sources(self):
        return [(filename, document.text()) for filename, document in self.documents.items()]

    def refresh(self):
        self._timer.stop()
        self.program.refresh(self.sources())
        self.refreshed.emit()

    def shutdown(self):
        """Stop pending analysis and file notifications when the editor closes."""
        self._timer.stop()
        self._watcher.blockSignals(True)

    def apply(self, edits):
        """Apply TextEdits to the documents, one undo step per file, and refresh."""
        by_file = {}
        for edit in edits:
            by_file.setdefault(edit.filename, []).append(edit)
        for filename, file_edits in by_file.items():
            self.documents[filename].apply(file_edits)
        if by_file:
            self.refresh()

    def save(self, document):
        document.save()
        if document.filename not in self._watcher.files():
            self._watcher.addPath(document.filename)

    def undo(self, filenames):
        """Undo the most recent change of one of the files."""
        for filename in reversed(self._recent):
            document = self.documents[filename].text_document
            if filename in filenames and document.isUndoAvailable():
                self._undoing = True
                try:
                    document.undo()
                finally:
                    self._undoing = False
                self._redo.append(filename)
                self.refresh()
                return True
        return False

    def redo(self, filenames):
        """Redo the most recently undone change of one of the files."""
        for index in range(len(self._redo) - 1, -1, -1):
            filename = self._redo[index]
            document = self.documents[filename].text_document
            if filename in filenames and document.isRedoAvailable():
                del self._redo[index]
                self._undoing = True
                try:
                    document.redo()
                finally:
                    self._undoing = False
                self.refresh()
                return True
        return False

    def _add(self, filename):
        filename = os.path.normpath(filename)
        try:
            with open(filename, encoding='utf-8', newline='') as file:
                text = file.read()
        except FileNotFoundError:
            text = ''
        document = Document(filename, text, self)
        self.documents[filename] = document
        document.text_document.contentsChanged.connect(lambda filename=filename: self._changed(filename))
        if os.path.exists(filename):
            self._watcher.addPath(filename)

    def _changed(self, filename):
        if filename in self._recent:
            self._recent.remove(filename)
        self._recent.append(filename)
        if not self._undoing:
            self._redo.clear()
        self._timer.start()

    def _file_changed(self, filename):
        document = self.documents.get(filename)
        if document is None:
            return
        if os.path.exists(filename) and filename not in self._watcher.files():
            # Editors that replace files remove them from the watch list.
            self._watcher.addPath(filename)
        try:
            with open(filename, encoding='utf-8', newline='') as file:
                text = file.read().replace('\r\n', '\n')
        except OSError:
            return
        if text in (document.saved_text, document.text()):
            return
        if document.is_modified:
            self.external_change.emit(document)
            return
        self.reload(document, text)

    def reload(self, document, text=None):
        if text is None:
            with open(document.filename, encoding='utf-8', newline='') as file:
                text = file.read().replace('\r\n', '\n')
        document.replace_all(text)
        document.saved_text = text
        document.text_document.setModified(False)
        self.refresh()


def qt_position(text, offset):
    """Convert a Python string offset to a QTextDocument position (UTF-16 code units)."""
    return offset + sum(1 for character in text[:offset] if ord(character) > 0xFFFF)


def python_offset(text, position):
    """Convert a QTextDocument position to a Python string offset."""
    offset = 0
    units = 0
    while offset < len(text) and units < position:
        units += 2 if ord(text[offset]) > 0xFFFF else 1
        offset += 1
    return offset
