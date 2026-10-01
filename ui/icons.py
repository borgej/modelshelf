"""Line icons as inline SVG, tinted to the active theme at runtime.

Hand-drawn on a 24×24 grid with 2px round strokes, rendered through QtSvg so
they stay crisp at any DPI and follow the theme without separate image sets.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui import theme

P = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "folder_plus": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 10v6M9 13h6"/>',
    "copies": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "log": '<path d="M5 6h14M5 12h14M5 18h9"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "export": '<path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 19h14"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "star": '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check_circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "open": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    "explorer": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M10 13h6M13 10l3 3-3 3"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "tag": '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
    "cube": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    "printer": '<path d="M5 20h14M7 20V9h10v11M9 5h6v4H9z"/><path d="M12 13v3"/>',
    "slicer": '<path d="M4 17l8 4 8-4M4 12.5l8 4 8-4"/><path d="M12 3l8 4.5-8 4.5-8-4.5z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "reset": '<path d="M4 12a8 8 0 1 0 2.3-5.7"/><path d="M4 4v5h5"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    "scan": '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M7 12h10"/>',
}

# Filled variants (drawn with fill instead of stroke).
FILLED = {"star_filled": P["star"]}

_cache: dict = {}


def clear_cache() -> None:
    _cache.clear()


def _svg(name: str, color: str) -> bytes:
    if name in FILLED:
        body = FILLED[name]
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}" '
                f'stroke="{color}" stroke-width="1.5" stroke-linejoin="round">{body}</svg>').encode()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{P[name]}</svg>').encode()


def pixmap(name: str, size: int = 18, color: str | None = None, dpr: float = 2.0) -> QPixmap:
    color = color or theme.C["text2"]
    key = (name, size, color, dpr)
    if key in _cache:
        return _cache[key]
    px = QPixmap(QSize(int(size * dpr), int(size * dpr)))
    px.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(_svg(name, color)))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    px.setDevicePixelRatio(dpr)
    _cache[key] = px
    return px


def icon(name: str, size: int = 18, color: str | None = None) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color or theme.C["text2"]), QIcon.Normal)
    ic.addPixmap(pixmap(name, size, color or theme.C["text"]), QIcon.Active)
    ic.addPixmap(pixmap(name, size, theme.C["muted"]), QIcon.Disabled)
    return ic


def draw(p: QPainter, name: str, rect, color: str) -> None:
    QSvgRenderer(QByteArray(_svg(name, color))).render(p, QRectF(rect))


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """The ModelShelf mark: a stack of print layers forming a shelf, orange on dark."""
    from PySide6.QtGui import QColor, QLinearGradient, QPainterPath
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 24.0
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(0.5 * s, 0.5 * s, 23 * s, 23 * s), 5.5 * s, 5.5 * s)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#232833"))
    g.setColorAt(1, QColor("#12151b"))
    p.fillPath(bg, g)
    widths = [(5, 19), (6.2, 17.8), (7.4, 16.6)]
    cols = ["#ff7a2f", "#ffa066", "#ffc59c"]
    for i, ((x0, x1), col) in enumerate(zip(widths, cols)):
        y = (16.2 - i * 3.6) * s
        path = QPainterPath()
        path.addRoundedRect(QRectF(x0 * s, y, (x1 - x0) * s, 2.5 * s), 1.2 * s, 1.2 * s)
        p.fillPath(path, QColor(col))
    base = QPainterPath()
    base.addRoundedRect(QRectF(3.5 * s, 19.3 * s, 17 * s, 1.4 * s), 0.7 * s, 0.7 * s)
    p.fillPath(base, QColor("#5b6272"))
    p.end()
    return px
