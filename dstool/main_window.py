import os

from PySide6.QtCore import (
    Qt, QItemSelectionModel, QEvent, QMimeData, QSize, QTimer, QSettings,
    qWarning,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QKeyEvent, QKeySequence, QShortcut, QTextCursor,
    QImage, QImageReader, QColor, QPalette, QDrag, QDragEnterEvent, QDropEvent,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeView, QHeaderView, QPushButton, QToolButton, QLabel, QSlider, QMenu,
    QCheckBox, QRadioButton, QButtonGroup, QLineEdit, QProgressBar, QFrame,
    QSizePolicy, QStackedWidget, QStyledItemDelegate, QFileDialog, QMessageBox,
)

from dstool.color_theme import apply_palette
from dstool.highlight_words_widget import HighlightWordsWidget
from dstool.image_widget import ImageWidget, MAX_BRUSH_SIZE
from dstool.file_list_model import (
    FileFilterProxyModel,
    ImageFileListModel,
)
from dstool.widgets import (
    CaptionSyntaxHighlighter,
    CaptionEdit,
    CaptionGenerationPanel,
    ResizingTabWidget,
)
from dstool.utils import (
    SUPPORTED_EXTENSIONS,
    mask_filename,
    caption_filename,
    closest_aspect_ratio,
    show_in_file_explorer,
    words_to_regex_patterns,
)
from dstool.caption_request import CaptionRequest
from dstool.folder_config import (
    DEFAULT_SERVER_URL,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_CAPTIONER_PROMPT,
    load_config,
    save_config,
)


APPLICATION_TITLE = "DSTool"
DEFAULT_WINDOW_SIZE = QSize(1152, 864)

# QSettings keys
SETTINGS_KEY_THEME = "app/theme"
SETTINGS_KEY_GEOMETRY = "window/geometry"
SETTINGS_KEY_STATE = "window/state"
SETTINGS_KEY_MAIN_SPLITTER = "splitter/main"
SETTINGS_KEY_VIEW_SPLITTER = "splitter/view"
SETTINGS_KEY_SERVER_URL = "captioner/server_url"
SETTINGS_KEY_TIMEOUT = "captioner/timeout"
SETTINGS_KEY_REASONING = "captioner/reasoning_enabled"


class ColorPreservingDelegate(QStyledItemDelegate):
    _light_theme: bool = False

    @staticmethod
    def _update_color_theme(scheme: Qt.ColorScheme):
        ColorPreservingDelegate._light_theme = (scheme == Qt.ColorScheme.Light)

    def __init__(self, parent=None):
        super().__init__(parent)
        app = QApplication.instance()
        if app is not None:
            # Initialize from current scheme
            self._update_color_theme(app.styleHints().colorScheme())
            # Update on theme changes
            app.styleHints().colorSchemeChanged.connect(self._update_color_theme)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)

        color = index.data(Qt.ForegroundRole)
        if isinstance(color, QColor):
            # Override text color for the selected state
            option.palette.setColor(QPalette.HighlightedText, color)
        if ColorPreservingDelegate._light_theme and option.text == "⬛":
            option.text = "⬜"


