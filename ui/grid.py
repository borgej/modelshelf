"""The card grid: a QListView in icon mode with a fully custom-painted delegate.

Painting cards (instead of one widget per card) keeps scrolling smooth with
thousands of models — there is nothing to build per item, only to draw the
handful that are on screen.
"""

from __future__ import annotations

import os
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from core.db import Item
from ui import icons, theme
from ui.library_model import ItemRole, LibraryModel, ThumbCache

GAP = 16
TEXT_H = 84


def fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def fmt_dims(it: Item, scale: float = 1.0) -> str:
    if it.dim_x is None:
        return ""
    d = [v * scale for v in (it.dim_x, it.dim_y, it.dim_z)]
    big = max(d)
    # Enough decimals that a tiny model doesn't read "1×2×1".
    fmt = "{:.0f}" if big >= 20 else "{:.1f}" if big >= 2 else "{:.2f}"
    return "×".join(fmt.format(v) for v in d) + " mm"


def looks_mis_scaled(it: Item) -> bool:
    """A detailed mesh only a few mm across was almost certainly saved in cm,
    inches or metres (STL has no units; slicers ask about this on import)."""
    return (it.dim_x is not None and max(it.dim_x, it.dim_y, it.dim_z) < 5
            and (it.triangles or 0) >= 2000)


