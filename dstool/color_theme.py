from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


color_schemes = {
    Qt.ColorScheme.Light: {
        "base": "#F9F9F9",
        "text": "#1C1C1C",
        "disabled": "#E0E0E0",
        "disabled_text": "#7D7D7D",
        "window": "#CFCFCF",
        "window_section": "#DBDBDB",
        "window_text": "#1C1C1C",
        "highlight": "#3B8ED0",
        "box_frame": "#3E4649",
        "button": "#36719F",
        "green_button": "#226622",
        "toolbutton_checked": "#FFAA00",
        "chip": "#6AA5D2",
        "chip_selected": "#FFAA00",
    },
    Qt.ColorScheme.Dark: {
        "base": "#343638",
        "text": "#DCE4EE",
        "disabled": "#2D2D2D",
        "disabled_text": "#9D9D9D",
        "window": "#2E2E2E",
        "window_text": "#DCE4EE",
        "window_section": "#2B2B2B",
        "highlight": "#195A8C",
        "box_frame": "#656A6E",
        "button": "#103B59",
        "green_button": "#226622",
        "toolbutton_checked": "#CC8800",
        "chip": "#294F69",
        "chip_selected": "#7F5700",
    },
}


def apply_palette(scheme: Qt.ColorScheme):
    QApplication.styleHints().setColorScheme(scheme)

    scheme = QApplication.styleHints().colorScheme()
    colors = color_schemes[scheme]

    # Generate derived colors
    def saturation_adjusted(color, sat_coef, val_coef=1.0) -> QColor:
        new_color = QColor(color).toHsv()
        new_sat = max(0, min(255, int(new_color.saturation() * sat_coef)))
        new_val = max(0, min(255, int(new_color.value() * val_coef)))
        new_color.setHsv(new_color.hsvHue(), new_sat, new_val)
        return new_color
    colors["alt_base"] = QColor(colors["base"]).darker(110).name()

    accent_color = QColor(colors["button"])
    colors["button_hover"] = accent_color.lighter(120).name()
    colors["button_disabled"] = saturation_adjusted(accent_color, 0.2, 0.8).name()
    colors["toolbutton_checked_hover"] = QColor(colors["toolbutton_checked"]).lighter(120).name()
    colors["toolbutton_checked_pressed"] = QColor(colors["toolbutton_checked"]).darker(120).name()
    colors["green_button_hover"] = QColor(colors["green_button"]).lighter(120).name()
    colors["green_button_pressed"] = QColor(colors["green_button"]).darker(120).name()

    chip_hover = QColor(colors["chip"]).lighter(120)
    chip_selected_hover = QColor(colors["chip_selected"]).lighter(120)
    colors["chip_hover"] = chip_hover.name()
    colors["chip_hover_frame"] = chip_hover.darker().name()
    colors["chip_selected_hover"] = chip_selected_hover.name()
    colors["chip_selected_frame"] = QColor(colors["chip_selected"]).darker().name()
    colors["chip_frame"] = QColor(colors["chip"]).darker().name()
    colors["chip_selected_hover_frame"] = chip_selected_hover.darker().name()

    app = QApplication.instance()
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["alt_base"]))
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["window_text"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(colors["disabled"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["button_hover"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(colors["button_disabled"]))

    style_sheet = """
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
            border: 1px solid {box_frame};
            border-radius: 2px;
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border-color: palette(Highlight);
        }}
        QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
            background-color: transparent;
            color: {disabled_text};
        }}
        QToolButton{{
            padding: 2px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {button_hover};
        }}
        QToolButton:checked {{
            background: {toolbutton_checked};
        }}
        QToolButton:checked:hover {{
            background: {toolbutton_checked_hover};
        }}
        QToolButton:checked:pressed {{
            background: {toolbutton_checked_pressed};
        }}
        QToolButton#green {{
            background: {green_button};
        }}
        QToolButton#green:hover {{
            background: {green_button_hover};
        }}
        QToolButton#green:pressed {{
            background: {green_button_pressed};
        }}
        QToolButton#green:disabled {{
            background: {button_disabled};
        }}
        QMenu::item:disabled {{
            color: {disabled_text};
        }}
        QMenu::item:disabled:selected {{
            color: {disabled_text};
            background-color: {button_disabled};
        }}
        ChipWidget {{
            background-color: {chip};
            color: palette(text);
            border: 1px solid {chip_frame};
            border-radius: 4px;
            padding: 0px 2px;
        }}
        ChipWidget:hover {{
            background-color: {chip_hover};
            border: 1px solid {chip_hover_frame};
        }}
        ChipWidget[selected=true] {{
            background-color: {chip_selected};
            border: 1px solid {chip_selected_frame};
        }}
        ChipWidget[selected=true]:hover {{
            background-color: {chip_selected_hover};
            border: 1px solid {chip_selected_hover_frame};
        }}
    """
    app.setPalette(palette)
    app.setStyleSheet(style_sheet.format(**colors))
