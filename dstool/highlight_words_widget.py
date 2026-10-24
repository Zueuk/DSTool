from math import ceil

from PySide6.QtCore import (
    Qt, QEvent, QObject, QPoint, QSize, QTimer, QRect, Signal
)
from PySide6.QtGui import (
    QFontMetricsF, QPalette, QTextCursor,
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QStackedWidget, QVBoxLayout, QApplication, QScrollArea,
    QPlainTextEdit, QLayout,
)

from dstool.widgets import ElidedLabel, WildcardSyntaxHighlighter


class FlowLayout(QLayout):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._item_list = []
        self._spacing = 2
        self._cached_min_size = QSize()
        self._parent = parent
        if isinstance(parent, QWidget):
            parent.installEventFilter(self)

    def eventFilter(self, watched, event):
        # The cached min size includes the parent's width as a cap, so it must
        # be recomputed whenever the parent resizes.
        if watched is self._parent and event.type() == QEvent.Resize:
            self._cached_min_size = QSize()
        return False

    def __del__(self):
        for item in self._item_list:
            if item.widget():
                item.widget().deleteLater()
        self._item_list.clear()

    def setSpacing(self, spacing: int):
        self._spacing = spacing

    def spacing(self) -> int:
        return self._spacing

    def addItem(self, item):
        self._item_list.append(item)
        self._cached_min_size = QSize()
        self.update()

    def count(self):
        return len(self._item_list)

    def itemAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._item_list):
            result = self._item_list.pop(index)
            self._cached_min_size = QSize()
            return result
        return None

    def invalidateCache(self):
        self._cached_min_size = QSize()

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        if not self._cached_min_size.isValid():
            # Return the width of the widest single item, not the sum
            size = QSize()
            for item in self._item_list:
                size = size.expandedTo(item.minimumSize())
            margins = self.contentsMargins()
            size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

            # Cap at parent widget's width if available
            parent = self.parent()
            if parent and parent.width() > 0:
                size.setWidth(min(size.width(), parent.width()))

            self._cached_min_size = size
        return self._cached_min_size

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    @staticmethod
    def _item_size(item, max_width):
        """The size the item is laid out at.

        An item whose hint exceeds the available line width (e.g. a
        single unbroken word wider than the container) is clamped to the
        line width instead of overflowing; elidable widgets render at
        whatever width they are given."""
        size = item.sizeHint()
        if size.width() > max_width:
            size.setWidth(max_width)
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self._spacing
        line_width = rect.width()
        right = rect.x() + rect.width()

        for item in self._item_list:
            size = self._item_size(item, line_width)
            next_x = x + size.width()
            if next_x > right and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + size.width()
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), size))

            x = next_x + spacing
            line_height = max(line_height, size.height())

        return y + line_height - rect.y()


# ============================================================================
# ChipWidget - A single chip widget with hover + click support
# ============================================================================

class ChipWidget(ElidedLabel):
    """A single chip widget displaying a word with rounded rectangle background.

    Styling is done entirely via QSS."""

    clicked = Signal()
    doubleClicked = Signal()

    _PADDING_H = 2  # QSS horizontal padding - must match the stylesheet
    _BORDER = 1     # QSS border width - must match the stylesheet

    def __init__(self, text: str, parent=None):
        super().__init__(text, Qt.ElideRight, parent)
        self.setAlignment(Qt.AlignCenter | Qt.TextSingleLine)
        self.setObjectName("ChipWidget")
        self._selected = False
        self._cached_size_hint = QSize()

    def sizeHint(self):
        """Return the cached size the FULL word needs.

        All chips share the same font (never changes), text is set once
        at construction, so this value is computed once and cached; the
        cache saves thousands of QFontMetricsF measurements during
        splitter drags and flow relayouts.

        The chip always reports the full width; a chip wider than the line
        is clamped by FlowLayout._item_size.
        """
        if self._cached_size_hint.isValid():
            return self._cached_size_hint
        fm = QFontMetricsF(self.font())
        frame = 2 * (self._PADDING_H + self._BORDER)
        w = ceil(fm.horizontalAdvance(self._text) + frame)
        h = ceil(fm.height() + 2 * self._BORDER)
        self._cached_size_hint = QSize(w, h)
        return self._cached_size_hint

    def setSelected(self, selected: bool):
        """Set selection state and update QSS attribute."""
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty("selected", selected)
        # Force QSS re-evaluation using the global stylesheet
        self.style().polish(self)

    def mousePressEvent(self, event):
        """Handle single click."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Handle double click."""
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


