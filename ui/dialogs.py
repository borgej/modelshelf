"""Settings, duplicates and log windows."""

from __future__ import annotations

import datetime as dt
import glob
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from core.db import Item
from core.mesh import fmt_date
from core.settings import MATERIALS, Settings
from ui import icons, theme
from ui.grid import fmt_size
from ui.widgets import hline, icon_button, label


# --------------------------------------------------------------------------- slicers

SLICER_GLOBS = [
    r"C:\Program Files\ElegooSlicer\elegoo-slicer.exe",
    r"C:\Program Files\Bambu Studio\bambu-studio.exe",
    r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
    r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
    r"C:\Program Files\Ultimaker Cura*\UltiMaker-Cura.exe",
    r"C:\Program Files\Ultimaker Cura*\Ultimaker-Cura.exe",
    r"C:\Program Files\Creality\Creality Print*\CrealityPrint.exe",
    r"C:\Program Files\Anycubic Slicer*\AnycubicSlicer*.exe",
    r"C:\Program Files\SuperSlicer*\superslicer.exe",
]


def find_slicers() -> list[str]:
    found = []
    for pattern in SLICER_GLOBS:
        found.extend(glob.glob(pattern))
    local = os.environ.get("LOCALAPPDATA", "")
    for pattern in (r"Programs\*\*.exe",):
        for p in glob.glob(os.path.join(local, pattern)):
            if any(k in p.lower() for k in ("slicer", "cura", "bambu")) and "uninst" not in p.lower():
                found.append(p)
    return list(dict.fromkeys(found))


