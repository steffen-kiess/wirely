"""Source text editor with Wirely highlighting and diagnostics."""

import re

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QFontDatabase, QPainter, QPalette, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QToolTip, QWidget

from wirely.compiler import parser

from .sync import python_offset, qt_position

KEYWORDS = frozenset(terminal.pattern.value for terminal in parser.parser.terminals
                     if terminal.pattern.type == 'str' and terminal.pattern.value.isidentifier())

_TOKEN = re.compile(r'''
    (?P<comment>//.*)
  | (?P<block>/+\*)
  | (?P<raw_string>r\#?"[^"]*"\#?)
  | (?P<string>"(?:\\.|[^"\\])*"?)
  | (?P<attribute>\#!?\[)
  | (?P<number>\b(?:0x[0-9a-fA-F]*|0o[0-7]*|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\b)
  | (?P<word>r\#[A-Za-z_]\w*|[A-Za-z_]\w*)
''', re.VERBOSE)
_BLOCK_PART = re.compile(r'/+\*|\*+/')

_DIAGNOSTIC_COLORS = {'error': '#e0301e', 'syntax_error': '#e0301e', 'internal_error': '#e0301e',
                      'warning': '#d08000', 'hint': '#2f7fd0', 'info': '#2f7fd0'}


def _is_dark(palette):
    return palette.color(QPalette.ColorRole.Base).lightness() < 128


def _format(color, bold=False, italic=False):
    text_format = QTextCharFormat()
    text_format.setForeground(QColor(color))
    if bold:
        text_format.setFontWeight(700)
    text_format.setFontItalic(italic)
    return text_format


class Highlighter(QSyntaxHighlighter):
    """Highlights keywords, literals, attributes, and nested comments.

    The block state is the depth of nested block comments at the end of a line.
    """

    def __init__(self, document, dark):
        super().__init__(document)
        colors = (('#c792ea', '#c3e88d', '#f78c6c', '#7f8c98', '#82aaff', '#ffcb6b') if dark else
                  ('#7a1fa2', '#2e7d32', '#b5530f', '#6a737d', '#1f5fbf', '#8a6d00'))
        keyword, string, number, comment, attribute, type_name = colors
        self.formats = {
            'keyword': _format(keyword, bold=True),
            'string': _format(string),
            'raw_string': _format(string),
            'number': _format(number),
            'comment': _format(comment, italic=True),
            'attribute': _format(attribute),
            'type': _format(type_name),
        }

    def highlightBlock(self, text):
        depth = max(self.previousBlockState(), 0)
        position = 0
        if depth:
            position = self._comment(text, 0, depth)
        while position < len(text) and self.currentBlockState() <= 0:
            match = _TOKEN.search(text, position)
            if match is None:
                break
            kind = match.lastgroup
            start, end = match.span()
            if kind == 'block':
                position = self._comment(text, start, 0)
                continue
            if kind == 'attribute':
                close = text.find(']', end)
                end = len(text) if close < 0 else close + 1
                self.setFormat(start, end - start, self.formats['attribute'])
            elif kind == 'word':
                word = match.group()
                if word in KEYWORDS:
                    self.setFormat(start, end - start, self.formats['keyword'])
                elif word[0].isupper():
                    self.setFormat(start, end - start, self.formats['type'])
            else:
                self.setFormat(start, end - start, self.formats[kind])
            position = max(end, start + 1)
        if self.currentBlockState() < 0:
            self.setCurrentBlockState(0)

    def _comment(self, text, start, depth):
        """Format a block comment from start; return where it ends on this line."""
        position = start
        while True:
            match = _BLOCK_PART.search(text, position)
            if match is None:
                self.setFormat(start, len(text) - start, self.formats['comment'])
                self.setCurrentBlockState(depth)
                return len(text)
            depth += 1 if match.group().endswith('*') else -1
            position = match.end()
            if depth == 0:
                self.setFormat(start, position - start, self.formats['comment'])
                self.setCurrentBlockState(0)
                return position