# ============================================================================
# FlowChipContainer - Container with FlowLayout for chip widgets
# ============================================================================

class FlowChipContainer(QWidget):
    """Container widget that arranges chip widgets in a flow layout."""

    word_clicked = Signal(str)
    word_double_clicked = Signal()
    sizeHintChanged = Signal()  # emitted when the flow height changes

    _MAX_LINES = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._words = []  # Track words for size computation
        self._layout = FlowLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setContentsMargins(2, 2, 2, 2)
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self.setAutoFillBackground(True)
        self._last_hint_height = None
        self._settle_scheduled = False

    def set_words(self, words):
        """Replace all chips with the given words."""
        for w in self.findChildren(ChipWidget):
            w.deleteLater()
        self._layout._item_list.clear()
        self._layout.invalidateCache()

        self._words = list(words)
        for word in self._words:
            chip = ChipWidget(str(word))
            chip.clicked.connect(self._on_word_clicked)
            chip.doubleClicked.connect(self._on_word_double_clicked)
            # Store word as property on the chip for later retrieval
            chip.setProperty("word", word)
            self._layout.addWidget(chip)
        self._notify_if_height_changed()
        # The synchronous hint above was read with the chip geometry of the
        # previous layout. Schedule one re-check once the event loop lets
        # the pending layout settle, so a hint that was read from a mid
        # transition state gets corrected instead of staying stuck (see
        # HighlightWordsWidget: ResizingTabWidget applies the hint as the
        # maximum height, and nothing else would re-open it afterwards).
        self._schedule_hint_recheck()

    def _schedule_hint_recheck(self):
        if self._settle_scheduled:
            return
        self._settle_scheduled = True
        QTimer.singleShot(0, self._recheck_hint_after_settle)

    def _recheck_hint_after_settle(self):
        self._settle_scheduled = False
        self._notify_if_height_changed()

    def _notify_if_height_changed(self):
        """Emit sizeHintChanged when the flow height changed because
        chips were added/removed or the container (re)laid out.

        This must not wait for the next layout pass: the parent reads
        this container's sizeHint right after mutating the word list, and
        without the emission the parent would be told the stale
        (pre-mutation) height and never re-check after the flow actually
        relayouts."""
        new_height = self.sizeHint().height()
        if new_height != self._last_hint_height:
            self._last_hint_height = new_height
            self.updateGeometry()
            self.sizeHintChanged.emit()

    def _on_word_clicked(self):
        """Emit word_clicked signal with the clicked word and mark as selected."""
        sender = QObject.sender(self)
        if not isinstance(sender, ChipWidget):
            return
        word = sender.property("word")

        # Select chips containing the clicked word (there may be more than one)
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChipWidget):
                chip_widget = item.widget()
                chip_widget.setSelected(chip_widget.property("word") == word)
        self.word_clicked.emit(word)

    def _on_word_double_clicked(self):
        """Emit word_double_clicked signal."""
        self.word_double_clicked.emit()

    def clear_selection(self):
        """Clear all selection states."""
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChipWidget):
                item.widget().setSelected(False)

    _LINE_HEIGHT = 20

    def _flow_height(self):
        """Uncapped flow height at the current width, margins included.

        Returns _LINE_HEIGHT (one line) until the container has a real
        width and at least one chip - the same "no real geometry" sentinel
        the old sizeHint used."""
        if self._layout.count() == 0 or self.width() <= 0:
            return self._LINE_HEIGHT
        m = self.contentsMargins()
        inner = max(self.width() - m.left() - m.right(), 1)
        height = self._layout.heightForWidth(inner)
        if height <= 0:
            height = self._LINE_HEIGHT
        return height + m.top() + m.bottom()

    def _max_flow_height(self):
        """Height cap: _MAX_LINES rows of chips (at the flow spacing) plus
        the container margins. Beyond this the QScrollArea's vertical
        scrollbar (in HighlightWordsWidget) takes over, so the tab can never
        grow taller than _MAX_LINES rows of chips.
        """
        line_h = self._LINE_HEIGHT
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if item and item.widget():
                h = item.widget().sizeHint().height()
                if h > 0:
                    line_h = h
                    break
        spacing = self._layout.spacing()
        m = self.contentsMargins()
        return (self._MAX_LINES * line_h
                + (self._MAX_LINES - 1) * spacing
                + m.top() + m.bottom())

    def sizeHint(self):
        """Size at the container's current width, capped at _MAX_LINES rows.

        The parent (HighlightWordsWidget) is the only reader of this hint
        and it only does so once it has been laid out, at which point this
        container already carries the parent's real width (the parent
        keeps the hidden stack page in sync). Before that the number is
        meaningless, so a zero/dummy is reported.
        """
        if self._layout.count() == 0 or self.width() <= 0:
            return QSize(0, self._LINE_HEIGHT)
        height = min(self._flow_height(), self._max_flow_height())
        return QSize(self.width(), height)

    def minimumSizeHint(self):
        """Return minimum size: width of the widest single chip (capped at
        300); height is the FULL (uncapped) flow height. The container lives
        inside a QScrollArea (widgetResizable), whose lower bound is the
        widget's minimumSizeHint - so an uncapped min is what makes the
        vertical scrollbar appear once the flow exceeds _MAX_LINES rows.
        (HighlightWordsWidget.minimumSizeHint no longer reads this, so the
        tab can still shrink to a single line.)
        """
        if self._layout.count() == 0:
            return QSize(0, 20)
        widest = 0
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if not item or not item.widget():
                continue
            widest = max(widest, item.widget().minimumSize().width())
        # Cap the width at a reasonable maximum
        capped_width = min(widest, 300)
        if capped_width < 50:
            capped_width = 200
        return QSize(capped_width, self._flow_height())

    def resizeEvent(self, event):
        """Handle resize: the flow height depends on the new width, so the
        height hint may change - notify the parent (it re-checks and
        forwards only when ITS hint actually changes).

        Runs even with zero chips: a shrink to nothing must be forwarded
        as well, and _sync_hidden_pages resizes this container to the
        parent's width without changing its height, which is exactly how
        a pending width change reaches the hint math.
        """
        super().resizeEvent(event)
        self._notify_if_height_changed()

    def mousePressEvent(self, event):
        """Handle click - if on empty space, clear all selection states."""
        if event.button() == Qt.LeftButton:
            widget_at_pos = QApplication.widgetAt(event.globalPosition().toPoint())
            if not isinstance(widget_at_pos, ChipWidget):
                # Click on empty space - clear all selection
                self.clear_selection()
                self.word_clicked.emit("")
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Handle double-click - if on empty space, trigger edit mode."""
        if event.button() == Qt.LeftButton:
            widget_at_pos = QApplication.widgetAt(event.globalPosition().toPoint())
            if not isinstance(widget_at_pos, ChipWidget):
                # Double-click on empty space - trigger edit mode
                self.word_double_clicked.emit()
        super().mouseDoubleClickEvent(event)


# ============================================================================
# HighlightWordsTextEdit - For editing highlight words
# ============================================================================

class HighlightWordsTextEdit(QPlainTextEdit):
    """Messenger-style auto-growing QPlainTextEdit (space-separated words).

    Height tracks the content from 1 line up to _MAX_LINES lines at the
    editor's current width; beyond that it stops growing and the editor's
    own scrollbar takes over. The parent widget then takes the max of this
    and the chip container's height, so the widget is always tall enough
    for both.

    The parent lays this widget out at its (real) width - including while
    it is a hidden stack page - so once laid out the wrap math is exact.
    Before the parent has laid it out (no real width) a dummy size is
    reported."""

    editingDone = Signal()
    editingCancelled = Signal()
    sizeHintChanged = Signal()  # Emits when the computed size hint changes

    _MAX_LINES = 9
    _LINE_PAD = 4  # document top/bottom margin + viewport slack

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        self.setPlaceholderText("Wildcards are supported...")
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # self.setStyleSheet("HighlightWordsTextEdit{border: 0px}")
        self._highlighter = WildcardSyntaxHighlighter(self.document())
        self._last_hint = QSize()
        self.document().documentLayout().documentSizeChanged.connect(self._update_hint)

    def _text_width(self):
        """Width the document wrap math runs at; 0 when the editor has not
        been given a real width yet, in which case callers report a dummy
        height instead of laying the document out at a bogus width."""
        return self.viewport().width()

    def _content_height(self):
        """Height for the current text at the current width: 1.._MAX_LINES
        lines.

        The document is queried at the real viewport width, so the line
        count is exact. Above _MAX_LINES the extra content is left to the
        vertical scrollbar, so the hint stays bounded no matter how long
        the text gets. Returns 0 when there is no real width yet.
        """
        text_width = self._text_width()
        if text_width <= 0:
            return 0
        if not self.toPlainText():
            lines = 1
        else:
            doc = self.document()
            doc.setTextWidth(text_width)
            lines = 0
            block = doc.begin()
            while block.isValid():
                lines += block.layout().lineCount()
                block = block.next()
        lines = min(lines, self._MAX_LINES)
        return ceil(lines * QFontMetricsF(self.font()).lineSpacing() + self._LINE_PAD * 2 + 3)

    def sizeHint(self):
        """Return size based on content at the current width; a dummy
        (zero) size until the parent has laid this widget out."""
        if self.width() <= 0:
            return QSize(0, 0)
        return QSize(self.width(), self._content_height())

    def showEvent(self, event):
        super().showEvent(event)
        self._update_hint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The line count depends on the width, so the height hint changes
        # whenever the editor is resized.
        self._update_hint()

    def _update_hint(self):
        """Re-evaluate the size hint (height depends on content and width)
        and notify the parent only when it actually changed."""
        new_hint = self.sizeHint()
        if new_hint != self._last_hint:
            self._last_hint = new_hint
            self.sizeHintChanged.emit()

    def keyPressEvent(self, event):
        """Handle Enter as save, Escape as cancel, disable all newlines."""
        key = event.key()

        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.editing_done()
            return
        elif key == Qt.Key_Escape:
            self.editingCancelled.emit()
            return

        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        # Filter out newlines from pasted text
        if source and source.hasText():
            clean_text = source.text().replace('\n', '\r').replace('\r', ' ')
            self.insertPlainText(clean_text)
        else:
            super().insertFromMimeData(source)

    def focusOutEvent(self, event):
        """Save when focus is lost."""
        self.editing_done()
        super().focusOutEvent(event)

    def editing_done(self):
        """Signal that editing is done (save)."""
        self.editingDone.emit()


# ============================================================================
# HighlightWordsWidget - Main widget combining display and edit modes
# ============================================================================

class HighlightWordsWidget(QFrame):
    """A widget for displaying and editing trigger/highlight words.

    Emits sizeHintChanged when the size hint actually changes, so parent
    containers (like ResizingTabWidget) can re-layout only when needed.

    Displays words as clickable chips in a flow layout.
    Words are space-separated in edit mode.
    Double-clicking switches to an edit mode with QPlainTextEdit.
    Single-clicking a chip emits word_clicked(word).

    Signals:
        textChanged(text: str) - emitted when words change
        word_clicked(word: str) - emitted when a chip is single-clicked
        word_double_clicked() - emitted when area is double-clicked
    """
    textChanged = Signal(str)
    word_clicked = Signal(str)
    word_double_clicked = Signal()
    sizeHintChanged = Signal()  # Emits when sizeHint actually changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._words = []  # Single source of truth: list of word strings
        self._is_editing = False
        self._last_hint = None
        # The single "geometry is real" flag. Children start at Qt's
        # default size (640x480), so a non-zero width is NOT proof of a
        # real layout: until an *external* layout resizes this widget
        # (resizeEvent) both it and its pages carry garbage widths, and
        # the children's width-dependent height math must not run.
        # Once set, this widget resizes its hidden stack page to its own
        # real width (see _sync_hidden_pages), so from then on every
        # child width is real and the children need no flag of their own.
        self._has_been_laid_out = False
        self._settle_scheduled = False
        self.setChanged(False)

        self._stack = QStackedWidget()
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setBackgroundRole(QPalette.ColorRole.Highlight)

        # Display mode: flow chip container, wrapped in a scroll area so a
        # long word list scrolls (vertical, as-needed) instead of growing
        # the tab taller than _MAX_LINES rows (see FlowChipContainer).
        self._chip_container = FlowChipContainer()
        self._chip_container.word_double_clicked.connect(self._on_double_click)
        self._chip_container.word_clicked.connect(self._on_word_clicked)
        self._chip_container.sizeHintChanged.connect(self._on_child_hint_changed)

        self._chip_scroll = QScrollArea()
        self._chip_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._chip_scroll.setWidgetResizable(True)
        self._chip_scroll.setWidget(self._chip_container)
        self._chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Keep the scroll area's own minimum at one line so the container's
        # (now full-flow) minimumSizeHint can't propagate up through the
        # layout and fight the ResizingTabWidget height cap.
        self._chip_scroll.setMinimumHeight(24)
        self._chip_scroll.viewport().setAutoFillBackground(True)
        self._chip_scroll.viewport().setBackgroundRole(QPalette.ColorRole.Base)

        # Edit mode: plain text edit
        self._editor = HighlightWordsTextEdit()
        self._editor.editingDone.connect(self._on_editing_done)
        self._editor.editingCancelled.connect(self._on_editing_cancelled)
        self._editor.sizeHintChanged.connect(self._on_child_hint_changed)

        self._stack.addWidget(self._chip_scroll)
        self._stack.addWidget(self._editor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack)

        self.setText("")

    def showEvent(self, event):
        super().showEvent(event)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The first (and every later) real geometry from the outer layout
        # is the moment the width-dependent math is trusted.
        self._has_been_laid_out = True
        self._sync_hidden_pages()
        self._notify_if_hint_changed()

    def _sync_hidden_pages(self):
        """QStackedWidget does not resize hidden pages. Keep both pages at
        the current width so that width-dependent size hints (editor line
        count, flow wrapping) stay correct after mode switches and panel
        resizes. Only runs once the layout is real - before that the
        pages would inherit this widget's garbage default width.
        """
        if not self._has_been_laid_out:
            return
        self.updateGeometry()

    def _on_child_hint_changed(self):
        """A child (chip container or editor) re-evaluated its height on a
        text or width change; re-check and propagate upward only when this
        widget's own hint actually changed. The editor lives on a hidden
        stack page while it is not focused - syncing first makes its wrap
        math run at the real width."""
        self._sync_hidden_pages()
        self._notify_if_hint_changed()
        self._schedule_hint_recheck()

    def _schedule_hint_recheck(self):
        """Re-check the hint after layout settles.

        Synchronous checks read mid-transition geometry; defer to the
        next event loop turn so the parent's layout has processed,
        preventing the widget from being stuck at a wrong height.
        """
        if self._settle_scheduled:
            return
        self._settle_scheduled = True
        QTimer.singleShot(0, self._recheck_hint_after_settle)

    def _recheck_hint_after_settle(self):
        self._settle_scheduled = False
        self._notify_if_hint_changed()

    def _notify_if_hint_changed(self):
        """Emit sizeHintChanged only when the hint really changed.

        The parent (ResizingTabWidget) applies the hint as both the
        preferred and the maximum height, so the parent both grows and
        shrinks; emitting only on genuine change keeps it from being
        re-driven with a stale height."""
        new_hint = self.sizeHint()
        if new_hint != self._last_hint:
            self._last_hint = new_hint
            self.sizeHintChanged.emit()

    def sizeHint(self):
        """Return the size to request from the parent."""
        if not self._has_been_laid_out:
            return self.minimumSizeHint()
        m = self.contentsMargins()
        pad_size = QSize(m.left() + m.right(), m.top() + m.bottom())
        if self._is_editing:
            return self._editor.sizeHint() + pad_size
        else:
            return self._chip_container.sizeHint() + pad_size

    def minimumSizeHint(self):
        """Allow shrinking to a single line of chips: the parent re-derives
        the height from sizeHint on every real change (see
        _notify_if_hint_changed), so the min must not pin the current
        line count. This deliberately does NOT read the chip container's
        minimum (which is now the full flow height so the scroll area can
        scroll) - it stays one line so the tab can always shrink.
        """
        return QSize(0, 24)

    def _on_word_clicked(self, word):
        """Handle single-click on a word chip."""
        self.word_clicked.emit(word)

    def _on_double_click(self):
        """Handle double-click to enter edit mode."""
        self._switch_to_edit_mode()

    def _switch_to_edit_mode(self):
        """Switch to edit mode and populate the editor.

        The chips are left in place (hidden behind the editor) so a
        cancelled edit restores the display for free - the only state the
        editor can change is self._words, and only on accept.
        """
        self._sync_hidden_pages()
        self._editor.setPlainText(' '.join(self._words))

        # Position cursor at end of text
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._editor.setTextCursor(cursor)
        self._stack.setCurrentWidget(self._editor)
        self._editor.setFocus()
        self._is_editing = True
        self._notify_if_hint_changed()

    def _switch_to_display_mode(self):
        """Switch back to display mode (callers should handle chip updates).

        The flag is cleared BEFORE the stack switch: hiding the focused
        editor fires its focusOutEvent synchronously, which re-enters
        _on_editing_done - the flag must already read False for the
        re-entrant call to be ignored."""
        self._is_editing = False
        # The chip page is the scroll area (the container lives inside it).
        self._stack.setCurrentWidget(self._chip_scroll)
        self._notify_if_hint_changed()

    def _on_editing_done(self):
        """Handle editing completion (Enter / focus out).

        Runs at most once per session: finishing the edit hides the editor,
        which fires its focusOutEvent and re-emits editingDone - the second
        emission is ignored here."""
        if not self._is_editing:
            return
        text = self._editor.toPlainText()
        words = [w for w in text.split() if w.strip()]
        self._words = words
        self._chip_container.set_words(words)
        self._notify_if_hint_changed()

        joined = ' '.join(words)
        self.textChanged.emit(joined)
        self._switch_to_display_mode()

    def _on_editing_cancelled(self):
        """Handle Escape key during editing - discard the edit. Chips and
        self._words were untouched by the editor, so switching the page
        back is the whole restore."""
        self._switch_to_display_mode()

    def setChanged(self, changed: bool):
        """Set the 'changed' state which shows a green border."""
        if changed:
            self.setStyleSheet("HighlightWordsWidget{ border: 1px solid green; }")
        else:
            self.setStyleSheet("HighlightWordsWidget{ border: 1px solid transparent; }")

    def setText(self, text: str):
        """Set the trigger words as a space-joined string."""
        words = [w for w in text.split() if w.strip()]
        if words != self._words:
            self._words = words
            self._chip_container.set_words(words)
            self._notify_if_hint_changed()

    def text(self) -> str:
        """Get the trigger words as a space-joined string."""
        return ' '.join(self._words)

    def clear_selection(self):
        """Clear all chip selection states (e.g. on folder load).

        setText() keeps the existing chips when the word list is
        unchanged, so a previous folder's selected chip would survive a
        folder (re)open without this explicit clear.
        """
        self._chip_container.clear_selection()
