import re
from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QSize, QEvent, QRect,
)
from PySide6.QtGui import (
    QColor, QSyntaxHighlighter, QTextCharFormat, QFont, QFontMetrics,
    QFontDatabase, QTextCursor, QPainter, QPalette,
)
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QStackedWidget,
    QTabBar, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)


# ----------------------------------------------------------------------
# Elided label
# ----------------------------------------------------------------------
class ElidedLabel(QLabel):
    def __init__(self, text="", elide_mode: Qt.TextElideMode=Qt.ElideRight, parent=None):
        super().__init__(parent)
        self._text = text
        self._elide_mode = elide_mode
        self.setText(text)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def setText(self, text):
        self._text = text
        self.setToolTip(text)
        self.updateText()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateText()

    def updateText(self):
        metrics = QFontMetrics(self.font())
        # Elide the text to fit the current width
        m = self.contentsMargins()
        width = self.width() - m.left() - m.right()
        elided = metrics.elidedText(self._text, self._elide_mode, width)
        # Call the parent's setText to display the elided version
        super().setText(elided)

    def sizeHint(self):
        # Hint is based on the full text, not the elided display text: the
        # base QLabel hint shrinks as elision grows, so once a tight layout
        # elided the text the label would collapse to its minimum and stay
        # there (blank status) even after space became available again.
        metrics = QFontMetrics(self.font())
        m = self.contentsMargins()
        width = metrics.horizontalAdvance(self._text) + m.left() + m.right()
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self):
        # Return a very small width so the layout allows shrinking.
        return QSize(10, super().minimumSizeHint().height())