class _LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """Editor of one Document.

    element_cursor is emitted with the Python offset of the cursor when it
    moves because of the user. graph_requested asks to show the element at
    the cursor in its graph.
    """

    element_cursor = Signal(int)
    graph_requested = Signal()

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document_record = document
        self.setDocument(document.text_document)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 2)
        self.highlighter = Highlighter(document.text_document, _is_dark(self.palette()))
        self.diagnostics = ()
        self._diagnostic_selections = []
        self._reveal_selections = []
        self._link_selections = []
        self._revealing = False
        self.line_numbers = _LineNumbers(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_line_numbers)
        self.cursorPositionChanged.connect(self._cursor_moved)
        self._update_margin()

    def set_diagnostics(self, diagnostics):
        """Underline the diagnostics of this file."""
        text = self.document_record.text()
        self.diagnostics = tuple(diagnostic for diagnostic in diagnostics if diagnostic.start is not None)
        selections = []
        for diagnostic in self.diagnostics:
            start, end = _diagnostic_range(text, diagnostic)
            selection = QTextEdit.ExtraSelection()
            selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            selection.format.setUnderlineColor(QColor(_DIAGNOSTIC_COLORS.get(diagnostic.kind, '#e0301e')))
            cursor = QTextCursor(self.document())
            cursor.setPosition(qt_position(text, start))
            cursor.setPosition(qt_position(text, end), QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selections.append(selection)
        self._diagnostic_selections = selections
        self._update_selections()

    def reveal(self, span):
        """Highlight a span given as Python offsets and scroll to it, without moving focus."""
        text = self.document_record.text()
        start, end = (qt_position(text, offset) for offset in span)
        selection = QTextEdit.ExtraSelection()
        highlight = self.palette().color(QPalette.ColorRole.Highlight)
        highlight.setAlpha(60)
        selection.format.setBackground(highlight)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, False)
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        selection.cursor = cursor
        self._reveal_selections = [selection]
        self._update_selections()
        self._revealing = True
        try:
            moved = QTextCursor(self.document())
            moved.setPosition(start)
            self.setTextCursor(moved)
            self.centerCursor()
        finally:
            self._revealing = False

    def set_link_highlight(self, span):
        """Mark a span given as Python offsets as linked to the diagram selection, or nothing for None.

        Neither the cursor nor the viewport moves.
        """
        self._link_selections = []
        if span is not None:
            text = self.document_record.text()
            start, end = (qt_position(text, min(offset, len(text))) for offset in span)
            selection = QTextEdit.ExtraSelection()
            highlight = self.palette().color(QPalette.ColorRole.Highlight)
            highlight.setAlpha(30)
            selection.format.setBackground(highlight)
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            self._link_selections = [selection]
        self._update_selections()

    def go_to(self, offset):
        text = self.document_record.text()
        cursor = QTextCursor(self.document())
        cursor.setPosition(qt_position(text, offset))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        first = menu.actions()[0] if menu.actions() else None
        show = QAction('Show in Diagram', menu)
        show.triggered.connect(self.graph_requested)
        menu.insertAction(first, show)
        menu.insertSeparator(first)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab and not event.modifiers():
            self.insertPlainText('  ')
            return
        super().keyPressEvent(event)

    def event(self, event):
        if event.type() == event.Type.ToolTip:
            cursor = self.cursorForPosition(event.pos())
            offset = python_offset(self.document_record.text(), cursor.position())
            text = self.document_record.text()
            messages = [diagnostic.message for diagnostic in self.diagnostics
                        if _diagnostic_range(text, diagnostic)[0] <= offset <= _diagnostic_range(text, diagnostic)[1]]
            if messages:
                QToolTip.showText(event.globalPos(), '\n\n'.join(messages), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def line_number_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance('9') * digits

    def paint_line_numbers(self, event):
        painter = QPainter(self.line_numbers)
        palette = self.palette()
        painter.fillRect(event.rect(), palette.color(QPalette.ColorRole.AlternateBase))
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                painter.drawText(0, top, self.line_numbers.width() - 6, height,
                                 Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        contents = self.contentsRect()
        self.line_numbers.setGeometry(QRect(contents.left(), contents.top(),
                                            self.line_number_width(), contents.height()))

    def _update_margin(self):
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_line_numbers(self, rect, dy):
        if dy:
            self.line_numbers.scroll(0, dy)
        else:
            self.line_numbers.update(0, rect.y(), self.line_numbers.width(), rect.height())

    def _update_selections(self):
        self.setExtraSelections(self._link_selections + self._diagnostic_selections + self._reveal_selections)

    def _cursor_moved(self):
        if self._revealing:
            return
        if self._reveal_selections:
            self._reveal_selections = []
            self._update_selections()
        self.element_cursor.emit(python_offset(self.document_record.text(), self.textCursor().position()))


def _diagnostic_range(text, diagnostic):
    start = min(diagnostic.start, len(text))
    end = diagnostic.end
    if end is None or end <= start:
        # Underline the token at the position, or one character.
        match = re.compile(r'\w+|\S').match(text, start)
        end = match.end() if match else min(start + 1, len(text))
    return start, min(end, len(text))
