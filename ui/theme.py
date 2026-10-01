"""Dark and light palettes, and the application stylesheet built from them.

Widgets never hard-code colours: they read `theme.C` (the active palette) when
they paint, and the stylesheet is regenerated on every theme switch. "system"
follows Windows' app mode live via QStyleHints.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette

DARK = {
    "bg": "#0f1115", "surface": "#161920", "raised": "#1e222a", "input": "#1a1d24",
    "border": "#2b303a", "border_subtle": "#21252d", "hover": "#252a33",
    "text": "#eceef2", "text2": "#a2a9b6", "muted": "#6b7280",
    "accent": "#ff7a2f", "accent_hover": "#ff8f50", "accent_text": "#1b0d04",
    "accent_subtle": "#35210f", "accent_line": "#6a3813",
    "success": "#34d399", "success_subtle": "#12291f",
    "warning": "#fbbf24", "danger": "#f87171", "danger_subtle": "#3a1b1b",
    "stage_in": "#2a2f39", "stage_out": "#191c22", "shadow": "#000000",
    "badge_bg": "#d90d0f13",
}

LIGHT = {
    "bg": "#f3f4f6", "surface": "#ffffff", "raised": "#f1f2f5", "input": "#ffffff",
    "border": "#d6dae1", "border_subtle": "#e5e7ec", "hover": "#eceef2",
    "text": "#151820", "text2": "#4a5260", "muted": "#8a92a0",
    "accent": "#e8620c", "accent_hover": "#cf5405", "accent_text": "#ffffff",
    "accent_subtle": "#fdebdd", "accent_line": "#f4b58a",
    "success": "#059669", "success_subtle": "#dcf5ea",
    "warning": "#b45309", "danger": "#dc2626", "danger_subtle": "#fde2e2",
    "stage_in": "#f6f7f9", "stage_out": "#e2e6ec", "shadow": "#8090a0",
    "badge_bg": "#e6ffffff",
}

# File-type badge colours, readable on both stages.
KIND_COLORS = {
    "dark": {"STL": "#7cb4ff", "3MF": "#ff9a5c", "OBJ": "#c4a5ff", "GCODE": "#5ee0a8",
             "ZIP": "#f5d06b", "CAD": "#f59ac8"},
    "light": {"STL": "#1d64d8", "3MF": "#c2410c", "OBJ": "#7c3aed", "GCODE": "#047857",
              "ZIP": "#a16207", "CAD": "#be185d"},
}

# Corner radii. Kept small on purpose: tight corners read as a tool, big soft
# ones as a template.
R_SM = 2     # badges, list rows, checkboxes
R_MD = 4     # buttons, inputs, cards, panels

# First family that exists wins, so each system gets its own UI font.
FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "Ubuntu", "Cantarell",
                 "DejaVu Sans", "Helvetica Neue", "sans-serif"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono",
                 "Menlo", "monospace"]
FONT = ", ".join(f"'{f}'" for f in FONT_FAMILIES)
MONO = ", ".join(f"'{f}'" for f in MONO_FAMILIES)

C: dict[str, str] = dict(DARK)
MODE = "dark"


def qc(name: str, alpha: int | None = None) -> QColor:
    col = QColor(C[name])
    if alpha is not None:
        col.setAlpha(alpha)
    return col


def kind_color(kind: str) -> QColor:
    return QColor(KIND_COLORS[MODE].get(kind, C["text2"]))


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return True


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, app, preference: str = "system"):
        super().__init__()
        self.app = app
        self.preference = preference
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.preference == "system" and self.apply())

    def set_preference(self, pref: str) -> None:
        self.preference = pref
        self.apply()

    def apply(self) -> None:
        global MODE
        dark = system_is_dark() if self.preference == "system" else self.preference == "dark"
        MODE = "dark" if dark else "light"
        C.clear()
        C.update(DARK if dark else LIGHT)
        pal = QPalette()
        for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "input"), (QPalette.Text, "text"),
                          (QPalette.WindowText, "text"), (QPalette.Button, "raised"),
                          (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                          (QPalette.HighlightedText, "accent_text"), (QPalette.ToolTipBase, "raised"),
                          (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "muted"),
                          (QPalette.AlternateBase, "raised")):
            pal.setColor(role, QColor(C[key]))
        self.app.setPalette(pal)
        from ui import icons
        icons.clear_cache()
        self.app.setStyleSheet(stylesheet())
        self.changed.emit()


def _check_png() -> str:
    """QSS can only draw an indicator image from a file, so the tick is
    rendered from the icon set into the temp folder (one per theme colour)."""
    import os
    import tempfile
    from ui import icons
    path = os.path.join(tempfile.gettempdir(), f"modelshelf-check-{C['accent_text'].lstrip('#')}.png")
    if not os.path.exists(path):
        px = icons.pixmap("check", 14, C["accent_text"], dpr=2.0)
        px.save(path, "PNG")
    return path.replace("\\", "/")


def stylesheet() -> str:
    c = C
    check = _check_png()
    return f"""
    QWidget {{ color: {c['text']}; font-family: {FONT}; font-size: 13px; }}
    QMainWindow, QDialog, QWidget[role="page"] {{ background: {c['bg']}; }}
    QLabel {{ background: transparent; }}
    QLabel[role="h1"] {{ font-size: 20px; font-weight: 700; }}
    QLabel[role="h2"] {{ font-size: 15px; font-weight: 700; }}
    QLabel[role="h3"] {{ font-size: 11px; font-weight: 700; color: {c['muted']}; letter-spacing: 1px; }}
    QLabel[role="muted"] {{ color: {c['muted']}; font-size: 12px; }}
    QLabel[role="sub"] {{ color: {c['text2']}; font-size: 12px; }}
    QLabel[role="mono"] {{ font-family: {MONO}; font-size: 12px; color: {c['text2']}; }}
    QLabel[role="statvalue"] {{ font-size: 24px; font-weight: 700; }}
    QLabel[role="statunit"] {{ font-size: 13px; color: {c['text2']}; font-weight: 600; }}
    QLabel[role="statlabel"] {{ font-size: 10px; color: {c['muted']}; font-weight: 700; letter-spacing: 1.2px; }}
    QLabel[role="designer"] {{ color: {c['accent']}; font-size: 12px; }}
    QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']};
               padding: 5px 8px; border-radius: {R_SM}px; }}

    QFrame[role="panel"] {{ background: {c['surface']}; border: 1px solid {c['border_subtle']};
                            border-radius: {R_MD}px; }}
    QFrame[role="header"] {{ background: {c['surface']}; border: none;
                             border-bottom: 1px solid {c['border_subtle']}; }}
    QFrame[role="divider"] {{ background: {c['border_subtle']}; border: none; }}
    QFrame[role="stat"] {{ background: transparent; border: none;
                           border-right: 1px solid {c['border_subtle']}; }}
    QFrame[role="stat"][last="true"] {{ border-right: none; }}

    QPushButton, QToolButton {{
        background: {c['raised']}; border: 1px solid {c['border']}; border-radius: {R_MD}px;
        padding: 7px 13px; color: {c['text']}; font-weight: 600;
    }}
    QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; border-color: {c['muted']}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {c['border']}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; border-color: {c['border_subtle']}; }}
    QPushButton:checked, QToolButton:checked {{ background: {c['accent_subtle']}; border-color: {c['accent']};
                                                color: {c['text']}; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}
    QPushButton[variant="primary"] {{ background: {c['accent']}; border: 1px solid {c['accent']};
                                      color: {c['accent_text']}; font-weight: 700; }}
    QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
    QPushButton[variant="primary"]:disabled {{ background: {c['raised']}; color: {c['muted']};
                                               border-color: {c['border']}; }}
    QPushButton[variant="danger"] {{ background: transparent; color: {c['danger']};
                                     border-color: {c['border']}; }}
    QPushButton[variant="danger"]:hover {{ background: {c['danger_subtle']}; border-color: {c['danger']}; }}
    QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; }}
    QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{ background: {c['hover']}; }}
    QPushButton[variant="chip"] {{
        background: transparent; border: 1px solid {c['border']}; border-radius: {R_MD}px;
        padding: 5px 12px; font-family: {MONO}; font-size: 12px; font-weight: 600; color: {c['text2']};
    }}
    QPushButton[variant="chip"]:hover {{ border-color: {c['muted']}; color: {c['text']}; }}
    QPushButton[variant="chip"]:checked {{ background: {c['accent_subtle']}; border-color: {c['accent']};
                                           color: {c['accent']}; }}
    QPushButton[variant="seg"] {{ background: transparent; border: none; border-bottom: 2px solid transparent;
        border-radius: 0; padding: 8px 6px; font-size: 11px; font-weight: 700; color: {c['muted']}; }}
    QPushButton[variant="seg"]:hover {{ color: {c['text']}; }}
    QPushButton[variant="seg"]:checked {{ color: {c['accent']}; border-bottom-color: {c['accent']};
                                          background: transparent; }}

    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c['input']}; border: 1px solid {c['border']}; border-radius: {R_MD}px;
        padding: 6px 10px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {c['accent']}; }}
    QLineEdit[role="search"] {{ padding-left: 32px; min-height: 22px; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['border']};
        selection-background-color: {c['accent_subtle']}; selection-color: {c['text']}; outline: none; padding: 4px; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}

    QCheckBox {{ spacing: 9px; background: transparent; color: {c['text']}; padding: 2px 0; }}
    QCheckBox::indicator:unchecked:hover {{ border-color: {c['accent']}; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: {R_SM}px; border: 1px solid {c['border']};
                            background: {c['input']}; }}
    QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
                                    image: url({check}); }}
    QCheckBox::indicator:disabled {{ background: {c['raised']}; border-color: {c['border_subtle']}; }}

    QListView, QTreeWidget, QListWidget {{ background: transparent; border: none; outline: none; }}
    QTreeWidget::item, QListWidget::item {{ padding: 4px 6px; border-radius: {R_SM}px; color: {c['text2']}; }}
    QTreeWidget::item:hover, QListWidget::item:hover {{ background: {c['hover']}; color: {c['text']}; }}
    QTreeWidget::item:selected, QListWidget::item:selected {{ background: {c['accent_subtle']};
                                                              color: {c['accent']}; }}
    QTreeView::branch {{ background: transparent; }}
    QTreeView::branch:selected {{ background: {c['accent_subtle']}; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: {R_SM}px; min-height: 36px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: {R_SM}px; min-width: 36px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QProgressBar {{ background: {c['raised']}; border: none; border-radius: 1px; max-height: 4px; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 1px; }}
    QSlider::groove:horizontal {{ background: {c['border']}; height: 4px; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {c['text']}; width: 14px; height: 14px; margin: -5px 0;
                                  border-radius: 7px; }}

    QMenu {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: {R_MD}px; padding: 4px; }}
    QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: {R_SM}px; }}
    QMenu::item:selected {{ background: {c['accent_subtle']}; color: {c['text']}; }}
    QMenu::item:disabled {{ color: {c['muted']}; }}
    QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 6px; }}
    QMenu::icon {{ padding-left: 6px; }}

    QSplitter::handle {{ background: transparent; }}
    QTabWidget::pane {{ border: none; }}
    QStatusBar {{ background: {c['surface']}; border-top: 1px solid {c['border_subtle']};
                  min-height: 30px; }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ color: {c['text2']}; font-size: 12px; padding: 0 4px; }}
    QLabel[role="footer"] {{ color: {c['text2']}; font-size: 12px; }}
    QFrame[role="sidefooter"] {{ border: none; border-top: 1px solid {c['border_subtle']}; }}
    """