# ----------------------------------------------------------------------
# Resizing tab widget
# ----------------------------------------------------------------------
class ResizingTabWidget(QTabWidget):
    """A QTabWidget that sizes itself (only in height) to its current page.

    Pages report their preferred size via sizeHint(); this widget applies
    that height as BOTH preferred and maximum, so it grows and shrinks with
    the page instead of stretching to fill the leftover panel space. Width
    is left to the parent layout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabPosition(QTabWidget.South)
        self.currentChanged.connect(self._on_current_changed)
        self.tabBar().installEventFilter(self)

    def sizeHint(self):
        current = self.currentWidget()
        if current is not None:
            page_size = current.sizeHint()
            tabBar = self.tabBar()
            tabBar_height = 2 + (tabBar.sizeHint().height() if tabBar else 0)
            return QSize(0, page_size.height() + tabBar_height)
        return super().sizeHint()

    def updateGeometry(self):
        super().updateGeometry()
        self.setMaximumHeight(self.sizeHint().height())

    def _on_current_changed(self, index):
        # The page (and therefore this hint) changed.
        self.updateGeometry()

    def eventFilter(self, watched, event):
        # Intercept wheel events on the tab bar
        if watched is self.tabBar() and event.type() == QEvent.Type.Wheel:
            return True  # Consume the event, preventing tab switching
        return super().eventFilter(watched, event)


# ----------------------------------------------------------------------
# Wildcard syntax highlighter
# ----------------------------------------------------------------------
class WildcardSyntaxHighlighter(QSyntaxHighlighter):
    """Highlights * and ? in magenta."""

    def __init__(self, parent):
        super().__init__(parent)
        self._symbols_format = QTextCharFormat()
        self._symbols_format.setForeground(QColor("#0EA5E9"))  # symbols highlight
        self._wildcard_format = QTextCharFormat()
        self._wildcard_format.setForeground(QColor("#C026D3"))  # wildcard highlight

    def highlightBlock(self, text: str):
        symbols_pattern = r'[^\w\s]+'
        matches = re.finditer(symbols_pattern, text)
        for match in matches:
            self.setFormat(match.start(), match.end() - match.start(), self._symbols_format)

        wildcard_pattern = r'[?*]'
        matches = re.finditer(wildcard_pattern, text)
        for match in matches:
            self.setFormat(match.start(), match.end() - match.start(), self._wildcard_format)


# ----------------------------------------------------------------------
# Markdown syntax highlighter
# ----------------------------------------------------------------------
class MarkdownSyntaxHighlighter(QSyntaxHighlighter):
    """Highlights common markdown syntax in a plain text block.

    Delimiters are kept visible; formatting (bold / italic / monospace / underline)
    and colors are applied to the whole span to make pasted system prompts more readable.
    """

    def __init__(self, parent):
        super().__init__(parent)

        # --- text formats -------------------------------------------------
        self._heading_format = QTextCharFormat()
        self._heading_format.setForeground(QColor("#EA580C"))
        self._heading_format.setFontWeight(QFont.Bold)

        self._bold_format = QTextCharFormat()
        self._bold_format.setForeground(QColor("#D97706"))
        self._bold_format.setFontWeight(QFont.Bold)

        self._bold_italic_format = QTextCharFormat()
        self._bold_italic_format.setForeground(QColor("#E11D48"))
        self._bold_italic_format.setFontWeight(QFont.Bold)
        self._bold_italic_format.setFontItalic(True)

        self._italic_format = QTextCharFormat()
        self._italic_format.setForeground(QColor("#6D28D9"))
        self._italic_format.setFontItalic(True)

        self._code_format = QTextCharFormat()
        self._code_format.setForeground(QColor("#22C55E"))
        self._code_format.setBackground(QColor("#3C4551"))
        self._code_format.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        self._link_format = QTextCharFormat()
        self._link_format.setForeground(QColor("#2563EB"))
        self._link_format.setFontUnderline(True)

        self._marker_format = QTextCharFormat()
        self._marker_format.setForeground(QColor("#C026D3"))
        self._marker_format.setFontWeight(QFont.Bold)

        self._symbols_format = QTextCharFormat()
        self._symbols_format.setForeground(QColor("#0EA5E9"))

        # --- rules (applied in order; later rules override earlier ones) --
        self._rules = [
            (re.compile(r'[^\w\s]+'), self._symbols_format),
            (re.compile(r'^#{1,6}\s.*'), self._heading_format),
            (re.compile(r'^\s*```\s*.*'), self._code_format),
            (re.compile(r'`[^`]+`'), self._code_format),
            (re.compile(r'\[[^\]]+\]\([^)]+\)'), self._link_format),
            (re.compile(r'\*\*\*[^*]+\*\*\*'), self._bold_italic_format),
            (re.compile(r'^\s*>\s?'), self._marker_format),
            (re.compile(r'^\s*(?:[-*+]|\d+\.)\s'), self._marker_format),
            (re.compile(r'^\s*([-_*])\1{2,}\s*$'), self._marker_format),
            (re.compile(r'(?<!\*)\*(?!\*)[^*\n]+\*(?!\*)'), self._italic_format),
            (re.compile(r'(?<![_\w])_(?!_)[^_\n]+_(?!_)'), self._italic_format),
            (re.compile(r'\*\*[^*]+\*\*'), self._bold_format),
            (re.compile(r'__[^_]+__'), self._bold_format),
        ]

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


# ----------------------------------------------------------------------
# Caption syntax highlighter
# ----------------------------------------------------------------------
class CaptionSyntaxHighlighter(QSyntaxHighlighter):
    """Highlights trigger words in yellow and punctuation in cyan."""

    def __init__(self, parent):
        super().__init__(parent)
        self._patterns = []
        self._symbols_format = QTextCharFormat()
        self._symbols_format.setForeground(QColor("#0EA5E9"))
        self._trigger_format = QTextCharFormat()
        self._trigger_format.setForeground(QColor("#D97706"))
        self._trigger_format.setFontWeight(QFont.Bold)

    def set_highlight_regex(self, patterns: list):
        """Accept pre-compiled regex patterns."""
        self._patterns = patterns
        self.rehighlight()

    def highlightBlock(self, text: str):
        # Highlight any non-alphanumeric symbols
        symbols_pattern = r'[^\w\s]+'
        matches = re.finditer(symbols_pattern, text)
        for match in matches:
            self.setFormat(match.start(), match.end() - match.start(), self._symbols_format)

        # Highlight trigger patterns (regex)
        for pattern in self._patterns:
            matches = pattern.finditer(text)
            for match in matches:
                self.setFormat(match.start(), match.end() - match.start(), self._trigger_format)


# ----------------------------------------------------------------------
# Caption editor with line numbers
# ----------------------------------------------------------------------

class _CaptionLineNumberArea(QWidget):
    """Line number margin for CaptionEdit."""

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return self._editor.line_number_area_width()

    def paintEvent(self, event):
        self._editor._paint_line_numbers(event)


class CaptionEdit(QPlainTextEdit):
    """QPlainTextEdit with line numbers display."""
    saveRequested = Signal()
    navigateUp = Signal()
    navigateDown = Signal()
    _LINE_NUMBERS_PADDING = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        # Line numbers area
        self._line_number_area = _CaptionLineNumberArea(self)
        self._last_line_number_area_width = 0
        self._min_line_number_area_width = self.fontMetrics().horizontalAdvance("9") * 3 + self._LINE_NUMBERS_PADDING
        self.updateRequest.connect(self._update_line_number_area)
        self.document().contentsChanged.connect(self._update_line_number_area_width)
        self._update_line_number_area_width()

    def line_number_area_width(self):
        digits = 2  # +1 for extra padding
        max_number = max(1, self.blockCount())
        while max_number >= 10:
            max_number //= 10
            digits += 1
        numbers_width = self.fontMetrics().horizontalAdvance("9") * digits + self._LINE_NUMBERS_PADDING
        return max(numbers_width, self._min_line_number_area_width)

    def _update_line_number_area_width(self):
        width = self.line_number_area_width()
        if width != self._last_line_number_area_width:
            self._last_line_number_area_width = width
            self.setViewportMargins(width, 0, 0, 0)
            self._position_line_number_area(width)
        self._line_number_area.update()

    def _position_line_number_area(self, width):
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), width, cr.height())
        )

    def _update_line_number_area(self, rect, dy):
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height()
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_line_number_area(self.line_number_area_width())

    def _iter_numbered_lines(self, start_block: int = 0):
        """Yield (block, number) for non-empty blocks; number is 1-based among non-empty lines."""
        start = start_block
        number = 1
        b = self.document().firstBlock()
        while b.isValid() and b.blockNumber() < start:
            if b.text().strip():
                number += 1
            b = b.next()
        block = self.document().findBlockByNumber(start)
        while block.isValid():
            if block.text().strip():
                yield block, number
                number += 1
            block = block.next()

    def _paint_line_numbers(self, event):
        painter = QPainter(self._line_number_area)
        # Darkened palette colors so the gutter reads as a distinct margin
        palette = self.palette()
        bg_color = palette.color(QPalette.ColorRole.Window).darker(100)
        alt_color = palette.color(QPalette.ColorRole.Window).darker(110)
        painter.fillRect(event.rect(), bg_color)
        text_color = palette.color(QPalette.ColorRole.PlaceholderText)
        painter.setPen(text_color)

        offset = self.contentOffset()
        fm = self.fontMetrics()
        width = self._line_number_area.width()

        # If the topmost visible line is an empty line, we must start enumeration
        # from the preceding numbered block so its number is still visible (clamped
        # to the top of the viewport) on that empty line.
        first_visible = self.firstVisibleBlock()
        start_block_num = first_visible.blockNumber()
        if not first_visible.text().strip():
            prev = first_visible.previous()
            while prev.isValid() and not prev.text().strip():
                prev = prev.previous()
            if prev.isValid():
                start_block_num = prev.blockNumber()

        for block, number in self._iter_numbered_lines(start_block_num):
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > event.rect().bottom():
                break
            # The band runs to the top of the NEXT numbered line, so empty
            # lines belong to this line number (matching the delimiter lines)
            bottom = rect.bottom()
            nb = block.next()
            while nb.isValid() and not nb.text().strip():
                nb = nb.next()
            if nb.isValid():
                bottom = self.blockBoundingGeometry(nb).translated(offset).top()
            band = QRect(0, int(rect.y()), width, max(1, int(bottom) - int(rect.y())))
            if not band.intersects(event.rect()):
                continue
            # Alternate line background (subtle)
            if number % 2 == 0:
                painter.fillRect(band, alt_color)
            # Right-align line numbers with proper padding
            line = f"{number}"
            text_width = fm.horizontalAdvance(line)
            # Adjust vertical alignment to match text baseline
            # Wrapped blocks that start above the viewport get their number
            # clamped to the top of the viewport (full repaints only).
            # On scroll strips (partial updates), skip numbers whose top is
            # outside the update rect to avoid duplicates.
            if event.rect().top() == 0:
                top = max(int(rect.y()), 0)
            elif int(rect.y()) >= event.rect().top():
                top = int(rect.y())
            else:
                continue
            painter.drawText(width - text_width - 6, top + fm.ascent(), line)
        painter.end()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter) and modifiers == Qt.ControlModifier:
            self.saveRequested.emit()
            return
        elif key == Qt.Key_Up and modifiers == Qt.ControlModifier:
            self.navigateUp.emit()
            return
        elif key == Qt.Key_Down and modifiers == Qt.ControlModifier:
            self.navigateDown.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())

        base_color = self.palette().color(QPalette.ColorRole.Window)
        alt_color = base_color.darker(105)

        content_offset = self.contentOffset()

        first_visible = self.firstVisibleBlock()
        start_block_num = first_visible.blockNumber()
        if not first_visible.text().strip():
            prev = first_visible.previous()
            while prev.isValid() and not prev.text().strip():
                prev = prev.previous()
            if prev.isValid():
                start_block_num = prev.blockNumber()

        for block, number in self._iter_numbered_lines(start_block_num):
            rect = self.blockBoundingGeometry(block).translated(content_offset)
            if rect.top() > event.rect().bottom():
                break

            bottom = rect.bottom()
            nb = block.next()
            while nb.isValid() and not nb.text().strip():
                nb = nb.next()
            if nb.isValid():
                bottom = self.blockBoundingGeometry(nb).translated(content_offset).top()

            band = QRect(
                event.rect().left(),
                int(rect.y()),
                event.rect().width(),
                max(1, int(bottom) - int(rect.y()))
            )

            if not band.intersects(event.rect()):
                continue

            if number % 2 == 0:
                painter.fillRect(band, alt_color)

        painter.end()

        super().paintEvent(event)


# ----------------------------------------------------------------------
# Caption generation console
# ----------------------------------------------------------------------
class CaptionGenerationConsole(QTextEdit):
    """A read-only text edit for displaying generation output with color-coded text."""

    _COLORS = {
        "caption": None,  # default text color
        "reasoning": QColor("#2563EB"),
        "debug": QColor("#D97706"),
        "info": QColor("#22C55E"),
        "error": QColor("#DC2626"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Monospace font for consistent output display
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        # self.setStyleSheet("QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c; }")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor("#181000"))
        self.setPalette(palette)

    def append_output(self, text: str, level: str = "caption"):
        """Append colored text with auto-scroll."""
        # Check if the scrollbar is near the bottom, allow small tolerance
        vbar = self.verticalScrollBar()
        at_bottom = vbar.value() >= vbar.maximum() - 10

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if level != "caption" or text.strip():  # Apply formatting
            fmt = QTextCharFormat()
            color = self._COLORS.get(level)
            if color:
                fmt.setForeground(color)
            if level == "reasoning":
                fmt.setFontItalic(True)
            cursor.setCharFormat(fmt)
        cursor.insertText(text)

        # Keep scrolled to bottom
        if at_bottom:
            vbar.setValue(vbar.maximum())

    def append_html(self, html: str):
        """Append raw HTML with auto-scroll."""
        # Check if the scrollbar is near the bottom, allow small tolerance
        vbar = self.verticalScrollBar()
        at_bottom = vbar.value() >= vbar.maximum() - 10

        self.insertHtml(html)

        # Keep scrolled to bottom
        if at_bottom:
            vbar.setValue(vbar.maximum())


# ----------------------------------------------------------------------
# Caption generation panel
# ----------------------------------------------------------------------
class CaptionGenerationPanel(QWidget):
    """Bottom panel for caption generation: console and captioner settings.

    Pure content container — the console and the captioner URL/prompt edits
    live in ``stack``; ``tab_bar`` and the Generate/Append/Replace buttons are
    placed by MainWindow in the merged caption-mode bar (tab bar on the left,
    buttons on the right) and wired there, so the panel can be toggled without
    hiding the console or the captioner settings.
    """

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget(self)

        # Page 1: generation console
        self.console = CaptionGenerationConsole()
        self.stack.addWidget(self.console)

        # Page 2: captioner settings (moved out of the left config tabs)
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(2)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setColumnStretch(0, 1)

        self.captioner_url_label = QLabel("Captioner server URL")
        self.captioner_url_edit = QLineEdit()

        self.captioner_timeout_label = QLabel("Request timeout")
        self.captioner_timeout_spinbox = QSpinBox()
        self.captioner_timeout_spinbox.setSuffix(" sec")
        self.captioner_timeout_spinbox.setRange(1, 600)

        # Align heights in the grid row
        self.captioner_url_edit.setMinimumHeight(self.captioner_timeout_spinbox.sizeHint().height())

        grid.addWidget(self.captioner_url_label, 0, 0)
        grid.addWidget(self.captioner_url_edit, 1, 0)
        grid.addWidget(self.captioner_timeout_label, 0, 1)
        grid.addWidget(self.captioner_timeout_spinbox, 1, 1)

        self.captioner_prompt_label = QLabel("Captioner prompt")
        self.captioner_prompt_edit = QPlainTextEdit()
        self.captioner_prompt_edit.setPlaceholderText("")
        self.captioner_prompt_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.captioner_prompt_edit._highlighter = MarkdownSyntaxHighlighter(self.captioner_prompt_edit.document())

        settings_layout.addLayout(grid)
        settings_layout.addWidget(self.captioner_prompt_label)
        settings_layout.addWidget(self.captioner_prompt_edit, 1)
        self.stack.addWidget(settings_tab)

        # Tab bar: placed by MainWindow at the left of the merged caption bar.
        # Wheel events on it are consumed by MainWindow's event filter so
        # scrolling never switches tabs.
        self.tab_bar = QTabBar()
        self.tab_bar.addTab("Console")
        self.tab_bar.addTab("Captioner settings")
        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)
        self.tab_bar.setShape(QTabBar.Shape.RoundedSouth)

        layout.addWidget(self.stack, 1)

        # Action buttons: placed by MainWindow in the merged caption bar.
        self.btn_generate = QPushButton("Generate", self)
        self.btn_generate.setEnabled(False)
        self.btn_append = QPushButton("Append", self)
        self.btn_append.setVisible(False)
        self.btn_replace = QPushButton("Replace", self)
        self.btn_replace.setVisible(False)