def fmt_duration(sec: float | None) -> str:
    if not sec:
        return ""
    h, m = divmod(int(sec) // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _font(size: float, weight=QFont.Normal, mono=False) -> QFont:
    f = QFont(theme.MONO.split(",")[0] if mono else "Segoe UI Variable Text")
    if not mono:
        f.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    else:
        f.setFamilies(["Cascadia Mono", "Consolas"])
    f.setPixelSize(int(size))
    f.setWeight(weight)
    return f


class CardDelegate(QStyledItemDelegate):
    def __init__(self, view: "LibraryGrid", thumbs: ThumbCache):
        super().__init__(view)
        self.view = view
        self.thumbs = thumbs
        self._scaled: dict[tuple, QPixmap] = {}
        self.f_name = _font(13, QFont.DemiBold)
        self.f_sub = _font(11.5)
        self.f_mono = _font(11, mono=True)
        self.f_badge = _font(10, QFont.Bold, mono=True)

    def sizeHint(self, option, index):
        return self.view.gridSize()

    def _scaled_pix(self, name: str, side: int, dpr: float) -> QPixmap | None:
        key = (name, side, dpr)
        px = self._scaled.get(key)
        if px is not None:
            return px
        src = self.thumbs.get(name)
        if src is None:
            return None
        px = src.scaled(int(side * dpr), int(side * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        px.setDevicePixelRatio(dpr)
        if len(self._scaled) > 1200:
            self._scaled.clear()
        self._scaled[key] = px
        return px

    def forget(self, name: str) -> None:
        for k in [k for k in self._scaled if k[0] == name]:
            del self._scaled[k]

    def paint(self, p: QPainter, option, index):
        it: Item = index.data(ItemRole)
        if it is None:
            return
        c = theme.C
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        cell = option.rect
        r = QRectF(cell).adjusted(GAP / 2, GAP / 2, -GAP / 2, -GAP / 2)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)

        # --- card
        card = QPainterPath()
        card.addRoundedRect(r, theme.R_MD, theme.R_MD)
        p.fillPath(card, theme.qc("surface"))
        pen = QPen(theme.qc("accent") if selected else theme.qc("border" if hover else "border_subtle"))
        pen.setWidthF(2.0 if selected else 1.0)
        p.setPen(pen)
        p.drawPath(card)

        # --- stage
        stage_h = r.width() * 0.80
        stage = QRectF(r.left() + 7, r.top() + 7, r.width() - 14, stage_h - 7)
        sp = QPainterPath()
        sp.addRoundedRect(stage, theme.R_SM, theme.R_SM)
        grad = QRadialGradient(stage.center().x(), stage.top() + stage.height() * 0.45,
                               max(stage.width(), stage.height()) * 0.75)
        grad.setColorAt(0, theme.qc("stage_in"))
        grad.setColorAt(1, theme.qc("stage_out"))
        p.fillPath(sp, QBrush(grad))

        dpr = self.view.devicePixelRatioF()
        side = int(min(stage.width(), stage.height()) - 10)
        px = self._scaled_pix(it.thumb, side, dpr) if it.thumb else None
        if px is not None:
            w, h = px.width() / dpr, px.height() / dpr
            p.drawPixmap(QRectF(stage.center().x() - w / 2, stage.center().y() - h / 2 + 4, w, h), px,
                         QRectF(px.rect()))
        elif not it.thumb:
            ic = 44
            icons.draw(p, "cube" if it.kind != "GCODE" else "printer",
                       QRectF(stage.center().x() - ic / 2, stage.center().y() - ic / 2 - 8, ic, ic),
                       c["muted"])
            p.setFont(self.f_sub)
            p.setPen(theme.qc("muted"))
            msg = "No preview" if it.kind != "CAD" else "CAD file"
            if it.error and it.kind != "CAD":
                msg = "Could not read file"
            p.drawText(QRectF(stage.left(), stage.center().y() + 22, stage.width(), 18), Qt.AlignCenter, msg)

        # --- type badge
        p.setFont(self.f_badge)
        fm = QFontMetrics(self.f_badge)
        label = it.ext.upper() if it.kind not in ("GCODE",) else "GCODE"
        bw = fm.horizontalAdvance(label) + 14
        badge = QRectF(stage.left() + 8, stage.top() + 8, bw, 20)
        bp = QPainterPath()
        bp.addRoundedRect(badge, theme.R_SM, theme.R_SM)
        p.fillPath(bp, theme.qc("badge_bg"))
        p.setPen(theme.kind_color(it.kind))
        p.drawText(badge, Qt.AlignCenter, label)

        # --- status / favourite
        x = stage.right() - 8
        for flag, name, col in ((it.favorite, "star_filled", c["warning"]),
                                (it.status == "printed", "check_circle", c["success"]),
                                (it.status == "to_print", "clock", c["accent"])):
            if not flag:
                continue
            b = QRectF(x - 24, stage.top() + 8, 24, 22)
            pp = QPainterPath()
            pp.addRoundedRect(b, theme.R_SM, theme.R_SM)
            p.fillPath(pp, theme.qc("badge_bg"))
            icons.draw(p, name, b.adjusted(5, 4, -5, -4), col)
            x -= 28

        # --- colour dots
        if it.colors:
            n = min(len(it.colors), 8)
            dw = 12 + n * 12
            dots = QRectF(stage.left() + 8, stage.bottom() - 26, dw, 18)
            dp = QPainterPath()
            dp.addRoundedRect(dots, theme.R_SM, theme.R_SM)
            p.fillPath(dp, theme.qc("badge_bg"))
            for i, hexcol in enumerate(it.colors[:n]):
                cx = dots.left() + 12 + i * 12
                p.setPen(QPen(theme.qc("border"), 1))
                p.setBrush(QColor(hexcol))
                p.drawEllipse(QPointF(cx, dots.center().y()), 4.5, 4.5)

        # --- text
        tx = r.left() + 12
        tw = r.width() - 24
        ty = stage.bottom() + 9
        p.setPen(theme.qc("text"))
        p.setFont(self.f_name)
        fm = QFontMetrics(self.f_name)
        title = it.title if it.title and it.kind == "3MF" and len(it.title) > 2 else it.name
        lines = _wrap2(title, fm, tw)
        for i, line in enumerate(lines):
            p.drawText(QRectF(tx, ty + i * 17, tw, 18), Qt.AlignLeft | Qt.AlignVCenter, line)
        ty += 17 * len(lines) + 2
        p.setFont(self.f_sub)
        if it.designer:
            p.setPen(theme.qc("accent"))
            sub = f"by {it.designer}"
        else:
            p.setPen(theme.qc("muted"))
            sub = os.path.basename(it.folder) or it.folder
        p.drawText(QRectF(tx, ty, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(self.f_sub).elidedText(sub, Qt.ElideRight, int(tw)))

        # --- footer
        fy = r.bottom() - 24
        p.setFont(self.f_mono)
        p.setPen(theme.qc("muted"))
        p.drawText(QRectF(tx, fy, tw, 16), Qt.AlignLeft | Qt.AlignVCenter, fmt_size(it.size))
        right = fmt_dims(it) or fmt_duration(it.print_time) or (it.note if it.kind == "ZIP" else "")
        fmm = QFontMetrics(self.f_mono)
        right = fmm.elidedText(right, Qt.ElideLeft, int(tw - fmm.horizontalAdvance(fmt_size(it.size)) - 12))
        p.drawText(QRectF(tx, fy, tw, 16), Qt.AlignRight | Qt.AlignVCenter, right)
        p.restore()


def _wrap2(text: str, fm: QFontMetrics, width: float) -> list[str]:
    """Up to two lines; breaks at spaces/underscores/dashes, elides the rest."""
    w = int(width)
    if fm.horizontalAdvance(text) <= w:
        return [text]
    cut = len(text)
    while cut > 1 and fm.horizontalAdvance(text[:cut]) > w:
        cut -= 1
    brk = max(text.rfind(ch, 0, cut) for ch in " _-.")
    if brk > cut * 0.5:
        cut = brk + 1
    first, rest = text[:cut], text[cut:]
    return [first.rstrip(), fm.elidedText(rest.strip(), Qt.ElideRight, w)]


class LibraryGrid(QListView):
    activated_item = Signal(object)          # double click / Enter
    context_requested = Signal(QPoint)

    def __init__(self, model: LibraryModel, thumbs: ThumbCache, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.thumbs = thumbs
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        # Always reserve the scrollbar's width. If it only appeared when needed,
        # the viewport would shrink right after the columns were sized to fit,
        # and the last column would wrap away, leaving a wide empty strip.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(40)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_requested)
        self.setFrameShape(QListView.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.delegate = CardDelegate(self, thumbs)
        self.setItemDelegate(self.delegate)
        self.target = 220
        self.doubleClicked.connect(lambda idx: self.activated_item.emit(idx.data(ItemRole)))
        thumbs.ready.connect(self._thumb_ready)
        self._repaint = QTimer(self, singleShot=True, interval=30, timeout=self.viewport().update)

    def set_card_size(self, px: int) -> None:
        self.target = px
        self._relayout()

    def _thumb_ready(self, name: str) -> None:
        self.delegate.forget(name)
        if not self._repaint.isActive():
            self._repaint.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self):
        avail = self.viewport().width() - 2
        cols = max(1, int((avail + GAP) // (self.target + GAP)))
        cw = avail // cols
        h = int((cw - GAP) * 0.80 + TEXT_H + GAP)
        size = QSize(int(cw), h)
        if size != self.gridSize():
            self.setGridSize(size)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentIndex().isValid():
            self.activated_item.emit(self.currentIndex().data(ItemRole))
            return
        super().keyPressEvent(e)

    def selected_items(self) -> list[Item]:
        return [i.data(ItemRole) for i in self.selectionModel().selectedIndexes()]
