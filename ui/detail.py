"""Right-hand details panel: 3D preview, photos, facts, status, tags and notes."""

from __future__ import annotations

import datetime as dt
import os
import zipfile

from PySide6.QtCore import QObject, QRunnable, QSize, QStringListModel, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCompleter, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from core.db import Item
from core.mesh import fmt_date
from core.settings import Settings, estimate_grams
from ui import icons, theme
from ui.grid import fmt_dims, fmt_duration, fmt_size, looks_mis_scaled
from ui.viewer3d import ModelViewer
from ui.widgets import FlowLayout, PathLabel, TagChip, hline, icon_button, label, soft_wrap

RENDERABLE = {"STL", "3MF", "OBJ", "ZIP", "GCODE"}


# --------------------------------------------------------------------------- photos

def read_photo(item: Item, ref: str) -> QImage:
    if os.path.isabs(ref):
        return QImage(ref)
    try:
        with zipfile.ZipFile(item.path) as zf:
            data = zf.read(ref)
    except (OSError, KeyError, zipfile.BadZipFile):
        return QImage()
    img = QImage()
    img.loadFromData(data)
    return img


class _PhotoBridge(QObject):
    loaded = Signal(int, int, QImage)


class _PhotoJob(QRunnable):
    def __init__(self, token, idx, item, ref, bridge, size):
        super().__init__()
        self.a = (token, idx, item, ref, bridge, size)

    def run(self):
        token, idx, item, ref, bridge, size = self.a
        img = read_photo(item, ref)
        if not img.isNull() and size:
            img = img.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        bridge.loaded.emit(token, idx, img)


