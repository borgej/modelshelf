"""Small reusable widgets: flow layout, tag chips, icon buttons, search box."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                               QSizePolicy, QToolButton, QWidget)

from ui import icons, theme


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > rect.right() and line > 0:
                x, y = rect.x(), y + line + self._sp
                nx, line = x + hint.width() + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x, line = nx, max(line, hint.height())
        return y + line - rect.y()

    def clear(self):
        while self._items:
            it = self._items.pop()
            if it.widget():
                it.widget().deleteLater()


class TagChip(QFrame):
    removed = Signal(str)
    clicked = Signal(str)

    def __init__(self, text: str, removable=True, parent=None):
        super().__init__(parent)
        self.text = text
        c = theme.C
        self.setStyleSheet(
            f"TagChip {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: {theme.R_SM}px; }}"
            f"QLabel {{ color: {c['text2']}; font-size: 12px; }}"
            f"QToolButton {{ border: none; background: transparent; padding: 0; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 2, 4 if removable else 9, 2)
        lay.setSpacing(2)
        lay.addWidget(QLabel(text))
        if removable:
            b = QToolButton()
            b.setIcon(icons.icon("x", 12))
            b.setIconSize(QSize(12, 12))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda: self.removed.emit(self.text))
            lay.addWidget(b)

    def mousePressEvent(self, e):
        self.clicked.emit(self.text)


def icon_button(name: str, tooltip: str, text: str = "", variant: str | None = None,
                size: int = 16) -> QPushButton:
    b = QPushButton(text)
    b.setIcon(icons.icon(name, size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    b.setProperty("icon_name", name)
    b.setProperty("icon_size", size)
    if variant:
        b.setProperty("variant", variant)
    return b


def refresh_icons(root: QWidget) -> None:
    """Re-tint every icon button after a theme switch."""
    for b in root.findChildren(QPushButton) + root.findChildren(QToolButton):
        name = b.property("icon_name")
        if name:
            col = b.property("icon_color")
            size = b.property("icon_size") or 16
            color = theme.C[col] if col else None
            b.setIcon(icons.icon(name, size, color))


class SearchBox(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "search")
        self.setPlaceholderText("Search models, folders, designers, tags…   (Ctrl+F)")
        self.setClearButtonEnabled(True)
        self._icon = QLabel(self)
        self._icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.refresh_icon()

    def refresh_icon(self):
        self._icon.setPixmap(icons.pixmap("search", 15, theme.C["muted"]))
        self._icon.setFixedSize(16, 16)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._icon.move(11, (self.height() - 16) // 2)


ZWSP = "​"


def soft_wrap(text: str) -> str:
    """Let a long file name wrap: zero-width spaces after separators. Without
    them one unbroken name sets the label's minimum width and pushes the whole
    panel wider than its space. Display only: never use on text meant to be
    copied (the invisible characters would come along)."""
    out = []
    run = 0
    for ch in text:
        out.append(ch)
        run = 0 if ch == " " else run + 1
        if ch in "_-+.," or run >= 18:
            out.append(ZWSP)
            run = 0
    return "".join(out)


class PathLabel(QLabel):
    """One-line path, elided in the middle to fit. Tooltip shows it in full;
    a click copies the real path."""

    copied = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setProperty("role", "muted")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(10)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_path(self, path: str) -> None:
        self._full = path
        self.setToolTip(f"{path}\nClick to copy")
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, max(20, self.width() - 2)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def mouseReleaseEvent(self, e):
        if self._full and e.button() == Qt.LeftButton:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(self._full)
            self.copied.emit(self._full)


def open_external(target: str) -> None:
    """Open a web address or a local file/folder with its default program."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from core.system import external_launch
    url = QUrl(target) if "://" in target else QUrl.fromLocalFile(target)
    with external_launch():
        QDesktopServices.openUrl(url)


class _WheelGuard(QObject):
    """Stops combo boxes and number fields from grabbing the mouse wheel.

    By default Qt changes the value of whichever such field is under the
    pointer, so scrolling a long form stops as soon as the pointer passes over
    one, and silently changes a setting instead. With the guard the wheel
    scrolls the page; it only changes a value in a field the user has clicked
    into first.
    """

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            event.ignore()          # not handled here: Qt passes it on to the scroll area
            return True
        return False


_wheel_guard = None


def guard_wheel(root: QWidget) -> None:
    """Apply the wheel guard to every combo box and spin box inside `root`."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox
    global _wheel_guard
    if _wheel_guard is None:
        _wheel_guard = _WheelGuard()
    for w in root.findChildren(QComboBox) + root.findChildren(QAbstractSpinBox):
        # StrongFocus: hovering plus scrolling must not give the field focus either.
        w.setFocusPolicy(Qt.StrongFocus)
        w.installEventFilter(_wheel_guard)


def hline() -> QFrame:
    f = QFrame()
    f.setProperty("role", "divider")
    f.setFixedHeight(1)
    return f


def label(text: str = "", role: str | None = None, wrap=False, select=False) -> QLabel:
    l = QLabel(text)
    if role:
        l.setProperty("role", role)
    if wrap:
        l.setWordWrap(True)
    if select:
        l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l


def expanding() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w
