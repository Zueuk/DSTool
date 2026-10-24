import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QSize, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QPalette,
)
from PySide6.QtWidgets import (
    QApplication, QMessageBox,
)

from dstool.utils import (
    SUPPORTED_EXTENSIONS,
    is_image_file,
    is_mask_file,
    mask_filename,
    caption_filename,
    base_from_mask,
    regex_matches_any,
)


@dataclass
class ImageFileEntry:
    """Image file and its associated mask/caption files."""
    name: str
    has_image: bool
    has_mask: bool
    has_caption: bool
    caption: str | None  # Loaded text or None (not yet loaded)


class FileFilterProxyModel(QSortFilterProxyModel):
    """Filters image file list by filename/caption (regex OR). Dims non-matching rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._compiled_patterns = []

    def set_compiled_patterns(self, patterns: list):
        self._compiled_patterns = patterns
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        source_model = self.sourceModel()
        if source_model is None:
            return False

        index = source_model.index(source_row, 0)
        filename = source_model.data(index, Qt.DisplayRole)

        pattern = self.filterRegularExpression()
        if pattern.pattern() == "":
            return True

        # Check filename
        if filename and pattern.match(filename).hasMatch():
            return True

        # Check caption text (lazy-loaded)
        source_model._ensure_caption_loaded(source_row)
        entry = source_model._entries[source_row]
        caption_text = entry.caption if entry.caption else ""
        if caption_text and pattern.match(caption_text).hasMatch():
            return True

        return False

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()

        # ForegroundRole: dim non-matching rows
        if role == Qt.ForegroundRole and self._compiled_patterns:
            # Use source model's data for ForegroundRole
            src_index = self.mapToSource(index)
            text_color = self.sourceModel().data(src_index, Qt.ForegroundRole)
            if text_color is None:
                text_color = QApplication.palette().color(QPalette.ColorRole.Text)

            # Check if this row matches highlight patterns
            if self._matches_any_pattern(row):
                return text_color
            else:
                return QColor(text_color).darker(150)

        return super().data(index, role)

    def _matches_any_pattern(self, source_row: int) -> bool:
        source_model = self.sourceModel()
        if source_model is None:
            return False

        # Check filename
        index = source_model.index(source_row, 0)
        filename = source_model.data(index, Qt.DisplayRole)
        if filename:
            if regex_matches_any(self._compiled_patterns, filename):
                return True

        # Check caption text
        source_model._ensure_caption_loaded(source_row)
        entry = source_model._entries[source_row]
        caption_text = entry.caption if entry.caption else ""
        if caption_text:
            if regex_matches_any(self._compiled_patterns, caption_text):
                return True

        return False


class ImageFileListModel(QAbstractTableModel):
    MISSING_ITEM_COLOR = QColor("#808080")  # Image is missing but mask/caption exist
    MISSING_ALL_COLOR = QColor("#B01010")   # All files are missing

    entryRenamed = Signal(str, str)  # old_base, new_base

    def __init__(self, folder_path: str = "", parent=None):
        super().__init__(parent)
        self._folder = folder_path
        self._entries: list[ImageFileEntry] = []
        if folder_path:
            self.scan_folder(folder_path)

    def rowCount(self, parent=QModelIndex()):
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        entry = self._entries[row]
        if role == Qt.SizeHintRole:
            return QSize(-1, 24)
        elif role == Qt.ToolTipRole:
            return entry.name
        if col == 0:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return entry.name
            elif role == Qt.ForegroundRole:
                if not entry.has_image:
                    if not entry.has_mask and not entry.has_caption:
                        return self.MISSING_ALL_COLOR
                    else:
                        return self.MISSING_ITEM_COLOR
            elif role == Qt.FontRole:
                if not entry.has_image and not entry.has_mask and not entry.has_caption:
                    font = QFont()
                    font.setStrikeOut(True)
                    return font
        if col in (1, 2):
            if role == Qt.TextAlignmentRole:
                 return Qt.AlignCenter
            if role == Qt.DisplayRole:
                if col == 1:
                    return "☑️" if entry.has_mask else "⬛"
                else:
                    return "✅" if entry.has_caption else "⬛"
        return None

    def _load_caption_sync(self, base_name: str) -> str:
        if not self._folder:
            return ""
        caption_path = os.path.join(self._folder, caption_filename(base_name))
        if not os.path.isfile(caption_path):
            return ""
        try:
            with open(caption_path, 'r', encoding='utf-8') as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return ""

    def _caption_file_exists(self, base_name: str) -> bool:
        if not self._folder:
            return False
        caption_path = os.path.join(self._folder, caption_filename(base_name))
        return os.path.isfile(caption_path)

    def _ensure_caption_loaded(self, row: int):
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]
        if entry.has_caption and entry.caption is None:
            caption_text = self._load_caption_sync(entry.name)
            entry.caption = caption_text

    def get_caption_text(self, row: int) -> str:
        if row < 0 or row >= len(self._entries):
            return ""
        self._ensure_caption_loaded(row)
        entry = self._entries[row]
        return entry.caption if entry.caption else ""

    def set_caption_text(self, row: int, text: str):
        entry = self._entries[row]
        if text.strip() != "":
            entry.has_caption = True
            entry.caption = text
        else:
            entry.has_caption = False
            entry.caption = None
        idx_first = self.index(row, 0)
        idx_last = self.index(row, self.columnCount() - 1)
        if idx_first.isValid() and idx_last.isValid():
            self.dataChanged.emit(idx_first, idx_last)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return ["File", "Mask", "Caption"][section]
        return None

    def flags(self, index):
        if not index.isValid() or index.column() != 0:
            return Qt.ItemIsSelectable | Qt.ItemIsEnabled
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable

    def scan_folder(self, folder_path: str):
        self.beginResetModel()
        self._folder = folder_path
        self._entries.clear()
        if not folder_path or not os.path.isdir(folder_path):
            self.endResetModel()
            return
        all_files = os.listdir(folder_path)
        image_bases = set()
        for f in all_files:
            if is_image_file(f) and not is_mask_file(f):
                image_bases.add(Path(f).stem)
        mask_bases = set()
        for f in all_files:
            if is_mask_file(f):
                mask_bases.add(base_from_mask(f))
        caption_bases = set()
        for f in all_files:
            if f.endswith('.txt') and not is_mask_file(f):
                caption_bases.add(Path(f).stem)
        all_bases = sorted(image_bases | mask_bases | caption_bases, key=str.lower)
        for base in all_bases:
            has_img = base in image_bases
            has_msk = base in mask_bases
            has_cap = self._caption_file_exists(base)
            self._entries.append(ImageFileEntry(
                name=base,
                has_image=has_img,
                has_mask=has_msk,
                has_caption=has_cap,
                caption=None,  # Lazy-loaded on demand
            ))
        self.endResetModel()

    def update_item(self, row: int):
        entry = self._entries[row]
        entry.has_mask = os.path.isfile(os.path.join(self._folder, mask_filename(entry.name)))

        idx_first = self.index(row, 0)
        idx_last = self.index(row, self.columnCount() - 1)
        if idx_first.isValid() and idx_last.isValid():
            self.dataChanged.emit(idx_first, idx_last)

    def sync_item(self, row: int):
        entry = self._entries[row]
        base_name = entry.name

        folder = self._folder
        img_exists = any(
            os.path.isfile(os.path.join(folder, base_name + ext))
            for ext in SUPPORTED_EXTENSIONS
        )
        mask_exists = os.path.isfile(os.path.join(folder, mask_filename(base_name)))
        caption_exists = os.path.isfile(os.path.join(folder, caption_filename(base_name)))

        # Update internal state
        old_has_image = entry.has_image
        old_has_mask = entry.has_mask
        old_has_caption = entry.has_caption

        entry.has_image = img_exists
        entry.has_mask = mask_exists
        entry.has_caption = caption_exists
        if caption_exists:
            # Always sync caption text with disk so the model is the
            # single source of truth for the selected entry
            entry.caption = self._load_caption_sync(base_name)
        else:
            # Caption file disappeared; drop any stale cached text
            entry.caption = None

        # Emit dataChanged for modified cells
        cols = []
        if old_has_image != entry.has_image:
            cols.append(0)  # filename column
        if old_has_mask != entry.has_mask:
            cols.append(1)  # mask column
        if old_has_caption != entry.has_caption:
            cols.append(2)  # caption column

        if cols:
            idx_min = self.index(row, min(cols))
            idx_max = self.index(row, max(cols))
            self.dataChanged.emit(idx_min, idx_max)

    def entry_at(self, row: int) -> ImageFileEntry:
        """Public accessor for the entry at a given row."""
        return self._entries[row]

    def find_entry_by_name(self, name: str) -> int:
        """Find row index by base name. Returns -1 if not found."""
        for i, e in enumerate(self._entries):
            if e.name == name:
                return i
        return -1

    def folder(self):
        return self._folder

    def _check_rename_conflicts(self, old_base: str, new_base: str, folder: str) -> dict:
        """Check for file conflicts when renaming old_base to new_base.

        Returns a dict with:
          - 'conflict_entry': the existing entry name that conflicts (or None)
          - 'conflicting_types': list of file types that conflict
          - 'old_types': list of file types old_base has
          - 'new_types': list of file types the conflicting new_base entry has
        """
        # Collect file types that old_base has
        old_types = set()
        for ext in SUPPORTED_EXTENSIONS:
            if os.path.isfile(os.path.join(folder, old_base + ext)):
                old_types.add('image')
                break
        if os.path.isfile(os.path.join(folder, mask_filename(old_base))):
            old_types.add('mask')
        if os.path.isfile(os.path.join(folder, caption_filename(old_base))):
            old_types.add('caption')

        # Find entries that have new_base name
        for entry in self._entries:
            if entry.name == new_base:
                # Collect file types for this entry
                new_types = set()
                for ext in SUPPORTED_EXTENSIONS:
                    if os.path.isfile(os.path.join(folder, entry.name + ext)):
                        new_types.add('image')
                        break
                if os.path.isfile(os.path.join(folder, mask_filename(entry.name))):
                    new_types.add('mask')
                if os.path.isfile(os.path.join(folder, caption_filename(entry.name))):
                    new_types.add('caption')

                # Check for conflicts (same file type in both)
                conflicting = old_types & new_types
                return {
                    'conflict_entry': entry.name,
                    'conflicting_types': sorted(conflicting),
                    'old_types': sorted(old_types),
                    'new_types': sorted(new_types),
                }

        # Also check for bare file conflicts (file exists but no entry in model)
        for f in os.listdir(folder):
            if f == new_base + ".txt" and 'caption' in old_types:
                return {
                    'conflict_entry': '(bare file)',
                    'conflicting_types': ['caption'],
                    'old_types': sorted(old_types),
                    'new_types': ['caption'],
                }
            if is_image_file(f) and Path(f).stem == new_base and 'image' in old_types:
                return {
                    'conflict_entry': '(bare file)',
                    'conflicting_types': ['image'],
                    'old_types': sorted(old_types),
                    'new_types': ['image'],
                }
            if is_mask_file(f) and base_from_mask(f) == new_base and 'mask' in old_types:
                return {
                    'conflict_entry': '(bare file)',
                    'conflicting_types': ['mask'],
                    'old_types': sorted(old_types),
                    'new_types': ['mask'],
                }

        return {
            'conflict_entry': None,
            'conflicting_types': [],
            'old_types': sorted(old_types),
            'new_types': [],
        }

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        if index.column() != 0:
            return False

        folder = self._folder
        row = index.row()
        old_base = self._entries[row].name
        new_base = str(value).strip()

        if not new_base:
            QApplication.beep()
            return False
        if new_base == old_base:
            return False

        prohibited_chars = set('<>:"/\\|?*')
        if any(c in prohibited_chars for c in new_base):
            QApplication.beep()
            return False

        # Check for conflicts
        conflict_info = self._check_rename_conflicts(old_base, new_base, folder)

        if conflict_info['conflict_entry'] is not None:
            # Build user-friendly message
            conflict_entry = conflict_info['conflict_entry']
            types_str = ', '.join(conflict_info['conflicting_types'])

            if conflict_entry == '(bare file)':
                msg = (f"A file already exists with the name '{new_base}' "
                       f"({types_str} file). Rename will overwrite it.")
            else:
                extras = set(conflict_info['new_types']) - set(conflict_info['conflicting_types'])

                if conflict_info['conflicting_types']:
                    # Has conflicts: some file types overlap, extras will be merged
                    extra_str = f" (will merge with {', '.join(extras)})" if extras else ""
                    msg = (f"Entry '{conflict_entry}' already exists with {types_str} file(s). "
                           f"Rename will overwrite the existing {types_str} file(s).{extra_str}\n\nProceed?")
                else:
                    # No conflicts: target entry has complementary files, merge into it
                    target_types = ', '.join(conflict_info['new_types'])
                    msg = (f"Entry '{conflict_entry}' already has:\n{target_types}\n"
                           f"Renaming will merge into this entry.\n\nProceed?")

            reply = QMessageBox.question(
                None,
                "Rename Conflict",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.Yes:
                return False

        # Find which row has new_base (if any) - we'll need to remove it if merging
        conflict_row = None
        for r, entry in enumerate(self._entries):
            if entry.name == new_base:
                conflict_row = r
                break

        # Rename old_base's files to new_base
        renamed_any = False
        try:
            for ext in SUPPORTED_EXTENSIONS:
                old_path = os.path.join(folder, old_base + ext)
                new_path = os.path.join(folder, new_base + ext)
                if os.path.isfile(old_path):
                    # Overwrite if exists (from conflict merge)
                    os.rename(old_path, new_path)
                    renamed_any = True
            old_mask = os.path.join(folder, mask_filename(old_base))
            new_mask = os.path.join(folder, mask_filename(new_base))
            if os.path.isfile(old_mask):
                os.rename(old_mask, new_mask)
                renamed_any = True
            old_cap = os.path.join(folder, caption_filename(old_base))
            new_cap = os.path.join(folder, caption_filename(new_base))
            if os.path.isfile(old_cap):
                os.rename(old_cap, new_cap)
                renamed_any = True
        except OSError:
            QMessageBox.critical(None, "Rename Error", "Failed to rename files.")
            return False

        if not renamed_any:
            QApplication.beep()
            return False

        # Notify that an entry was renamed to select new item
        self.entryRenamed.emit(old_base, new_base)

        # Handle model updates for merge scenario after the new item is selected
        if conflict_row is not None and conflict_row != row:
            # Merge: A (row X) is renamed to B, B exists at row Y
            # Keep B's row (row Y), remove A's row (row X)
            # The files are already merged (A's files overwritten/merged into B's files)
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entries.pop(row)
            self.endRemoveRows()
        else:
            self._entries[row].name = new_base

        return True