class MainWindow(QMainWindow):
    def _show_about(self):
        QMessageBox.about(
            self,
            f"About {APPLICATION_TITLE}",
            f"{APPLICATION_TITLE}\n"
            "\n"
            "by Peter Sdobnov\n"
            "https://github.com/Zueuk/\n"
            "\n"
            "Dataset Masking and Captioning Tool\n"
            "created for use with OneTrainer\n"
        )

    def __init__(self, folder_path: str, hide_image_mode: bool = False, settings=None):
        super().__init__()
        self.setWindowTitle(APPLICATION_TITLE)

        self._color_theme_pref = "system"

        self._current_index = None
        self._dirty = False
        self._saved_highlight_words = ""  # space-separated
        self._saved_captioner_prompt = ""

        self._saved_hide_mode = False
        self._caption_request = None
        self._pending_caption = None
        self._batch_items = []
        self._batch_index = None  # not None == batch is running

        # QAction iconText = emoji icon, text = label text for menu display
        self.act_open_folder = QAction(self)
        self.act_open_folder.setText("Open folder...")
        self.act_open_folder.setIconText("📁")
        self.act_open_folder.setShortcut(QKeySequence("Ctrl+O"))
        self.act_open_folder.triggered.connect(self._on_open_folder)
        self.act_refresh_folder = QAction(self)
        self.act_refresh_folder.setText("Refresh folder")
        self.act_refresh_folder.setIconText("🔄")
        self.act_refresh_folder.setShortcut(QKeySequence("F5"))
        self.act_refresh_folder.setEnabled(False)
        self.act_refresh_folder.triggered.connect(self._refresh_current_folder)
        self.act_show_filter = QAction(self)
        self.act_show_filter.setText("Search")
        self.act_show_filter.setIconText("🔍")
        self.act_show_filter.setShortcut(QKeySequence("Ctrl+F"))
        self.act_show_filter.setCheckable(True)
        self.act_show_filter.toggled.connect(self._on_show_filter_changed)
        self.act_separator1 = QAction(self)
        self.act_separator1.setSeparator(True)
        self.act_undo = QAction("Undo", self)
        self.act_undo.setIconText("↩️")
        self.act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.act_undo.setEnabled(False)
        self.act_redo = QAction("Redo", self)
        self.act_redo.setIconText("↪️")
        self.act_redo.setShortcut(QKeySequence("Ctrl+Y"))
        self.act_redo.setEnabled(False)
        self.act_clear_mask = QAction("Clear mask", self)
        self.act_clear_mask.setIconText("🗑️")
        self.act_invert_mask = QAction("Invert mask", self)
        self.act_invert_mask.setIconText("☯️")
        self.act_invert_mask.setToolTip("Invert mask (Ctrl+I)")
        self.act_invert_mask.setShortcut(QKeySequence("Ctrl+I"))
        self.act_outline = QAction("Mask outline", self)
        self.act_outline.setIconText("Outline")
        self.act_outline.setToolTip("Toggle mask outline (Ctrl+M)")
        self.act_outline.setShortcut(QKeySequence("Ctrl+M"))
        self.act_outline.setCheckable(True)
        self.act_hide_image = QAction("🙈 mode", self)
        self.act_hide_image.setIconText("🙈")
        self.act_hide_image.setToolTip("🙈 mode (Ctrl+H)")
        self.act_hide_image.setShortcut(QKeySequence("Ctrl+H"))
        self.act_hide_image.setCheckable(True)
        self.act_hide_image.toggled.connect(self._update_config_save_button_state)

        self.act_toggle_caption_panel = QAction("Show caption panel", self)
        self.act_toggle_caption_panel.setIconText("🤖💬")
        self.act_toggle_caption_panel.setCheckable(True)
        self.act_toggle_caption_panel.setShortcut(QKeySequence("Ctrl+G"))
        self.act_toggle_caption_panel.setToolTip("Toggle caption generation panel (Ctrl+G)")
        self.act_toggle_caption_panel.toggled.connect(self._on_caption_panel_toggled)

        self.act_reasoning = QAction("Enable reasoning", self)
        self.act_reasoning.setCheckable(True)
        self.act_reasoning.setChecked(True)
        self.act_reasoning.toggled.connect(self._on_reasoning_switched)

        self.act_navigate_up = QAction("Previous image", self)
        self.act_navigate_up.setShortcut(QKeySequence("Ctrl+Up"))
        self.act_navigate_up.triggered.connect(self.navigate_up)
        self.act_navigate_down = QAction("Next image", self)
        self.act_navigate_down.setShortcut(QKeySequence("Ctrl+Down"))
        self.act_navigate_down.triggered.connect(self.navigate_down)

        self.act_save = QAction("Save changes", self)
        self.act_save.setIconText("💾 Save changes")
        self.act_save.setToolTip("Save changes (Ctrl+S)")
        self.act_save.setShortcut(QKeySequence("Ctrl+S"))
        self.act_save.setEnabled(False)
        self.act_save.triggered.connect(self.save_current_item)

        self.theme_menu = QMenu("Color theme", self)

        self.act_theme_system = QAction("System", self)
        self.act_theme_system.setCheckable(True)
        self.act_theme_dark = QAction("Dark", self)
        self.act_theme_dark.setCheckable(True)
        self.act_theme_light = QAction("Light", self)
        self.act_theme_light.setCheckable(True)

        theme_group = QActionGroup(self)
        theme_group.addAction(self.act_theme_system)
        theme_group.addAction(self.act_theme_dark)
        theme_group.addAction(self.act_theme_light)
        theme_group.setExclusive(True)

        self.theme_menu.addAction(self.act_theme_system)
        self.theme_menu.addAction(self.act_theme_dark)
        self.theme_menu.addAction(self.act_theme_light)

        self.act_theme_system.triggered.connect(lambda: self._set_color_theme("system"))
        self.act_theme_dark.triggered.connect(lambda: self._set_color_theme("dark"))
        self.act_theme_light.triggered.connect(lambda: self._set_color_theme("light"))

        self.act_about = QAction("About...", self)
        self.act_about.triggered.connect(self._show_about)

        # --- Left toolbar (above file tree) ---
        self.left_toolbar = QWidget()
        left_toolbar_layout = QHBoxLayout(self.left_toolbar)
        left_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        left_toolbar_layout.setSpacing(4)

        self.menu = QMenu()
        self.menu.addAction(self.act_open_folder)
        self.menu.addAction(self.act_refresh_folder)
        self.menu.addSeparator()
        self.menu.addAction(self.act_undo)
        self.menu.addAction(self.act_redo)
        self.menu.addAction(self.act_outline)
        self.menu.addAction(self.act_hide_image)
        self.menu.addSeparator()
        self.menu.addAction(self.act_toggle_caption_panel)
        self.menu.addAction(self.act_reasoning)
        self.menu.addSeparator()
        self.menu.addAction(self.act_save)
        self.menu.addSeparator()
        self.menu.addMenu(self.theme_menu)
        self.menu.addSeparator()
        self.menu.addAction(self.act_about)

        self.btn_menu = QToolButton()
        self.btn_menu.setText(f"☰ {APPLICATION_TITLE}")
        self.btn_menu.setToolTip("Menu")
        self.btn_menu.setMenu(self.menu)
        self.btn_menu.setPopupMode(QToolButton.InstantPopup)
        self.btn_menu.setStyleSheet("QToolButton{ padding: 2px } QToolButton::menu-indicator{ image: none; }")

        self.btn_open = QToolButton()
        self.btn_open.setDefaultAction(self.act_open_folder)
        self.btn_refresh_folder = QToolButton()
        self.btn_refresh_folder.setDefaultAction(self.act_refresh_folder)

        self.image_count_label = QLabel("")
        self.image_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_show_filter = QToolButton()
        self.btn_show_filter.setDefaultAction(self.act_show_filter)

        left_toolbar_layout.addWidget(self.btn_menu)
        left_toolbar_layout.addWidget(self.btn_open)
        left_toolbar_layout.addWidget(self.btn_refresh_folder)
        left_toolbar_layout.addStretch()
        left_toolbar_layout.addWidget(self.image_count_label)
        left_toolbar_layout.addWidget(self.btn_show_filter)

        # File filter - QLineEdit with clear button
        self.file_filter_edit = QLineEdit()
        self.file_filter_edit.setPlaceholderText("Filter...")
        self.file_filter_edit.setClearButtonEnabled(True)
        self.file_filter_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.file_filter_edit.textChanged.connect(self._on_filter_changed)
        self.file_filter_edit.setVisible(False)
        self.file_filter_edit.installEventFilter(self)

        # File list - create model and proxy
        self.file_model = ImageFileListModel(folder_path)
        self.file_model.entryRenamed.connect(self._on_entry_renamed)

        self.file_proxy_model = FileFilterProxyModel(self)
        self.file_proxy_model.setSourceModel(self.file_model)
        self.file_proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)

        self.file_view = QTreeView()
        self.file_view.setModel(self.file_proxy_model)
        self.file_view.setRootIsDecorated(False)
        self.file_view.setSelectionMode(QTreeView.SingleSelection)
        self.file_view.setSelectionBehavior(QTreeView.SelectRows)
        self.file_view.setEditTriggers(QTreeView.DoubleClicked | QTreeView.SelectedClicked | QTreeView.EditKeyPressed)
        self.file_view.setUniformRowHeights(True)
        self.file_view.setAlternatingRowColors(True)
        self.file_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_view.customContextMenuRequested.connect(self._on_file_view_context_menu)
        self.file_view.header().setStretchLastSection(False)
        self.file_view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_view.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.file_view.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.file_view.setItemDelegate(ColorPreservingDelegate(self.file_view))
        self.file_view.selectionModel().selectionChanged.connect(self._on_item_selected)

        # Tab widget at the bottom of left panel
        self.config_tabs = ResizingTabWidget()
        self.config_tabs.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)

        self.btn_save_folder_config = QPushButton("💾")
        self.btn_save_folder_config.setToolTip("Save in current folder")
        self.btn_save_folder_config.setStyleSheet("padding: 2px")
        self.btn_save_folder_config.setEnabled(False)
        self.btn_save_folder_config.clicked.connect(self.save_folder_config)
        self.config_tabs.setCornerWidget(self.btn_save_folder_config)

        # Highlight/"trigger" words tab
        highlight_words_tab = QWidget()
        highlight_words_tab_layout = QVBoxLayout(highlight_words_tab)
        highlight_words_tab_layout.setContentsMargins(0, 0, 0, 0)
        highlight_words_tab.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.highlight_words_widget = HighlightWordsWidget()
        self.highlight_words_widget.textChanged.connect(self.on_highlight_words_changed)
        self.highlight_words_widget.word_clicked.connect(self._on_highlight_word_clicked)
        self.highlight_words_widget.sizeHintChanged.connect(self.config_tabs.updateGeometry)

        self.config_tabs.addTab(self.highlight_words_widget, "Trigger words")

        # Batch captioning tab
        batch_tab = QWidget()
        batch_tab_layout = QVBoxLayout(batch_tab)
        batch_tab_layout.setContentsMargins(4, 4, 4, 4)
        batch_tab_layout.setSpacing(6)

        # Captioner URL display + settings button
        self.batch_url_display = QLineEdit()
        self.batch_url_display.setReadOnly(True)
        self.btn_batch_captioner_settings = QPushButton("⚙")
        self.btn_batch_captioner_settings.setToolTip("Open captioner settings")
        self.btn_batch_captioner_settings.setStyleSheet("padding: 2px")
        self.btn_batch_captioner_settings.clicked.connect(self._on_batch_captioner_settings)

        url_row = QHBoxLayout()
        url_row.addWidget(self.batch_url_display, 1)
        url_row.addWidget(self.btn_batch_captioner_settings)
        batch_tab_layout.addLayout(url_row)

        # Options group
        options_layout = QVBoxLayout()
        options_layout.setSpacing(4)

        # Existing captions: skip or overwrite
        self.batch_existing_group = QButtonGroup(self)
        self.batch_existing_group.setExclusive(True)
        self.batch_skip = QRadioButton("Skip existing")
        self.batch_skip.setChecked(True)
        self.batch_overwrite = QRadioButton("Overwrite existing")
        self.batch_existing_group.addButton(self.batch_skip)
        self.batch_existing_group.addButton(self.batch_overwrite)

        options_layout.addWidget(self.batch_skip)
        options_layout.addWidget(self.batch_overwrite)

        self.batch_reasoning = QCheckBox("Enable reasoning")
        self.batch_reasoning.setChecked(self.act_reasoning.isChecked())
        self.batch_reasoning.toggled.connect(self._on_batch_reasoning_changed)
        options_layout.addWidget(self.batch_reasoning)
        batch_tab_layout.addLayout(options_layout)

        # Progress bar (hidden initially)
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        self.batch_progress.setFormat("%v of %m complete")
        batch_tab_layout.addWidget(self.batch_progress)

        # Start/Stop button (centered)
        self.btn_batch_generate = QPushButton("Generate")
        self.btn_batch_generate.clicked.connect(self._on_batch_generate)
        batch_tab_layout.addStretch()
        batch_tab_layout.addWidget(self.btn_batch_generate, 0, Qt.AlignHCenter)
        batch_tab_layout.addStretch()
        self._update_batch_start_enabled()

        self.config_tabs.addTab(batch_tab, "Batch captioning")

        # Captioner URL + prompt live in the caption generation panel
        self._caption_panel = CaptionGenerationPanel()
        self._generation_console = self._caption_panel.console
        self.captioner_url_edit = self._caption_panel.captioner_url_edit
        self.captioner_url_edit.setPlaceholderText(DEFAULT_SERVER_URL)
        self.captioner_url_edit.textChanged.connect(self._update_batch_url_display)
        self._caption_panel.captioner_timeout_spinbox.setValue(DEFAULT_REQUEST_TIMEOUT)
        self.captioner_prompt_edit = self._caption_panel.captioner_prompt_edit
        self.captioner_prompt_edit.textChanged.connect(self.on_captioner_prompt_changed)
        # Connect caption panel buttons
        self._caption_panel.btn_generate.clicked.connect(self.on_generate_caption)
        self._caption_panel.btn_append.clicked.connect(self._append_caption)
        self._caption_panel.btn_replace.clicked.connect(self._replace_caption)

        # Add left_toolbar to left_panel
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        # left_layout.setSpacing(4)
        left_layout.addWidget(self.left_toolbar)
        left_layout.addWidget(self.file_filter_edit)
        left_layout.addWidget(self.file_view, 1)
        left_layout.addWidget(self.config_tabs, 0)

        # --- Right toolbar (above image_widget) ---
        self.right_toolbar = QWidget()
        right_toolbar_layout = QHBoxLayout(self.right_toolbar)
        right_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        right_toolbar_layout.setSpacing(4)

        self.btn_undo = QToolButton()
        self.btn_undo.setDefaultAction(self.act_undo)

        self.btn_redo = QToolButton()
        self.btn_redo.setDefaultAction(self.act_redo)

        self.btn_clear_mask = QToolButton()
        self.btn_clear_mask.setDefaultAction(self.act_clear_mask)

        self.btn_invert_mask = QToolButton()
        self.btn_invert_mask.setDefaultAction(self.act_invert_mask)

        self.btn_outline = QToolButton()
        self.btn_outline.setStyleSheet("QToolButton:checked{ margin: 1px; padding: 1px; border: 1px dotted palette(text); border-radius: 2px; background: transparent }")
        self.btn_outline.setDefaultAction(self.act_outline)

        self.btn_hide_image = QToolButton()
        self.btn_hide_image.setDefaultAction(self.act_hide_image)

        self.brush_size_slider = QSlider(Qt.Horizontal)
        self.brush_size_slider.setRange(1, MAX_BRUSH_SIZE)
        self.brush_size_slider.setValue(32)
        self.brush_size_label = QLabel("Size: 32")
        self.brush_size_slider.valueChanged.connect(self.set_brush_radius)

        self.brush_value_slider = QSlider(Qt.Horizontal)
        self.brush_value_slider.setRange(0, 20)
        self.brush_value_slider.setValue(20)
        self.brush_value_slider.setPageStep(1)
        self.brush_value_label = QLabel("Value: 1.0")
        self.brush_value_slider.valueChanged.connect(self.on_brush_value_changed)

        self.btn_save_current = QToolButton()
        self.btn_save_current.setDefaultAction(self.act_save)
        self.btn_save_current.setObjectName("green")

        # Image info labels
        self.image_res_label = QLabel("Image 0×0")
        self.mask_res_label = QLabel("Mask 0×0")
        self.mask_res_label.setStyleSheet("color: lightblue;")
        self.mask_res_label.setVisible(False)

        self.caption_size_label = QLabel("Caption size: 0")
        self.caption_size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.caption_size_label.setStyleSheet(f"color: {ImageFileListModel.MISSING_ITEM_COLOR.name()}")

        self.btn_reasoning = QToolButton()
        self.btn_reasoning.setDefaultAction(self.act_reasoning)
        # Set text and tooltip based on checked status
        self._on_reasoning_switched()

        right_toolbar_layout.addWidget(self.btn_undo)
        right_toolbar_layout.addWidget(self.btn_redo)
        right_toolbar_layout.addSpacing(4)
        right_toolbar_layout.addWidget(self.btn_clear_mask)
        right_toolbar_layout.addWidget(self.btn_invert_mask)
        right_toolbar_layout.addSpacing(4)
        right_toolbar_layout.addWidget(self.btn_outline)
        right_toolbar_layout.addWidget(self.btn_hide_image)
        right_toolbar_layout.addSpacing(4)
        right_toolbar_layout.addWidget(self.brush_size_label)
        right_toolbar_layout.addWidget(self.brush_size_slider)
        right_toolbar_layout.addSpacing(4)
        right_toolbar_layout.addWidget(self.brush_value_label)
        right_toolbar_layout.addWidget(self.brush_value_slider)
        right_toolbar_layout.addStretch()
        right_toolbar_layout.addSpacing(4)
        right_toolbar_layout.addWidget(self.btn_save_current)

        self.image_frame = QFrame()
        frame_layout = QVBoxLayout(self.image_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        self.image_widget = ImageWidget()
        frame_layout.addWidget(self.image_widget)
        self.image_frame.setMinimumHeight(100)

        # ImageWidget connections
        self.act_undo.triggered.connect(self.image_widget.undo)
        self.act_redo.triggered.connect(self.image_widget.redo)
        self.act_clear_mask.triggered.connect(self.image_widget.clear_mask)
        self.act_invert_mask.triggered.connect(self.image_widget.invert_mask)

        self.image_widget.undo_stack.canUndoChanged.connect(self.act_undo.setEnabled)
        self.image_widget.undo_stack.canRedoChanged.connect(self.act_redo.setEnabled)

        self.act_outline.toggled.connect(self.image_widget.set_outline_mode)
        self.act_hide_image.toggled.connect(self.image_widget.set_hide_image_mode)

        self.image_widget.maskModified.connect(self.on_mask_modified)
        self.image_widget.brushRadiusChanged.connect(self.set_brush_radius)

        self.caption_edit = CaptionEdit()
        self.caption_edit.setPlaceholderText("Caption text...")

        # CaptionEdit connections
        self.caption_edit.navigateUp.connect(self.navigate_up)
        self.caption_edit.navigateDown.connect(self.navigate_down)
        self.caption_edit.saveRequested.connect(self.save_current_item)
        self.caption_edit.textChanged.connect(self.on_caption_text_changed)

        self._view_splitter = QSplitter(Qt.Vertical)
        self._view_splitter.addWidget(self.image_frame)
        self._view_splitter.addWidget(self._caption_panel)
        self._view_splitter.setCollapsible(0, False)
        self._view_splitter.setCollapsible(1, False)
        self._view_splitter.setStretchFactor(0, 0)
        self._view_splitter.setStretchFactor(1, 1)
        self._caption_panel.setVisible(False)

        # Mode bar: single row with:
        #   - left container: image/mask info (normal) or tab bar (caption mode)
        #   - center: action buttons (always centered in full row)
        #   - right container: caption size label + panel toggle
        # Wheel events on the tab bar are consumed by eventFilter() below.
        self._info_stack = QStackedWidget()

        # Normal mode: image/mask info on the left
        normal_info = QWidget()
        normal_info_layout = QHBoxLayout(normal_info)
        normal_info_layout.setContentsMargins(0, 0, 0, 0)
        normal_info_layout.setSpacing(4)
        normal_info_layout.addWidget(self.image_res_label)
        normal_info_layout.addWidget(self.mask_res_label)
        normal_info_layout.addStretch()
        self._info_stack.addWidget(normal_info)

        # Caption mode: tab bar on the left
        caption_info = QWidget()
        caption_info_layout = QHBoxLayout(caption_info)
        caption_info_layout.setContentsMargins(0, 0, 0, 0)
        caption_info_layout.setSpacing(2)
        caption_info_layout.addWidget(self._caption_panel.tab_bar)
        caption_info_layout.addStretch()
        self._info_stack.addWidget(caption_info)
        self._caption_panel.tab_bar.installEventFilter(self)

        self.btn_caption_panel = QToolButton()
        self.btn_caption_panel.setDefaultAction(self.act_toggle_caption_panel)

        # Left container: wraps the mode stack (image/mask info or tab bar)
        # No top margin: the caption panel and tab bar should be flush.
        left_container = QWidget()
        left_container_layout = QHBoxLayout(left_container)
        left_container_layout.setContentsMargins(0, 0, 0, 2)
        left_container_layout.setSpacing(2)
        left_container_layout.addWidget(self._info_stack, 1)

        # Right container: caption size label + toggle
        right_container = QWidget()
        right_container_layout = QHBoxLayout(right_container)
        right_container_layout.setContentsMargins(0, 4, 0, 2)
        right_container_layout.setSpacing(2)
        right_container_layout.addStretch()
        right_container_layout.addWidget(self.caption_size_label)
        right_container_layout.addWidget(self.btn_caption_panel)
        # Stable minimum width so button centering doesn't shift when
        # Main row: left container | centered buttons | right container
        # Both containers use stretch=1 so they always grow equally,
        # keeping the buttons centered regardless of content size changes.
        self.middle_layout = QHBoxLayout()
        self.middle_layout.setContentsMargins(4, 0, 4, 2)
        self.middle_layout.setSpacing(2)

        # Container for caption-mode buttons (Generate, Append, Replace, reasoning)
        # Toggled as a group when caption panel is active/inactive.
        self.caption_btn_container = QWidget()
        caption_btn_layout = QHBoxLayout(self.caption_btn_container)
        caption_btn_layout.setContentsMargins(0, 0, 0, 0)
        caption_btn_layout.setSpacing(2)
        caption_btn_layout.addWidget(self._caption_panel.btn_append)
        caption_btn_layout.addWidget(self._caption_panel.btn_replace)
        caption_btn_layout.addWidget(self.btn_reasoning)
        caption_btn_layout.addWidget(self._caption_panel.btn_generate)

        self.middle_layout.addWidget(left_container, 1)
        self.middle_layout.addStretch()
        self.middle_layout.addWidget(self.caption_btn_container)
        self.middle_layout.addStretch()
        self.middle_layout.addWidget(right_container, 1)

        # Hide caption-mode buttons initially (caption panel starts off).
        self.caption_btn_container.setVisible(False)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        # No gap between the view splitter and the mode bar, so the caption
        # tab bar (RoundedSouth) sits flush under the panel's stack.
        right_layout.setSpacing(0)
        right_layout.addWidget(self.right_toolbar)
        right_layout.addSpacing(4)
        right_layout.addWidget(self._view_splitter, 1)
        right_layout.addLayout(self.middle_layout)
        right_layout.addWidget(self.caption_edit, 0)

        # Create syntax highlighter
        self.caption_highlighter = CaptionSyntaxHighlighter(self.caption_edit.document())

        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.addWidget(left_panel)
        self._main_splitter.addWidget(right_panel)
        self._main_splitter.setCollapsible(0, False)
        self._main_splitter.setCollapsible(1, False)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 3)
        self._main_splitter.setSizes([300, 900])

        central = QWidget()
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.addWidget(self._main_splitter)
        self.setCentralWidget(central)

        # Keyboard shortcuts
        # F2: in file_view only - rename entry
        QShortcut(QKeySequence("F2"), self.file_view, self._on_rename_item)
        # [ and ]: change brush size
        QShortcut(QKeySequence("["), self, self.image_widget.decrease_brush_size)
        QShortcut(QKeySequence("]"), self, self.image_widget.increase_brush_size)

        # QSettings initialization (injected for testability)
        self._settings = settings or QSettings(APPLICATION_TITLE, APPLICATION_TITLE)

        self.resize(DEFAULT_WINDOW_SIZE)
        self.load_settings()

        self._apply_color_scheme()
        QApplication.styleHints().colorSchemeChanged.connect(self.on_color_scheme_changed)

        # Set initial hide image mode if requested via command line
        if hide_image_mode:
            self.act_hide_image.setChecked(True)

        # Open folder if provided via command line
        if folder_path:
            self.open_folder(folder_path)
        else:
            self._saved_captioner_prompt = DEFAULT_CAPTIONER_PROMPT
            self.captioner_prompt_edit.setPlainText(self._saved_captioner_prompt)

    def on_color_scheme_changed(self, new_scheme):
        # Only react to OS theme changes if user selected "system"
        if self._color_theme_pref == "system":
            apply_palette(new_scheme)

    def _set_color_theme(self, theme: str):
        self._color_theme_pref = theme
        self._apply_color_scheme()

    def _apply_color_scheme(self):
        if self._color_theme_pref == "dark":
            self.act_theme_dark.setChecked(True)
            color_scheme = Qt.ColorScheme.Dark
        elif self._color_theme_pref == "light":
            color_scheme = Qt.ColorScheme.Light
            self.act_theme_light.setChecked(True)
        else:
            color_scheme = Qt.ColorScheme.Unknown
            self.act_theme_system.setChecked(True)
        apply_palette(color_scheme)

    # ------------------------------------------------------------------
    # Window state persistence (QSettings)
    # ------------------------------------------------------------------
    def load_settings(self):
        # Color theme
        theme_pref = self._settings.value(SETTINGS_KEY_THEME, "system", type=str)
        if theme_pref in ("system", "dark", "light"):
            self._color_theme_pref = theme_pref

        # Window geometry
        geom = self._settings.value(SETTINGS_KEY_GEOMETRY)
        if geom is not None:
            self.restoreGeometry(geom)

        # Window state (maximized, etc.)
        state = self._settings.value(SETTINGS_KEY_STATE)
        if state is not None:
            self.restoreState(state)

        # Main splitter (left panel vs right panel)
        main_splitter_state = self._settings.value(SETTINGS_KEY_MAIN_SPLITTER)
        if main_splitter_state is not None:
            self._main_splitter.restoreState(main_splitter_state)

        # View splitter (image vs caption panel)
        view_splitter_state = self._settings.value(SETTINGS_KEY_VIEW_SPLITTER)
        if view_splitter_state is not None:
            self._view_splitter.restoreState(view_splitter_state)

        # Captioner URL and timeout
        url = self._settings.value(SETTINGS_KEY_SERVER_URL) or DEFAULT_SERVER_URL
        timeout = self._settings.value(SETTINGS_KEY_TIMEOUT) or DEFAULT_REQUEST_TIMEOUT
        reasoning = self._settings.value(SETTINGS_KEY_REASONING, True, type=bool)

        self.captioner_url_edit.setText(url)
        self._caption_panel.captioner_timeout_spinbox.setValue(timeout)
        self.act_reasoning.setChecked(bool(reasoning))
        self._update_batch_url_display()

    def save_settings(self):
        self._settings.setValue(SETTINGS_KEY_THEME, self._color_theme_pref)

        self._settings.setValue(SETTINGS_KEY_GEOMETRY, self.saveGeometry())
        self._settings.setValue(SETTINGS_KEY_STATE, self.saveState())
        self._settings.setValue(SETTINGS_KEY_MAIN_SPLITTER, self._main_splitter.saveState())
        self._settings.setValue(SETTINGS_KEY_VIEW_SPLITTER, self._view_splitter.saveState())

        # Save captioner settings on close
        self._settings.setValue(SETTINGS_KEY_SERVER_URL, self.captioner_url_edit.text().strip())
        self._settings.setValue(SETTINGS_KEY_TIMEOUT, self._caption_panel.captioner_timeout_spinbox.value())
        self._settings.setValue(SETTINGS_KEY_REASONING, self.act_reasoning.isChecked())

    def closeEvent(self, event):
        """Save window and captioner settings when closing."""
        self.save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def _on_caption_panel_toggled(self, checked: bool):
        """Show/hide the caption generation panel and swap the mode bar page."""
        self._caption_panel.setVisible(checked)
        self._info_stack.setCurrentIndex(1 if checked else 0)

        generating = (self._caption_request is not None)
        self.caption_btn_container.setVisible(checked or generating)

    def _set_generating_ui(self, active: bool):
        """Update caption action buttons and editor state during generation.

        - While generating: Generate button becomes 'Cancel'; Append/Replace hidden.
        - After done or error: Generate back to 'Generate'; container and Append/Replace
          are controlled by _on_caption_panel_toggled and _on_caption_generated.
        - 'Batch Generate' is disabled while a single caption is generating.
        """
        self._caption_panel.btn_generate.setText("Cancel" if active else "Generate")
        self._caption_panel.btn_append.setVisible(False)
        self._caption_panel.btn_replace.setVisible(False)
        self.caption_edit.setEnabled(not active)
        self._update_batch_start_enabled()

    def _end_generating_ui(self):
        """Restore caption UI and file list after generation or batch ends."""
        self._set_generating_ui(False)
        self._caption_panel.btn_generate.setEnabled(True)
        self.file_view.setEnabled(True)

    def _refresh_caption_bar_layout(self):
        """Force a full layout pass of the mode bar.

        Ensures the layout updates cleanly when buttons change visibility.
        """
        self.middle_layout.activate()

    def set_brush_radius(self, value):
        self.brush_size_slider.setValue(value)
        self.brush_size_label.setText(f"Size: {value:>3}")
        self.image_widget.brush_radius = value
        self.image_widget.update()

    def _update_dirty_state(self):
        if self._current_index is not None:
            saved_caption = self.file_model.get_caption_text(self._current_index.row())
        else:
            saved_caption = ""
        caption_changed = (self.caption_edit.toPlainText() != saved_caption)
        mask_changed = self.image_widget.is_mask_dirty()
        self._dirty = (caption_changed or mask_changed)
        self._update_save_button()
        self.caption_edit.setStyleSheet("QPlainTextEdit{border-color: green}" if caption_changed else "")
        frame_color = "green" if mask_changed else "transparent"
        self.image_frame.setStyleSheet(f"QFrame{{ border: 1px solid {frame_color}; background: #202020; }}")

    def _update_config_save_button_state(self):
        """Update save button state based on whether folder config differs from saved state.

        Server URL and timeout are auto-saved to QSettings on change, so they
        are not tracked as unsaved folder config.
        """
        highlight_words_changed = (
            self.highlight_words_widget.text().strip() != self._saved_highlight_words
        )
        self.highlight_words_widget.setChanged(highlight_words_changed)
        captioner_changed = (
            self.captioner_prompt_edit.toPlainText().strip() != self._saved_captioner_prompt
        )
        self.captioner_prompt_edit.setStyleSheet(
            "QPlainTextEdit{border-color: green}" if captioner_changed else ""
        )
        hide_mode_changed = (
            self.act_hide_image.isChecked() != self._saved_hide_mode
        )
        self.btn_save_folder_config.setEnabled(
            highlight_words_changed
            or captioner_changed
            or hide_mode_changed
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape and self.file_filter_edit.hasFocus():
            if self.file_filter_edit.text() != "":
                self.file_filter_edit.clear()
            else:
                self.act_show_filter.toggle()
        super().keyPressEvent(event)

    def _on_open_folder(self):
        # If batch is running, warn user but don't stop it yet
        if self._batch_index is not None:
            reply = QMessageBox.warning(
                self,
                "Open Folder",
                "Opening a new folder will stop batch generation. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        folder = QFileDialog.getExistingDirectory(self, "Open Folder", self.file_model.folder())
        if not folder:
            return
        self.open_folder(folder)

    def _refresh_current_folder(self):
        # If batch generation is running, ask for confirmation
        if self._batch_index is not None:
            reply = QMessageBox.question(
                self,
                "Cancel Batch Generation?",
                "Batch generation is currently running.\nDo you want to cancel and refresh the folder?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                # Stop the batch using existing handler
                self._on_batch_generate()
            else:
                # User chose not to cancel
                return

        folder = self.file_model.folder()
        self.open_folder(folder)

    def _on_show_filter_changed(self, checked: bool):
        if checked:
            self.file_filter_edit.show()
            self.file_filter_edit.setFocus()
        else:
            self.file_filter_edit.clear()
            self.file_filter_edit.hide()

    def eventFilter(self, obj, event: QEvent):
        """Event filters: file_filter_edit (hide on focus out with empty text)
        and the caption tab bar (consume wheel events so scrolling never
        switches tabs)."""
        if obj is self.file_filter_edit and event.type() == QEvent.Type.FocusOut:
            if not self.file_filter_edit.text():
                self.act_show_filter.setChecked(False)
                self.file_filter_edit.hide()
        elif obj is self._caption_panel.tab_bar and event.type() == QEvent.Type.Wheel:
            return True  # Consume the event, preventing tab switching
        return super().eventFilter(obj, event)

    def _on_filter_changed(self, text: str):
        self.file_proxy_model.setFilterRegularExpression(text)
        # Update image count label to reflect filtered results
        total_count = self.file_model.rowCount()
        count = self.file_proxy_model.rowCount()
        if (count != total_count):
            self.image_count_label.setText(f"{count} of {total_count} images")
        else:
            self.image_count_label.setText(f"{total_count} images")
        self._update_batch_start_enabled()

    def _cancel_caption_request(self):
        """Cancel any in-flight caption generation request."""
        if self._caption_request is not None:
            self._caption_request.cancel()
            self._caption_request = None
            self._set_generating_ui(False)
            self.file_view.setEnabled(True)
            # Hide caption generation buttons if they were visible during generation
            if not self._caption_panel.isVisible():
                self.caption_btn_container.setVisible(False)

    def open_folder(self, folder: str = ""):
        if not folder:
            return

        # If batch is running and we’re switching folders, stop the batch
        current_folder = self.file_model.folder() or ""
        if self._batch_index is not None and folder != current_folder:
            self._on_batch_generate()  # triggers the Stop branch

        # Cancel any in-flight caption request before switching folder
        self._cancel_caption_request()
        self._generation_console.clear()

        self.file_model.scan_folder(folder)
        self._current_index = None

        self.caption_edit.clear()
        self._dirty = False
        self._update_save_button()

        # Clear the filter when opening a new folder
        self.file_filter_edit.clear()

        folder_name = self.file_model.folder()
        self.update_title(folder_name)
        total_count = self.file_model.rowCount()
        self.image_count_label.setText(f"{total_count} images")
        self.act_refresh_folder.setEnabled(True)
        self._update_batch_start_enabled()

        # Load config
        self.load_folder_config()

        if self.file_proxy_model.rowCount() > 0:
            self.file_view.setCurrentIndex(self.file_proxy_model.index(0, 0))
        else:
            # Empty folder: clear image widget so previous image is not editable
            self._current_index = None
            self._clear_image_widget()
        self.file_view.setFocus()

    def _clear_image_widget(self):
        """Clear ImageWidget and mask-edit actions when no file is selected."""
        self.image_widget.original_image = QImage()
        self.image_widget.mask_image = QImage()
        self.image_widget.cached_image = None
        self.image_widget.cached_thumbnail = None
        self.image_widget.cached_mask = None
        self.image_widget.undo_stack.clear()
        self.image_widget.update()

        self.act_undo.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.act_clear_mask.setEnabled(False)
        self.act_invert_mask.setEnabled(False)
        self._caption_panel.btn_generate.setEnabled(False)

    def _on_item_selected(self, selected, deselected):
        indexes = selected.indexes()
        if not indexes:
            return
        idx = indexes[0]
        # Map proxy index to source model index
        src_idx = self.file_proxy_model.mapToSource(idx)
        base_name = self.file_model.data(src_idx, Qt.DisplayRole)
        if src_idx == self._current_index and not self._dirty:
            return

        # Cancel any in-flight caption request before loading new file
        self._cancel_caption_request()

        # Discard pending caption so Append/Replace don't apply to the new file
        self._pending_caption = None
        self._caption_panel.btn_append.setVisible(False)
        self._caption_panel.btn_replace.setVisible(False)

        # Sync model state with actual disk state before loading
        self.file_model.sync_item(src_idx.row())

        folder = self.file_model.folder()

        img_path = None
        for ext in SUPPORTED_EXTENSIONS:
            assert type(base_name) is str
            path = os.path.join(folder, base_name + ext)
            if os.path.isfile(path):
                img_path = path
                break
        original_image: QImage | None = None
        if img_path:
            try:
                img_reader = QImageReader(img_path)
                original_image = img_reader.read()
                if original_image.isNull():
                    qWarning(f"[IMG] {base_name}: QImageReader error: {img_reader.errorString()} format={img_reader.format()}")
                    original_image = None
                else:
                    if original_image.format() not in (QImage.Format_RGB32, QImage.Format_RGBA8888):
                        original_image = original_image.convertToFormat(QImage.Format_RGB32)
            except (OSError, RuntimeError) as e:
                qWarning(f"[IMG] {base_name}: ERROR {e}")
                original_image = None

        mask_path = os.path.join(folder, mask_filename(base_name))
        mask: QImage | None = None
        if os.path.isfile(mask_path):
            try:
                mask_reader = QImageReader(mask_path)
                mask = mask_reader.read()
                if mask.isNull():
                    qWarning(f"[MASK] {base_name}: QImageReader error: {mask_reader.errorString()}")
                    mask = None
                elif mask.format() != QImage.Format_Grayscale8:
                    converted = mask.convertToFormat(QImage.Format_Grayscale8)
                    if not converted.isNull():
                        mask = converted
                    else:
                        mask = None
            except (OSError, RuntimeError):
                mask = None

        if mask is None:
            # Use original image size for mask, or default to 512x512
            if original_image is not None:
                mask = QImage(original_image.width(), original_image.height(), QImage.Format_Grayscale8)
            else:
                mask = QImage(512, 512, QImage.Format_Grayscale8)
            mask.fill(255)

        self._current_index = src_idx
        self.image_widget.set_image_missing(original_image is None)
        self.image_widget.load_images(original_image, mask)

        # Enable generate button only if the selected entry has an image
        self._caption_panel.btn_generate.setEnabled(original_image is not None)

        # Caption text comes from the model (single source of truth);
        # sync_selection() above already aligned the entry with disk
        caption = self.file_model.get_caption_text(src_idx.row())

        self.caption_edit.blockSignals(True)
        self.caption_edit.setPlainText(caption)
        self.caption_edit.blockSignals(False)

        self._hover_pos = None
        self._update_dirty_state()
        self.update_resolution_info()
        self.update_caption_size()

    def update_title(self, text = ""):
        title = APPLICATION_TITLE
        if text:
            title += f" - {text}"
        self.setWindowTitle(title)

    def on_mask_modified(self):
        self._update_dirty_state()
        self.update_resolution_info()

    def on_highlight_words_changed(self, text: str):
        """Update highlighter trigger words when words change.

        Words are space-separated. Also updates the caption editor's
        syntax highlighter.
        """
        patterns = words_to_regex_patterns(text)
        self.caption_highlighter.set_highlight_regex(patterns)

        # A commit rebuilds the chips without any selection: dimming follows
        # chip selection (see _on_highlight_word_clicked), so clear patterns
        self.file_proxy_model.set_compiled_patterns([])
        self.file_proxy_model.invalidate()

        self._update_config_save_button_state()

    def _on_highlight_word_clicked(self, word: str):
        """Handle single-click on a highlight word chip.

        Filters the file view to show only entries whose filename
        contains the clicked trigger word. Empty string clears the filter.
        """
        pattern = None
        if word != "":
            pattern = words_to_regex_patterns(word)
        if pattern:
            self.file_proxy_model.set_compiled_patterns(pattern)
        else:
            # Clear filter - reset to show all files
            self.file_proxy_model.set_compiled_patterns([])
        self.file_proxy_model.invalidate()

    def load_folder_config(self):
        """Load folder-specific configuration from TOML config file."""
        folder = self.file_model.folder()
        if not folder:
            return
        config = load_config(folder)
        self._saved_highlight_words = config["trigger_words"].strip()
        cfg_prompt = config["captioner_prompt"].strip()
        # Use default prompt when config has none; that value is the
        # "effective saved" one so the change indicator stays correct.
        self._saved_captioner_prompt = cfg_prompt or DEFAULT_CAPTIONER_PROMPT
        self._saved_hide_mode = bool(config.get("hide_mode", False))

        if self._saved_hide_mode:
            self.act_hide_image.setChecked(True)

        self.highlight_words_widget.setText(self._saved_highlight_words)
        patterns = words_to_regex_patterns(self._saved_highlight_words)
        self.caption_highlighter.set_highlight_regex(patterns)

        self.highlight_words_widget.clear_selection()
        self.file_proxy_model.set_compiled_patterns([])
        self.file_proxy_model.invalidate()

        self.captioner_prompt_edit.setPlainText(self._saved_captioner_prompt)
        self._update_config_save_button_state()

    def save_folder_config(self):
        """Save folder-specific configuration (trigger words, captioner prompt, hide mode)."""
        folder = self.file_model.folder()
        if not folder:
            return
        trigger_words = self.highlight_words_widget.text().strip()
        captioner_prompt = self.captioner_prompt_edit.toPlainText().strip()
        hide_mode_enabled = self.act_hide_image.isChecked()

        # Only persist captioner_prompt if it differs from the default.
        prompt_to_save = captioner_prompt if captioner_prompt != DEFAULT_CAPTIONER_PROMPT else ""
        if save_config(
            folder,
            trigger_words,
            prompt_to_save,
            hide_mode=hide_mode_enabled,
        ):
            self._saved_highlight_words = trigger_words
            # Track the effective "saved" prompt (default when equal, else user value).
            self._saved_captioner_prompt = captioner_prompt if prompt_to_save else DEFAULT_CAPTIONER_PROMPT
            self._saved_hide_mode = hide_mode_enabled
            self._update_config_save_button_state()

    def on_captioner_prompt_changed(self):
        """Handle captioner prompt text changes."""
        self._update_config_save_button_state()

    def on_caption_text_changed(self):
        self._update_dirty_state()
        self.update_caption_size()

    def update_caption_size(self):
        caption_text = self.caption_edit.toPlainText()
        # Each non-empty line is a separate caption entry
        lines = [line for line in caption_text.split('\n') if line.strip()]
        n = len(lines)
        if n > 0:
            max_len = max(len(line.strip()) for line in lines)
            captions_word = "caption" if n == 1 else "captions"
            text = f"{n} {captions_word}, max length {max_len}"
        else:
            text = "0 captions, max length 0"
        entry = None
        if self._current_index is not None:
            row = self._current_index.row()
            if 0 <= row < self.file_model.rowCount():
                entry = self.file_model.entry_at(row)
        if entry and entry.has_caption:
            self.caption_size_label.setStyleSheet("")
        else:
            self.caption_size_label.setStyleSheet(f"color: {ImageFileListModel.MISSING_ITEM_COLOR.name()}")
        self.caption_size_label.setText(text)

    def save_current_item(self):
        if self._current_index is None:
            return
        folder = self.file_model.folder()
        base_name = self.file_model.data(self._current_index, Qt.DisplayRole)
        app = QApplication.instance()
        app.setOverrideCursor(Qt.BusyCursor)

        caption_text = self.caption_edit.toPlainText()
        cap_path = os.path.join(folder, caption_filename(base_name))
        saved_caption = self.file_model.get_caption_text(self._current_index.row())
        caption_changed = caption_text != saved_caption
        if caption_changed:
            if caption_text.strip() == "":
                if os.path.exists(cap_path):
                    os.remove(cap_path)
            else:
                with open(cap_path, 'w', encoding='utf-8') as f:
                    f.write(caption_text)

        mask_path = os.path.join(folder, mask_filename(base_name))
        if self.image_widget.is_mask_dirty():
            self.image_widget.save_mask(mask_path)

        self.file_model.update_item(self._current_index.row())
        if caption_changed:
            # Update the model's cached caption so highlight/dirty checks
            # read the newly saved text
            self.file_model.set_caption_text(self._current_index.row(), caption_text)
        self.image_widget.undo_stack.setClean()
        self._update_dirty_state()
        self.update_resolution_info()
        self.update_caption_size()
        app.restoreOverrideCursor()

    def navigate_up(self):
        idx = self.file_view.currentIndex()
        if not idx.isValid():
            return
        row = idx.row()
        if row > 0:
            new_idx = self.file_view.model().index(row - 1, 0)
            self.file_view.setCurrentIndex(new_idx)

    def navigate_down(self):
        idx = self.file_view.currentIndex()
        if not idx.isValid():
            return
        row = idx.row()
        total = self.file_view.model().rowCount()
        if row < total - 1:
            new_idx = self.file_view.model().index(row + 1, 0)
            self.file_view.setCurrentIndex(new_idx)

    def _on_rename_item(self):
        if self.file_view.state() == QTreeView.EditingState:
            return
        idx = self.file_view.currentIndex()
        if idx.isValid():
            self.file_view.edit(idx)

    def _on_entry_renamed(self, old_base: str, new_base: str):
        """Handle entry rename from model signal.

        If the renamed entry is currently displayed, clears _current_index
        and re-selects the entry with the new name to trigger a full reload.
        """
        if self._current_index is not None and self.file_model.data(self._current_index, Qt.DisplayRole) == old_base:
            # Clear current index to force _on_item_selected to reload
            self._current_index = None

            # Find the renamed entry and select it (if visible in current filter)
            row = self.file_model.find_entry_by_name(new_base)
            if row >= 0:
                src_idx = self.file_model.index(row, 0)
                proxy_idx = self.file_proxy_model.mapFromSource(src_idx)
                if proxy_idx.isValid():
                    self.file_view.setCurrentIndex(proxy_idx)

    def on_brush_value_changed(self, value: int):
        """Convert integer slider (0-20) to float 0.0-1.0."""
        float_val = value / self.brush_value_slider.maximum()
        self.image_widget.brush_value = float_val
        self.brush_value_label.setText(f"Value: {float_val:.2f}")

    def update_resolution_info(self):
        """Update the resolution info labels based on current image/mask state."""
        entry = None
        if self._current_index is not None:
            row = self._current_index.row()
            if 0 <= row < self.file_model.rowCount():
                entry = self.file_model.entry_at(row)
        img_exists = entry is None or entry.has_image
        original_image = self.image_widget.original_image
        mask = self.image_widget.mask_image

        if original_image is not None:
            img_w, img_h = original_image.width(), original_image.height()
            ar = closest_aspect_ratio(img_w, img_h)
            self.image_res_label.setText(f"{img_w}×{img_h} ({ar})")
            self.image_res_label.setVisible(img_exists)
        else:
            if mask is not None:
                img_w, img_h = mask.width(), mask.height()
                ar = closest_aspect_ratio(img_w, img_h)
                self.image_res_label.setText(f"{img_w}×{img_h} ({ar})")
            self.image_res_label.setVisible(True)

        if mask is not None:
            mask_w, mask_h = mask.width(), mask.height()
        else:
            mask_w, mask_h = 0, 0
        self.mask_res_label.setText(f"Mask {mask_w}×{mask_h}")
        self.mask_res_label.setVisible(entry is not None and entry.has_mask)

    def _update_save_button(self):
        self.act_save.setEnabled(self._dirty)

    # ------------------------------------------------------------------
    # Right-click context menu for file list
    # ------------------------------------------------------------------
    def _on_file_view_context_menu(self, pos):
        """Show context menu on right-click."""
        idx = self.file_view.indexAt(pos)
        if not idx.isValid():
            return

        # Select the clicked row
        self.file_view.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)

        menu = QMenu(self)
        open_action = QAction("Open file (shell)", self)
        rename_action = QAction("Rename", self)
        copy_action = QAction("Copy image to clipboard", self)
        show_action = QAction("Show in file explorer", self)
        menu.addAction(open_action)
        menu.addAction(rename_action)
        menu.addAction(copy_action)
        menu.addSeparator()
        menu.addAction(show_action)

        rename_action.triggered.connect(self._on_rename_item)
        show_action.triggered.connect(self._show_in_explorer)
        open_action.triggered.connect(self._on_open_image_in_shell)
        copy_action.triggered.connect(self._on_copy_image_to_clipboard)

        menu.exec(self.file_view.mapToGlobal(pos))

    def _show_in_explorer(self):
        """Open file explorer with the selected file highlighted."""
        idx = self.file_view.currentIndex()
        if not idx.isValid():
            return

        src_idx = self.file_proxy_model.mapToSource(idx)
        base_name = self.file_model.data(src_idx, Qt.DisplayRole)
        if not base_name:
            return

        folder = self.file_model.folder()

        # Find the image file path to highlight in Explorer
        file_path = None
        for ext in SUPPORTED_EXTENSIONS:
            assert type(base_name) is str
            path = os.path.join(folder, base_name + ext)
            if os.path.isfile(path):
                file_path = path
                break

        # Fallback: use the caption or mask file
        if file_path is None:
            cap_path = os.path.join(folder, caption_filename(base_name))
            if os.path.isfile(cap_path):
                file_path = cap_path
            else:
                mask_path = os.path.join(folder, mask_filename(base_name))
                if os.path.isfile(mask_path):
                    file_path = mask_path

        if file_path and os.path.isfile(file_path):
            show_in_file_explorer(os.path.abspath(file_path))

    def _on_open_image_in_shell(self):
        """Open the selected image file in the default application."""
        idx = self.file_view.currentIndex()
        if not idx.isValid():
            return

        src_idx = self.file_proxy_model.mapToSource(idx)
        base_name = self.file_model.data(src_idx, Qt.DisplayRole)
        if not base_name:
            return

        folder = self.file_model.folder()

        # Find the image file path
        file_path = None
        for ext in SUPPORTED_EXTENSIONS:
            assert type(base_name) is str
            path = os.path.join(folder, base_name + ext)
            if os.path.isfile(path):
                file_path = path
                break

        if file_path and os.path.isfile(file_path):
            os.startfile(file_path)

    def _on_copy_image_to_clipboard(self):
        """Copy the selected image to the clipboard."""
        img = self.image_widget.original_image
        if img is not None and not img.isNull():
            clipboard = QApplication.clipboard()
            clipboard.setImage(img)

    # ------------------------------------------------------------------
    # Caption generation
    # ------------------------------------------------------------------
    def _on_reasoning_switched(self):
        checked = self.act_reasoning.isChecked()
        if checked:
            self.act_reasoning.setToolTip("Reasoning enabled")
            self.act_reasoning.setIconText("🤔")
        else:
            self.act_reasoning.setToolTip("Reasoning disabled")
            self.act_reasoning.setIconText("😶")
        # Keep reasoning switches in sync
        self.batch_reasoning.blockSignals(True)
        self.batch_reasoning.setChecked(checked)
        self.batch_reasoning.blockSignals(False)

    def on_generate_caption(self):
        """Toggle caption generation/cancellation in the caption panel."""
        # Cancel in-flight request if button says "Cancel"
        if self._caption_request is not None:
            self._cancel_caption_request()
            return

        if self._current_index is None:
            return

        # Check that image exists
        folder = self.file_model.folder()
        base_name = self.file_model.data(self._current_index, Qt.DisplayRole)
        img_path = None
        for ext in SUPPORTED_EXTENSIONS:
            assert type(base_name) is str
            path = os.path.join(folder, base_name + ext)
            if os.path.isfile(path):
                img_path = path
                break
        if img_path is None:
            return

        # Use values from the GUI (already loaded by load_config in open_folder)
        guide_text = self.captioner_prompt_edit.toPlainText()
        server_url = self.captioner_url_edit.text() or DEFAULT_SERVER_URL
        timeout_seconds = self._caption_panel.captioner_timeout_spinbox.value()

        self._caption_request = CaptionRequest(
            server_url,
            timeout_seconds,
            guide_text,
            img_path,
            self.btn_reasoning.isChecked(),
        )

        self._caption_request.progress.connect(self._generation_console.append_output)
        self._caption_request.progress_html.connect(self._generation_console.append_html)
        self._caption_request.finished_caption.connect(self._on_caption_generated)
        self._caption_request.error.connect(self._on_caption_error)

        self._generation_console.clear()
        self._show_generation_console()
        self._set_generating_ui(True)
        self.file_view.setEnabled(False)  # prevent selection changes while generating

        self._caption_request.start()

    def _on_caption_generated(self, caption: str):
        self._set_generating_ui(False)
        self._caption_request = None
        self._pending_caption = caption
        if not self.caption_edit.toPlainText().strip():
            # empty caption, insert automatically and unlock file list
            self._replace_caption()
        else:
            # let user choose Append or Replace
            self._caption_panel.btn_append.setVisible(True)
            self._caption_panel.btn_replace.setVisible(True)
        self.file_view.setEnabled(True)
        self._refresh_caption_bar_layout()

    def _append_caption(self):
        """Append generated caption to the caption editor."""
        if self._pending_caption is None:
            return
        caption = self._pending_caption
        self._pending_caption = None

        cursor = self.caption_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        existing = self.caption_edit.toPlainText()
        if existing and not existing.endswith('\n'):
            cursor.insertText('\n')
        cursor.insertText(caption)
        self.caption_edit.setTextCursor(cursor)

        self._caption_panel.btn_append.setVisible(False)
        self._caption_panel.btn_replace.setVisible(False)
        self.file_view.setEnabled(True)  # user acted, allow selection
        self._update_dirty_state()
        self.update_caption_size()
        self._refresh_caption_bar_layout()

    def _replace_caption(self):
        """Replace the caption editor content with the generated caption."""
        if self._pending_caption is None:
            return

        caption = self._pending_caption
        self._pending_caption = None

        cursor = self.caption_edit.textCursor()
        cursor.select(QTextCursor.Document)
        cursor.insertText(caption)
        self.caption_edit.setTextCursor(cursor)

        self._caption_panel.btn_append.setVisible(False)
        self._caption_panel.btn_replace.setVisible(False)
        self.file_view.setEnabled(True)  # user acted, allow selection
        self._refresh_caption_bar_layout()

    def _on_caption_error(self, error_msg: str):
        """Show error message in the caption panel."""
        self._caption_request = None
        self._set_generating_ui(False)
        self.file_view.setEnabled(True)  # generation ended with error, re-enable
        QMessageBox.critical(self, "Generation Error", error_msg)

    def _on_batch_reasoning_changed(self, checked: bool):
        """Sync reasoning checkbox with the main reasoning action."""
        self.act_reasoning.setChecked(checked)

    def _update_batch_url_display(self, *_):
        """Mirror the captioner server URL in the batch generation tab (default when empty)."""
        self.batch_url_display.setText(self.captioner_url_edit.text() or DEFAULT_SERVER_URL)

    def _on_batch_captioner_settings(self):
        """Open the captioner settings page in the caption panel."""
        self.act_toggle_caption_panel.setChecked(True)
        self._caption_panel.tab_bar.setCurrentIndex(1)

    def _update_batch_start_enabled(self):
        # Batch generation running: keep the button enabled as 'Stop'.
        if self._batch_index is not None:
            self.btn_batch_generate.setEnabled(True)
            return
        # Single caption generating: disable starting batch generation.
        if self._caption_request is not None:
            self.btn_batch_generate.setEnabled(False)
            return
        # Default: based on images count.
        has_images = self.file_proxy_model.rowCount() > 0
        self.btn_batch_generate.setEnabled(has_images)

    def _do_single_batch_caption(self):
        """Start caption generation for the current batch item.

        Reads the item from _batch_items[self._batch_index]. Sets up the CaptionRequest
        with proper signal wiring. The actual completion is handled by
        _on_batch_caption_generated / _on_batch_caption_error.
        """
        if self._batch_index is None:
            return
        _, source_row = self._batch_items[self._batch_index]
        idx = self.file_model.index(source_row, 0)
        if not idx.isValid():
            return
        base_name = self.file_model.data(idx, Qt.DisplayRole)
        folder = self.file_model.folder()

        # Find image path
        img_path = None
        for ext in SUPPORTED_EXTENSIONS:
            assert type(base_name) is str
            path = os.path.join(folder, base_name + ext)
            if os.path.isfile(path):
                img_path = path
                break
        if img_path is None:
            return

        guide_text = self.captioner_prompt_edit.toPlainText()
        server_url = self.captioner_url_edit.text() or DEFAULT_SERVER_URL
        timeout_seconds = self._caption_panel.captioner_timeout_spinbox.value()
        reasoning = self.batch_reasoning.isChecked()

        # Cancel any existing request first
        self._cancel_caption_request()

        self._batch_error_occurred = False

        self._caption_request = CaptionRequest(
            server_url,
            timeout_seconds,
            guide_text,
            img_path,
            reasoning,
        )

        # Wire up signals
        self._caption_request.progress.connect(self._generation_console.append_output)
        self._caption_request.progress_html.connect(self._generation_console.append_html)
        self._caption_request.finished_caption.connect(self._on_batch_caption_generated)
        self._caption_request.error.connect(self._on_batch_caption_error)

        # Show output in the caption panel (auto-opened at batch start)
        self._generation_console.clear()
        self._caption_panel.btn_generate.setEnabled(False)

        self._caption_request.start()

    def _on_batch_caption_generated(self, caption: str):
        """Handle batch caption generation completion."""
        if self._batch_index is None:
            return

        self._caption_request = None

        # Save caption to disk
        base_name, source_row = self._batch_items[self._batch_index]
        idx = self.file_model.index(source_row, 0)
        if not idx.isValid():
            return
        folder = self.file_model.folder()
        cap_path = os.path.join(folder, caption_filename(base_name))
        try:
            with open(cap_path, 'w', encoding='utf-8') as f:
                f.write(caption)
            # Keep the model in sync so indicators and highlight colors update
            self.file_model.set_caption_text(source_row, caption)
        except OSError:
            pass

        # Move to next item
        self._batch_retry_count = 0
        self._batch_index += 1
        self._schedule_next_batch_item()

    def _on_batch_caption_error(self, error_msg: str):
        """Handle batch caption generation error.

        Retries the same item up to 2 times for transient errors.
        If the server reports "image input not supported", stops the batch generation
        immediately with a single clear message (no further retries).
        """
        if self._batch_index is None:
            return

        self._caption_request = None

        # Treat "image not supported" as fatal for the whole batch generation
        if "image input" in error_msg.lower() and "not supported" in error_msg.lower():
            self._batch_index = None
            self._batch_items = []
            self._batch_retry_count = 0
            self._end_generating_ui()
            self.config_tabs.tabBar().setEnabled(True)
            self.batch_progress.setVisible(False)
            self.btn_batch_generate.setText("Generate")
            QMessageBox.critical(
                self,
                "Batch Captioning Error",
                error_msg
            )
            return

        # For other errors, retry up to 2 times
        if self._batch_retry_count < 2:
            self._batch_retry_count += 1
            # Retry same item (don't advance index)
            self._do_single_batch_caption()
        else:
            # Exhausted retries: move to next item
            self._batch_retry_count = 0
            self._batch_index += 1
            self._schedule_next_batch_item()

    def _process_next_batch_item(self):
        if self._batch_index is None or self._batch_index >= self._batch_total:
            # All done: refresh caption for the last item if it's still selected
            if self._batch_items and self._current_index is not None and self._current_index.isValid():
                _, last_row = self._batch_items[-1]
                if self._current_index.row() == last_row:
                    caption = self.file_model.get_caption_text(last_row)
                    self.caption_edit.setPlainText(caption)

            self._end_generating_ui()
            self.config_tabs.tabBar().setEnabled(True)
            self.batch_progress.setVisible(False)
            self.btn_batch_generate.setText("Generate")
            self._batch_index = None
            self._batch_items = []
            return

        base_name, source_row = self._batch_items[self._batch_index]
        folder = self.file_model.folder()

        # Update progress to reflect completed items so far
        self.batch_progress.setValue(self._batch_index)

        # Skip files that already have a caption (unless overwrite is selected)
        if self.batch_skip.isChecked():
            cap_path = os.path.join(folder, caption_filename(base_name))
            if os.path.isfile(cap_path):
                self._batch_index += 1
                self._schedule_next_batch_item()
                return

        # Verify image file exists and is accessible
        img_path = None
        for ext in SUPPORTED_EXTENSIONS:
            path = os.path.join(folder, base_name + ext)
            if os.path.isfile(path):
                img_path = path
                break
        if not img_path:
            # File deleted/inaccessible mid-batch: skip gracefully
            self._generation_console.append_output(
                f"[WARN] Skipping {base_name}: image file not found or inaccessible."
            )
            self._batch_index += 1
            self._schedule_next_batch_item()
            return


        self._batch_retry_count = 0

        # Select the file in the view using stored source_row.
        idx = self.file_model.index(source_row, 0)
        if idx.isValid():
            proxy_idx = self.file_proxy_model.mapFromSource(idx)
            if proxy_idx.isValid():
                self.file_view.setCurrentIndex(proxy_idx)

        # Generate caption
        self._do_single_batch_caption()

    def _show_generation_console(self):
        """Ensure the caption panel is open and on the Console page."""
        self.act_toggle_caption_panel.setChecked(True)
        self._caption_panel.tab_bar.setCurrentIndex(0)

    def _snapshot_batch_items(self):
        """Build a snapshot list of (base_name, source_row) for all currently visible rows."""
        items = []
        total = self.file_proxy_model.rowCount()
        for i in range(total):
            proxy_idx = self.file_proxy_model.index(i, 0)
            src_idx = self.file_proxy_model.mapToSource(proxy_idx)
            base_name = self.file_model.data(src_idx, Qt.DisplayRole)
            if base_name:
                items.append((base_name, src_idx.row()))
        return items

    def _schedule_next_batch_item(self):
        """Queue processing of the next batch item."""
        QTimer.singleShot(0, self._process_next_batch_item)

    def _on_batch_generate(self):
        """Start or stop batch caption generation."""
        # If already running, stop
        if self._batch_index is not None:
            self._batch_index = None
            self._batch_items = []

            self._cancel_caption_request()
            self._end_generating_ui()
            self.config_tabs.tabBar().setEnabled(True)
            self.btn_batch_generate.setText("Generate")
            self.batch_progress.setVisible(False)
            return

        # Build a fixed list of items from the current visible rows.
        batch_items = self._snapshot_batch_items()
        total = len(batch_items)
        if total == 0:
            return

        self._batch_items = batch_items
        self._batch_index = 0
        self._batch_total = total
        self._batch_retry_count = 0
        self.batch_progress.setRange(0, total)
        self.batch_progress.setValue(0)
        self.batch_progress.setVisible(True)
        self.btn_batch_generate.setText("Stop")
        # Disable tab bar so user can't switch away from batch generation view
        self.config_tabs.tabBar().setEnabled(False)
        self.config_tabs.setMaximumHeight(self.config_tabs.sizeHint().height())
        # Auto-open the caption panel on the console page so generation output is visible
        self._show_generation_console()
        # Lock editor and caption buttons for the duration of the batch.
        self._set_generating_ui(True)
        # Keep caption panel button label as "Generate" (batch stop is via batch button)
        self._caption_panel.btn_generate.setText("Generate")
        self.file_view.setEnabled(False)  # prevent selection changes during batch

        # Kick off first item
        self._process_next_batch_item()