class SettingsDialog(QDialog):
    rerender_requested = Signal()

    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Settings")
        self.resize(640, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        body.setProperty("role", "page")
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 12)
        lay.setSpacing(10)

        # --- library
        lay.addWidget(label("Library folders", "h2"))
        lay.addWidget(label("ModelShelf looks in these folders (and all sub-folders). Files are only read, "
                            "never changed or moved.", "sub", wrap=True))
        self.roots = QListWidget()
        self.roots.setMinimumHeight(110)
        self.roots.setStyleSheet(f"QListWidget {{ background: {theme.C['input']}; border: 1px solid "
                                 f"{theme.C['border']}; border-radius: {theme.R_MD}px; padding: 4px; }}")
        for r in s.roots:
            self.roots.addItem(r)
        lay.addWidget(self.roots)
        row = QHBoxLayout()
        add = icon_button("folder_plus", "Add a folder", "Add folder…")
        rem = icon_button("x", "Remove the selected folder from the library", "Remove")
        add.clicked.connect(self._add_root)
        rem.clicked.connect(lambda: [self.roots.takeItem(self.roots.row(i)) for i in self.roots.selectedItems()])
        row.addWidget(add); row.addWidget(rem); row.addStretch()
        lay.addLayout(row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        self.inc_gcode = QCheckBox("G-code (sliced files)"); self.inc_gcode.setChecked(s.include_gcode)
        self.inc_zip = QCheckBox("ZIP archives (downloads from Thingiverse, Printables…)"); self.inc_zip.setChecked(s.include_zip)
        self.inc_cad = QCheckBox("CAD source files (STEP, F3D, SCAD… listed without preview)"); self.inc_cad.setChecked(s.include_cad)
        types = QVBoxLayout()
        types.setSpacing(4)
        types.setContentsMargins(0, 2, 0, 0)
        types.addWidget(label("STL, 3MF and OBJ are always included.", "muted"))
        for w in (self.inc_gcode, self.inc_zip, self.inc_cad):
            types.addWidget(w)
        form.addRow(label("Also include"), types)
        self.exclude = QLineEdit(", ".join(s.exclude))
        self.exclude.setToolTip("Folder names to skip, comma separated")
        form.addRow(label("Skip folders named"), self.exclude)
        lay.addLayout(form)

        lay.addWidget(hline())
        lay.addWidget(label("Appearance", "h2"))
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        self.theme_box = QComboBox()
        for key, txt in (("system", "Follow Windows"), ("dark", "Dark"), ("light", "Light")):
            self.theme_box.addItem(txt, key)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(s.theme)))
        form.addRow(label("Theme"), self.theme_box)
        self.color_btn = QPushButton()
        self._color = s.default_color
        self._paint_color_btn()
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow(label("Colour for models\nwithout colour info"), self.color_btn)
        self.embedded = QCheckBox("Use the slicer's own preview picture when a file has one")
        self.embedded.setChecked(s.prefer_embedded)
        form.addRow(label("Thumbnails"), self.embedded)
        rer = QPushButton("Re-render all thumbnails")
        rer.setToolTip("Needed after changing the colour or thumbnail source")
        rer.clicked.connect(self._rerender)
        form.addRow(label(""), rer)
        lay.addLayout(form)

        lay.addWidget(hline())
        lay.addWidget(label("Filament estimate", "h2"))
        lay.addWidget(label("Used for the weight shown on models that haven't been sliced. It's an estimate: "
                            "solid walls plus partial infill.", "sub", wrap=True))
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        self.material = QComboBox()
        for m, d in MATERIALS.items():
            self.material.addItem(f"{m}  ({d} g/cm³)", m)
        self.material.setCurrentIndex(max(0, self.material.findData(s.material)))
        form.addRow(label("Material"), self.material)
        self.infill = QSpinBox(); self.infill.setRange(0, 100); self.infill.setSuffix(" %"); self.infill.setValue(s.infill)
        form.addRow(label("Infill"), self.infill)
        self.wall = QDoubleSpinBox(); self.wall.setRange(0.2, 10); self.wall.setSingleStep(0.2); self.wall.setSuffix(" mm"); self.wall.setValue(s.wall_mm)
        form.addRow(label("Wall thickness"), self.wall)
        self.spool = QSpinBox(); self.spool.setRange(100, 10000); self.spool.setSingleStep(250); self.spool.setSuffix(" g"); self.spool.setValue(s.spool_g)
        form.addRow(label("Spool size"), self.spool)
        lay.addLayout(form)

        lay.addWidget(hline())
        lay.addWidget(label("Slicer & performance", "h2"))
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        srow = QHBoxLayout()
        self.slicer = QComboBox()
        self.slicer.setEditable(True)
        self.slicer.addItems(find_slicers())
        self.slicer.setCurrentText(s.slicer_path)
        self.slicer.lineEdit().setPlaceholderText("Path to your slicer .exe")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_slicer)
        srow.addWidget(self.slicer, 1); srow.addWidget(browse)
        form.addRow(label("Slicer"), srow)
        self.printer_url = QLineEdit(s.printer_url)
        self.printer_url.setPlaceholderText("http://192.168.1.x/…  (the printer's web page)")
        form.addRow(label("Printer web page"), self.printer_url)
        self.printer_name = QLineEdit(s.printer_name)
        form.addRow(label("Printer name"), self.printer_name)
        self.workers = QSpinBox(); self.workers.setRange(0, 64); self.workers.setSpecialValueText("Automatic")
        self.workers.setValue(s.workers)
        form.addRow(label("Parallel workers"), self.workers)
        self.lowprio = QCheckBox("Scan at low priority (keeps the PC responsive)")
        self.lowprio.setChecked(s.low_priority)
        form.addRow(label(""), self.lowprio)
        lay.addLayout(form)
        lay.addWidget(label("Everything stays on this computer: no accounts, no uploads. The only network "
                            "use is opening your printer's own web page on your local network, if you set one.",
                            "muted", wrap=True))
        lay.addStretch()

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setProperty("variant", "primary")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        foot = QHBoxLayout()
        foot.setContentsMargins(22, 8, 22, 14)
        foot.addWidget(bb)
        outer.addLayout(foot)
        self.rerender = False

    def _paint_color_btn(self):
        self.color_btn.setText(f"  {self._color.upper()}")
        px = icons.pixmap("cube", 16, self._color)
        from PySide6.QtGui import QIcon
        self.color_btn.setIcon(QIcon(px))

    def _pick_color(self):
        col = QColorDialog.getColor(QColor(self._color), self, "Model colour")
        if col.isValid():
            self._color = col.name()
            self._paint_color_btn()

    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, "Add library folder")
        if d:
            d = os.path.normpath(d)
            existing = [self.roots.item(i).text() for i in range(self.roots.count())]
            if d not in existing:
                self.roots.addItem(d)

    def _browse_slicer(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose slicer", r"C:\Program Files", "Programs (*.exe)")
        if f:
            self.slicer.setCurrentText(os.path.normpath(f))

    def _rerender(self):
        self.rerender = True
        self._save()

    def _save(self):
        s = self.s
        s.roots = [self.roots.item(i).text() for i in range(self.roots.count())]
        s.include_gcode, s.include_zip, s.include_cad = (
            self.inc_gcode.isChecked(), self.inc_zip.isChecked(), self.inc_cad.isChecked())
        s.exclude = [x.strip() for x in self.exclude.text().split(",") if x.strip()]
        s.theme = self.theme_box.currentData()
        if s.default_color.lower() != self._color.lower() or s.prefer_embedded != self.embedded.isChecked():
            self.rerender = True
        s.default_color = self._color
        s.prefer_embedded = self.embedded.isChecked()
        s.material = self.material.currentData()
        s.infill, s.wall_mm, s.spool_g = self.infill.value(), self.wall.value(), self.spool.value()
        s.slicer_path = self.slicer.currentText().strip()
        url = self.printer_url.text().strip()
        if url and "://" not in url:
            url = "http://" + url
        s.printer_url = url
        s.printer_name = self.printer_name.text().strip() or "Printer"
        s.workers = self.workers.value()
        s.low_priority = self.lowprio.isChecked()
        self.accept()


# --------------------------------------------------------------------------- duplicates

class DuplicatesDialog(QDialog):
    """Groups files with identical content (SHA-256), lets you recycle extra copies."""

    recycle = Signal(list)       # items
    ignore = Signal(str, bool)
    reveal = Signal(object)

    def __init__(self, items: list[Item], ignored: set[str], thumbs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicate files")
        self.resize(920, 720)
        self.thumbs = thumbs
        self.ignored = set(ignored)
        groups: dict[str, list[Item]] = {}
        for it in items:
            if it.sha256:
                groups.setdefault(it.sha256, []).append(it)
        self.groups = sorted(((sha, g) for sha, g in groups.items() if len(g) > 1),
                             key=lambda sg: -(sg[1][0].size or 0) * (len(sg[1]) - 1))
        self.checks: list[tuple[QCheckBox, Item, str]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        head = QHBoxLayout()
        head.addWidget(label("Duplicate files", "h1"))
        head.addStretch()
        self.summary = label("", "sub")
        head.addWidget(self.summary)
        lay.addLayout(head)
        lay.addWidget(label("Files with byte-for-byte identical content, whatever their names. "
                            "The suggested keeper is the copy with tags/notes, else the oldest.", "sub", wrap=True))
        tools = QHBoxLayout()
        sel = QPushButton("Select all extra copies")
        sel.clicked.connect(self._select_extras)
        none = QPushButton("Clear selection")
        none.clicked.connect(lambda: [c.setChecked(False) for c, _, _ in self.checks])
        self.show_ignored = QCheckBox("Show ignored sets")
        self.show_ignored.toggled.connect(self._build)
        tools.addWidget(sel); tools.addWidget(none); tools.addStretch(); tools.addWidget(self.show_ignored)
        lay.addLayout(tools)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        lay.addWidget(self.scroll, 1)

        foot = QHBoxLayout()
        self.sel_lbl = label("", "sub")
        foot.addWidget(self.sel_lbl)
        foot.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.go = icon_button("trash", "Move the selected files to the Windows Recycle Bin",
                              "Move selected to Recycle Bin", variant="danger")
        self.go.clicked.connect(self._recycle)
        foot.addWidget(close); foot.addWidget(self.go)
        lay.addLayout(foot)
        self._build()

    def _keeper(self, g: list[Item]) -> Item:
        return sorted(g, key=lambda i: (not (i.tags or i.notes or i.status or i.favorite), i.mtime or 0,
                                        len(i.path)))[0]

    def _build(self):
        self.checks = []
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 6, 8, 6)
        lay.setSpacing(10)
        shown = 0
        wasted = 0
        for sha, g in self.groups:
            ign = sha in self.ignored
            if not ign:
                wasted += (g[0].size or 0) * (len(g) - 1)
            if ign and not self.show_ignored.isChecked():
                continue
            shown += 1
            if shown > 400:
                lay.addWidget(label("Showing the 400 largest sets.", "muted"))
                break
            card = QFrame()
            card.setProperty("role", "panel")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.setSpacing(6)
            h = QHBoxLayout()
            h.addWidget(label(f"{len(g)}×  {fmt_size(g[0].size)} each", "mono"))
            h.addStretch()
            w = label(f"{fmt_size((g[0].size or 0) * (len(g) - 1))} wasted", "mono")
            w.setStyleSheet(f"color: {theme.C['accent']};")
            h.addWidget(w)
            ib = QCheckBox("Ignore this set")
            ib.setChecked(ign)
            ib.toggled.connect(lambda on, s=sha: self._toggle_ignore(s, on))
            h.addWidget(ib)
            cl.addLayout(h)
            keep = self._keeper(g)
            for it in sorted(g, key=lambda i: i.path.lower()):
                r = QHBoxLayout()
                cb = QCheckBox()
                cb.toggled.connect(self._update_sel)
                r.addWidget(cb)
                th = QLabel()
                th.setFixedSize(46, 46)
                px = self.thumbs.get(it.thumb) if it.thumb else None
                if px is not None:
                    th.setPixmap(px.scaled(46, 46, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                th.setStyleSheet(f"background: {theme.C['stage_out']}; border-radius: {theme.R_SM}px;")
                r.addWidget(th)
                col = QVBoxLayout()
                col.setSpacing(0)
                col.addWidget(label(it.name, "sub"))
                p = label(it.folder, "muted")
                p.setToolTip(it.path)
                col.addWidget(p)
                r.addLayout(col, 1)
                r.addWidget(label(fmt_date(it.mtime), "mono"))
                if it is keep:
                    k = label("keep")
                    k.setStyleSheet(f"color: {theme.C['success']}; border: 1px solid {theme.C['success']};"
                                    f"border-radius: {theme.R_SM}px; padding: 1px 6px; font-size: 11px;")
                    r.addWidget(k, 0, Qt.AlignVCenter)
                show = icon_button("explorer", "Show in File Explorer", variant="ghost", size=14)
                show.clicked.connect(lambda _=False, i=it: self.reveal.emit(i))
                r.addWidget(show)
                cl.addLayout(r)
                self.checks.append((cb, it, sha))
            lay.addWidget(card)
        if not self.groups:
            lay.addWidget(label("No duplicates found. Nice and tidy.", "sub"))
        lay.addStretch()
        self.scroll.setWidget(body)
        active = sum(1 for sha, _ in self.groups if sha not in self.ignored)
        extra = sum(len(g) - 1 for sha, g in self.groups if sha not in self.ignored)
        self.summary.setText(f"{active} sets · {extra} extra copies · {fmt_size(wasted)} reclaimable")
        self._update_sel()

    def _toggle_ignore(self, sha, on):
        if on:
            self.ignored.add(sha)
        else:
            self.ignored.discard(sha)
        self.ignore.emit(sha, on)
        self._build()

    def _select_extras(self):
        by_sha: dict[str, list] = {}
        for cb, it, sha in self.checks:
            by_sha.setdefault(sha, []).append((cb, it))
        for sha, rows in by_sha.items():
            if sha in self.ignored:
                continue
            keep = self._keeper([it for _, it in rows])
            for cb, it in rows:
                cb.setChecked(it is not keep)

    def _selected(self) -> list[Item]:
        return [it for cb, it, _ in self.checks if cb.isChecked()]

    def _update_sel(self):
        sel = self._selected()
        self.sel_lbl.setText(f"{len(sel)} selected · {fmt_size(sum(i.size or 0 for i in sel))}" if sel else "")
        self.go.setEnabled(bool(sel))

    def _recycle(self):
        sel = self._selected()
        by_sha: dict[str, int] = {}
        for _, it, sha in self.checks:
            by_sha[sha] = by_sha.get(sha, 0) + 1
        for sha in {s for _, it, s in self.checks if it in sel}:
            if sum(1 for it2 in sel if it2.sha256 == sha) >= by_sha[sha]:
                QMessageBox.warning(self, "Every copy selected",
                                    "You've selected every copy in at least one set. Leave one copy "
                                    "unticked in each set so nothing is lost.")
                return
        self.recycle.emit(sel)
        sel_ids = {i.id for i in sel}
        for i, (sha, g) in enumerate(self.groups):
            self.groups[i] = (sha, [x for x in g if x.id not in sel_ids])
        self.groups = [(s, g) for s, g in self.groups if len(g) > 1]
        self._build()


# --------------------------------------------------------------------------- log

class LogWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log")
        self.resize(820, 480)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet(f"font-family: {theme.MONO}; font-size: 12px;")
        self.text.setMaximumBlockCount(20000)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.text.clear)
        row.addWidget(clear)
        lay.addLayout(row)

    def append(self, line: str):
        self.text.appendPlainText(line)