class PhotoDialog(QDialog):
    def __init__(self, item: Item, index: int, parent=None):
        super().__init__(parent)
        self.item, self.i = item, index
        self.setWindowTitle(f"Photos — {item.name}")
        self.resize(900, 700)
        lay = QVBoxLayout(self)
        self.img = QLabel(alignment=Qt.AlignCenter)
        self.img.setMinimumSize(300, 300)
        lay.addWidget(self.img, 1)
        row = QHBoxLayout()
        self.prev = QPushButton("‹  Previous")
        self.next = QPushButton("Next  ›")
        self.caption = label("", "muted")
        row.addWidget(self.prev); row.addStretch(); row.addWidget(self.caption); row.addStretch(); row.addWidget(self.next)
        lay.addLayout(row)
        self.prev.clicked.connect(lambda: self.show_index(self.i - 1))
        self.next.clicked.connect(lambda: self.show_index(self.i + 1))
        self._orig = QImage()
        self.show_index(index)

    def show_index(self, i):
        n = len(self.item.photos)
        self.i = i % n
        self._orig = read_photo(self.item, self.item.photos[self.i])
        self.caption.setText(f"{self.i + 1} / {n} · {os.path.basename(self.item.photos[self.i])}")
        self._fit()

    def _fit(self):
        if self._orig.isNull():
            self.img.setText("Cannot open this image")
            return
        px = QPixmap.fromImage(self._orig).scaled(self.img.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img.setPixmap(px)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left:
            self.show_index(self.i - 1)
        elif e.key() == Qt.Key_Right:
            self.show_index(self.i + 1)
        else:
            super().keyPressEvent(e)


# --------------------------------------------------------------------------- panel

class DetailPanel(QFrame):
    action = Signal(str, list)                 # action name, items
    user_changed = Signal(list, dict)          # ids, fields
    tag_clicked = Signal(str)
    closed = Signal()

    def __init__(self, settings: Settings, thumbs, parent=None):
        super().__init__(parent)
        self.s = settings
        self.thumbs = thumbs
        self.items: list[Item] = []
        self.all_tags: list[str] = []
        self.setMinimumWidth(320)
        self.setMaximumWidth(460)
        self.setProperty("role", "panel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 12, 14, 16)
        lay.setSpacing(12)
        self.body_layout = lay

        # header row
        top = QHBoxLayout()
        self.kind_lbl = label("", "h3")
        top.addWidget(self.kind_lbl)
        top.addStretch()
        close = icon_button("x", "Close details", variant="ghost", size=14)
        close.clicked.connect(self.closed)
        top.addWidget(close)
        lay.addLayout(top)

        # viewer
        self.viewer = ModelViewer()
        self.viewer.setFixedHeight(290)
        lay.addWidget(self.viewer)
        vrow = QHBoxLayout()
        self.viewer_hint = label("", "muted", wrap=True)
        # Without this a long hint sets the panel's minimum width and pushes
        # everything past the right edge.
        self.viewer_hint.setMinimumWidth(10)
        vrow.addWidget(self.viewer_hint, 1)
        reset = icon_button("reset", "Reset view (or double-click the view)", variant="ghost", size=14)
        reset.clicked.connect(self.viewer.reset_view)
        self.reset_btn = reset
        self.viewer.ready.connect(self._viewer_ready)
        vrow.addWidget(reset)
        lay.addLayout(vrow)

        # photos
        self.photos_box = QWidget()
        self.photos_lay = QHBoxLayout(self.photos_box)
        self.photos_lay.setContentsMargins(0, 0, 0, 0)
        self.photos_lay.setSpacing(6)
        self.photos_scroll = QScrollArea()
        self.photos_scroll.setWidget(self.photos_box)
        self.photos_scroll.setWidgetResizable(True)
        self.photos_scroll.setFixedHeight(74)
        self.photos_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.photos_scroll)
        self._photo_token = 0
        self._photo_bridge = _PhotoBridge()
        self._photo_bridge.loaded.connect(self._photo_loaded)
        self._photo_btns: list[QPushButton] = []
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)

        # title
        self.title = label("", "h2", wrap=True)
        self.title.setMinimumWidth(10)
        lay.addWidget(self.title)
        self.designer = label("", "designer", select=True)
        lay.addWidget(self.designer)
        self.path_lbl = PathLabel()
        self.path_lbl.copied.connect(lambda _p: self.action.emit("copied_path", self.items))
        lay.addWidget(self.path_lbl)

        # actions
        arow = QHBoxLayout()
        arow.setSpacing(6)
        self.b_open = icon_button("open", "Open with the default app (Enter)", "Open", size=15)
        self.b_slicer = icon_button("slicer", "Open in your slicer", "Slicer", size=15)
        self.b_explorer = icon_button("explorer", "Show in File Explorer", "", size=15)
        self.b_more = icon_button("more", "More actions", "", size=15)
        for b in (self.b_open, self.b_slicer, self.b_explorer, self.b_more):
            arow.addWidget(b)
        arow.addStretch()
        lay.addLayout(arow)
        self.b_open.clicked.connect(lambda: self.action.emit("open", self.items))
        self.b_slicer.clicked.connect(lambda: self.action.emit("slicer", self.items))
        self.b_explorer.clicked.connect(lambda: self.action.emit("explorer", self.items))
        self.b_more.clicked.connect(self._more_menu)

        # status
        srow = QHBoxLayout()
        srow.setSpacing(6)
        self.b_queue = icon_button("clock", "Add to / remove from your print queue", "To print", size=15)
        self.b_printed = icon_button("check_circle", "Mark as printed", "Printed", size=15)
        self.b_fav = icon_button("star", "Favourite", "Favourite", size=15)
        for b in (self.b_queue, self.b_printed, self.b_fav):
            b.setCheckable(True)
            srow.addWidget(b)
        srow.addStretch()
        lay.addLayout(srow)
        self.b_queue.clicked.connect(lambda on: self._set_status("to_print" if on else ""))
        self.b_printed.clicked.connect(lambda on: self._set_status("printed" if on else ""))
        self.b_fav.clicked.connect(lambda on: self.user_changed.emit([i.id for i in self.items], {"favorite": on}))

        lay.addWidget(hline())

        # facts
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(12)
        self.facts.setVerticalSpacing(6)
        self.facts.setColumnStretch(1, 1)
        lay.addLayout(self.facts)

        lay.addWidget(hline())

        # tags
        lay.addWidget(label("TAGS", "h3"))
        self.tag_wrap = QWidget()
        self.tag_flow = FlowLayout(self.tag_wrap)
        lay.addWidget(self.tag_wrap)
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("Add tag and press Enter")
        self.tag_input.returnPressed.connect(self._add_tag)
        self.completer = QCompleter([])
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.tag_input.setCompleter(self.completer)
        lay.addWidget(self.tag_input)

        # notes
        lay.addWidget(label("NOTES", "h3"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Print settings, what worked, what to change…")
        self.notes.setFixedHeight(96)
        self._notes_timer = QTimer(self, singleShot=True, interval=600, timeout=self._save_notes)
        self.notes.textChanged.connect(self._notes_timer.start)
        lay.addWidget(self.notes)
        lay.addStretch()

        self._single_widgets = [self.viewer, self.viewer_hint, reset, self.path_lbl, self.notes,
                                self.b_explorer]

    # ------------------------------------------------------------------ populate
    def set_items(self, items: list[Item], all_tags: list[str]):
        if self._notes_timer.isActive():
            self._notes_timer.stop()
            self._save_notes()
        prev_ids = [i.id for i in self.items]
        self.items = items
        self.all_tags = all_tags
        self.completer.setModel(QStringListModel(all_tags, self.completer))
        if not items:
            self.viewer.clear()
            return
        single = len(items) == 1
        for w in self._single_widgets:
            w.setVisible(single)
        it = items[0]
        same_selection = prev_ids == [i.id for i in items]

        if single:
            self.kind_lbl.setText(f"{it.kind} FILE" if it.kind != "GCODE" else "SLICED G-CODE")
            self.title.setText(soft_wrap(it.title if (it.title and it.kind == "3MF") else it.name))
            self.designer.setText(f"by {it.designer}" if it.designer else "")
            self.designer.setVisible(bool(it.designer))
            self.path_lbl.set_path(it.path)
            if not same_selection:
                px = self.thumbs.get(it.thumb) if it.thumb else None
                renderable = it.kind in RENDERABLE and bool(it.triangles or it.kind == "GCODE")
                self.viewer_hint.setText("Loading 3D view…" if renderable else "")
                self.viewer.show_path(it.path, px, renderable,
                                      self.s.default_color)
                self._load_photos(it)
                self.notes.blockSignals(True)
                self.notes.setPlainText(it.notes)
                self.notes.blockSignals(False)
        else:
            self.kind_lbl.setText(f"{len(items)} SELECTED")
            self.title.setText(f"{len(items)} models")
            self.designer.setVisible(False)
            self.photos_scroll.setVisible(False)
            self.viewer.clear()

        self.b_queue.setChecked(all(i.status == "to_print" for i in items))
        self.b_printed.setChecked(all(i.status == "printed" for i in items))
        self.b_fav.setChecked(all(i.favorite for i in items))
        from core.settings import slicer_name
        name = slicer_name(self.s.slicer_path)
        self.b_slicer.setEnabled(bool(self.s.slicer_path))
        self.b_slicer.setText(name if self.s.slicer_path else "Slicer")
        self.b_slicer.setToolTip(f"Open in {name}   (Ctrl+O)" + (" — models inside the ZIP are unpacked to a temp folder first"
                                                      if any(i.kind == "ZIP" for i in items)
                                                      and not self.s.slicer_path.lower().endswith(
                                                          ("elegoo-slicer.exe", "orca-slicer.exe")) else "")
                                 if self.s.slicer_path else "Choose your slicer in Settings to enable this")
        self._fill_facts(items)
        self._fill_tags(items)

    def _fill_facts(self, items: list[Item]):
        while self.facts.count():
            w = self.facts.takeAt(0).widget()
            if w:
                w.deleteLater()
        rows: list[tuple[str, str]] = []
        if len(items) == 1:
            it = items[0]
            rows.append(("File size", fmt_size(it.size)))
            if it.dims:
                rows.append(("Dimensions", fmt_dims(it)))
                if looks_mis_scaled(it):
                    rows.append(("Scale?", "Unusually small for this much detail — probably saved in "
                                 f"another unit. In cm: {fmt_dims(it, 10)}; in inches: {fmt_dims(it, 25.4)}. "
                                 "Your slicer will offer to convert it on import."))
            if it.volume:
                rows.append(("Volume", f"{it.volume / 1000:.1f} cm³" if it.volume >= 100
                             else f"{it.volume:.1f} mm³"))
            if it.sliced_weight:
                rows.append(("Filament", f"{it.sliced_weight:.1f} g (from slicer)"))
            elif it.volume:
                g = estimate_grams(it.volume, it.area, self.s)
                grams = f"{g:.0f} g" if g >= 1 else "under 1 g"
                rows.append((f"≈ {self.s.material}", f"{grams} at {self.s.infill}% infill"))
            if it.print_time:
                rows.append(("Print time", fmt_duration(it.print_time)))
            if it.triangles:
                rows.append(("Triangles", f"{it.triangles:,}".replace(",", " ")))
            if it.parts and it.parts > 1:
                rows.append(("Parts", str(it.parts)))
            if it.note:
                rows.append(("Contents", it.note))
            rows.append(("Modified", fmt_date(it.mtime, with_time=True)))
            if it.added_at:
                rows.append(("Added", fmt_date(it.added_at)))
            if it.error:
                rows.append(("Problem", it.error))
        else:
            rows.append(("Total size", fmt_size(sum(i.size or 0 for i in items))))
            grams = sum((i.sliced_weight or estimate_grams(i.volume, i.area, self.s) or 0) for i in items)
            if grams:
                rows.append((f"≈ {self.s.material}", f"{grams:.0f} g"))
            kinds = {}
            for i in items:
                kinds[i.kind] = kinds.get(i.kind, 0) + 1
            rows.append(("Types", ", ".join(f"{v}× {k}" for k, v in sorted(kinds.items()))))
        for r, (k, v) in enumerate(rows):
            kl = label(k, "muted")
            vl = label(v, "mono" if k not in ("Problem", "Contents", "Scale?") else "sub", wrap=True, select=True)
            if k == "Scale?":
                vl.setStyleSheet(f"color: {theme.C['warning']};")
            if k == "Problem":
                vl.setStyleSheet(f"color: {theme.C['danger']};")
            self.facts.addWidget(kl, r, 0, Qt.AlignTop)
            self.facts.addWidget(vl, r, 1)
        if len(items) == 1 and items[0].colors:
            r = len(rows)
            self.facts.addWidget(label("Colours", "muted"), r, 0)
            box = QWidget()
            hl = QHBoxLayout(box)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(5)
            for col in items[0].colors:
                dot = QLabel()
                dot.setFixedSize(16, 16)
                dot.setToolTip(col)
                dot.setStyleSheet(f"background: {col}; border-radius: 8px; border: 1px solid {theme.C['border']};")
                hl.addWidget(dot)
            hl.addStretch()
            self.facts.addWidget(box, r, 1)

    def _fill_tags(self, items: list[Item]):
        self.tag_flow.clear()
        common = set(items[0].tags)
        for i in items[1:]:
            common &= set(i.tags)
        for t in sorted(common, key=str.lower):
            chip = TagChip(t)
            chip.removed.connect(self._remove_tag)
            chip.clicked.connect(self.tag_clicked)
            self.tag_flow.addWidget(chip)
        self.tag_wrap.setVisible(bool(common))
        self.tag_wrap.updateGeometry()

    def _viewer_ready(self, interactive: bool):
        """Only promise rotation once there is a 3D model to rotate."""
        self.viewer_hint.setText("Drag to rotate · right-drag to pan · scroll to zoom"
                                 if interactive else "Preview image only — no 3D data")
        self.viewer_hint.setToolTip("Double-click the view or press ↺ to reset" if interactive else "")
        self.reset_btn.setVisible(interactive)

    # ------------------------------------------------------------------ photos
    def _load_photos(self, it: Item):
        self._photo_token += 1
        while self.photos_lay.count():
            w = self.photos_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._photo_btns = []
        self.photos_scroll.setVisible(bool(it.photos))
        for idx, ref in enumerate(it.photos[:16]):
            b = QPushButton()
            b.setFixedSize(64, 64)
            b.setProperty("variant", "ghost")
            b.setToolTip(os.path.basename(ref))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=idx: PhotoDialog(it, i, self.window()).exec())
            self.photos_lay.addWidget(b)
            self._photo_btns.append(b)
            self._pool.start(_PhotoJob(self._photo_token, idx, it, ref, self._photo_bridge, 128))
        self.photos_lay.addStretch()

    def _photo_loaded(self, token, idx, img: QImage):
        if token != self._photo_token or idx >= len(self._photo_btns) or img.isNull():
            return
        px = QPixmap.fromImage(img)
        px.setDevicePixelRatio(2.0)
        b = self._photo_btns[idx]
        b.setIcon(px)
        b.setIconSize(QSize(60, 60))

    # ------------------------------------------------------------------ edits
    def _set_status(self, st: str):
        self.user_changed.emit([i.id for i in self.items], {"status": st})

    def _add_tag(self):
        raw = self.tag_input.text().strip()
        if not raw:
            return
        new = [t.strip() for t in raw.split(",") if t.strip()]
        self.tag_input.clear()
        for it in self.items:
            self.user_changed.emit([it.id], {"tags": list(set(it.tags) | set(new))})

    def _remove_tag(self, tag: str):
        for it in self.items:
            if tag in it.tags:
                self.user_changed.emit([it.id], {"tags": [t for t in it.tags if t != tag]})

    def _save_notes(self):
        if len(self.items) == 1 and self.notes.toPlainText() != self.items[0].notes:
            self.user_changed.emit([self.items[0].id], {"notes": self.notes.toPlainText()})

    def _more_menu(self):
        m = QMenu(self)
        m.addAction(icons.icon("copy"), "Copy path", lambda: self.action.emit("copy_path", self.items))
        m.addAction(icons.icon("refresh"), "Re-analyse / re-render", lambda: self.action.emit("reanalyze", self.items))
        m.addSeparator()
        m.addAction(icons.icon("trash", color=theme.C["danger"]), "Move to Recycle Bin…",
                    lambda: self.action.emit("recycle", self.items))
        m.exec(self.b_more.mapToGlobal(self.b_more.rect().bottomLeft()))
