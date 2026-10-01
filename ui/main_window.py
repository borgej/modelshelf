"""Main window: header, filter chips, sidebar | grid | details, status bar."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time

from PySide6.QtCore import QByteArray, QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QSlider, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from core.db import Library
from core.system import FILE_MANAGER, TRASH, find_slicers, launch, reveal, slicer_takes_zip, stop_workers
from core.mesh import fmt_date
from core.scanner import Scanner, ScanStats
from core.settings import Settings, estimate_grams, save as save_settings, slicer_name, thumbs_dir
from ui import icons, theme
from ui.detail import DetailPanel
from ui.dialogs import AboutDialog, COFFEE_URL, DuplicatesDialog, LogWindow, SettingsDialog
from ui.grid import LibraryGrid, fmt_size
from ui.library_model import SORTS, LibraryModel, ThumbCache
from ui.sidebar import Sidebar
from ui.widgets import SearchBox, hline, icon_button, label, open_external, refresh_icons
from version import __version__


class ScanThread(QThread):
    progress = Signal(str, int, int, str)
    item_changed = Signal(int)
    items_removed = Signal(list)
    log = Signal(str)
    finished_stats = Signal(object)

    def __init__(self, lib, settings, only_paths=None):
        super().__init__()
        self.only_paths = only_paths
        self.scanner = Scanner(lib, settings, progress=self.progress.emit,
                               item_changed=self.item_changed.emit,
                               items_removed=self.items_removed.emit, log=self.log.emit)

    def run(self):
        try:
            stats = self.scanner.run(self.only_paths)
        except Exception as exc:
            import traceback
            self.log.emit("Scan failed: " + traceback.format_exc())
            stats = ScanStats(cancelled=True)
        self.finished_stats.emit(stats)

    def cancel(self):
        self.scanner.cancel()


class MainWindow(QMainWindow):
    log_line = Signal(str)

    def __init__(self, settings: Settings, theme_mgr: theme.ThemeManager):
        super().__init__()
        self.s = settings
        self.theme_mgr = theme_mgr
        self.lib = Library()
        self.model = LibraryModel(self)
        self.model.sort_key = settings.sort if settings.sort in SORTS else "name"
        self.thumbs = ThumbCache(parent=self)
        self.scan: ScanThread | None = None
        self.log_window = LogWindow(self)
        self.log_line.connect(self.log_window.append)
        self._pending_upserts: set[int] = set()
        self._scan_started = 0.0
        self._eta_samples: list[tuple[float, int]] = []

        self.setWindowTitle("ModelShelf")
        self.setWindowIcon(QIcon(icons.app_icon_pixmap()))
        self.resize(1480, 920)
        self.setAcceptDrops(True)

        self._build()
        theme_mgr.changed.connect(self._on_theme)
        self.model.visibleChanged.connect(self._update_stats)
        self.model.visibleChanged.connect(self._update_count)
        self.grid.selectionModel().selectionChanged.connect(self._selection_changed)
        self.model.modelReset.connect(self._selection_changed)

        self._refilter_timer = QTimer(self, singleShot=True, interval=350, timeout=self._refilter_keep)
        self._search_timer = QTimer(self, singleShot=True, interval=160, timeout=self._apply_search)
        self._sidebar_timer = QTimer(self, singleShot=True, interval=800, timeout=self._rebuild_sidebar)

        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))
        if settings.splitter_state:
            self.splitter.restoreState(QByteArray.fromBase64(settings.splitter_state.encode()))

        if not self.s.slicer_path:
            found = find_slicers()
            if found:
                self.s.slicer_path = found[0]
                save_settings(self.s)
        self._update_printer_button()
        self._load_library()
        self.log(f"ModelShelf {__version__} — data in {os.path.dirname(self.lib.path)}")
        from core.settings import _path as settings_path
        sp = settings_path()
        try:
            stamp = dt.datetime.fromtimestamp(sp.stat().st_mtime).strftime("%d.%m.%Y %H:%M:%S")
        except OSError:
            stamp = "missing"
        self.log(f"Settings file {sp} (saved {stamp}); library folders: {self.s.roots or 'none'}; "
                 f"slicer: {self.s.slicer_path or 'none'}; printer page: {self.s.printer_url or 'none'}")
        if self.s.roots:
            QTimer.singleShot(300, self.start_scan)

    # ================================================================== layout
    def _build(self):
        central = QWidget()
        central.setProperty("role", "page")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- header
        header = QFrame()
        header.setProperty("role", "header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 12, 18, 12)
        hl.setSpacing(10)
        logo = QLabel()
        px = icons.app_icon_pixmap(80)
        px.setDevicePixelRatio(2.0)
        logo.setPixmap(px)
        hl.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(label("ModelShelf", "h1"))
        brand.addWidget(label("Local 3D model library", "muted"))
        hl.addLayout(brand)
        hl.addSpacing(18)
        self.search = SearchBox()
        self.search.setMinimumWidth(260)
        self.search.setMaximumWidth(520)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        hl.addWidget(self.search, 1)
        self.sort_box = QComboBox()
        for key, (txt, _, _) in SORTS.items():
            self.sort_box.addItem(f"Sort: {txt}", key)
        self.sort_box.setCurrentIndex(max(0, self.sort_box.findData(self.model.sort_key)))
        self.sort_box.currentIndexChanged.connect(self._sort_changed)
        hl.addWidget(self.sort_box)
        hl.addStretch()
        self.b_dupes = icon_button("copies", "Find files with identical content", "Duplicates")
        self.b_dupes.clicked.connect(self.show_duplicates)
        self.b_log = icon_button("log", "Show the log", variant="ghost")
        self.b_log.clicked.connect(self.log_window.show)
        self.b_theme = icon_button("moon", "Switch theme", variant="ghost")
        self.b_theme.clicked.connect(self._theme_menu)
        self.b_printer = icon_button("globe", "", self.s.printer_name)
        self.b_printer.clicked.connect(self.open_printer)
        self.b_settings = icon_button("sliders", "Settings", variant="ghost")
        self.b_settings.clicked.connect(self.open_settings)
        self.b_about = icon_button("info", "About ModelShelf", variant="ghost")
        self.b_about.clicked.connect(self.show_about)
        self.b_coffee = icon_button("coffee", f"Like ModelShelf? Buy me a coffee\n{COFFEE_URL}", variant="ghost")
        self.b_coffee.setProperty("icon_color", "warning")
        self.b_coffee.clicked.connect(lambda: open_external(COFFEE_URL))
        self.b_scan = icon_button("refresh", "Look for new and changed files (F5)", "Scan library", variant="primary")
        self.b_scan.setProperty("icon_color", "accent_text")
        self.b_scan.clicked.connect(self.start_scan)
        for b in (self.b_printer, self.b_dupes, self.b_log, self.b_theme, self.b_settings, self.b_about, self.b_coffee,
                  self.b_scan):
            hl.addWidget(b)
        root.addWidget(header)

        page = QVBoxLayout()
        page.setContentsMargins(16, 14, 16, 0)
        page.setSpacing(12)
        root.addLayout(page, 1)

        # ---- chips
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.status_chips: dict[str, QPushButton] = {}
        for key, txt in (("", "All"), ("favorite", "★ Favourites"), ("to_print", "To print"),
                         ("printed", "Printed")):
            b = QPushButton(txt)
            b.setProperty("variant", "chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._set_status_filter(k))
            chips.addWidget(b)
            self.status_chips[key] = b
        self.status_chips[""].setChecked(True)
        sep = QFrame()
        sep.setFixedSize(1, 22)
        sep.setProperty("role", "divider")
        chips.addSpacing(6)
        chips.addWidget(sep)
        chips.addSpacing(6)
        self.kind_chips: dict[str, QPushButton] = {}
        for kind in ("STL", "3MF", "OBJ", "GCODE", "ZIP", "CAD"):
            b = QPushButton(kind)
            b.setProperty("variant", "chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(self._kinds_changed)
            chips.addWidget(b)
            self.kind_chips[kind] = b
        chips.addStretch()
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(150, 380)
        self.size_slider.setValue(self.s.card_size)
        self.size_slider.setFixedWidth(120)
        self.size_slider.setToolTip("Card size")
        self.size_slider.valueChanged.connect(self._card_size)
        self.b_panel = icon_button("panel", "Show / hide the details panel", variant="ghost")
        self.b_panel.setCheckable(True)
        self.b_panel.setChecked(self.s.show_details)
        self.b_panel.toggled.connect(self._toggle_details)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("image", 14, theme.C["muted"]))
        chips.addWidget(ic)
        chips.addWidget(self.size_slider)
        chips.addWidget(self.b_panel)
        page.addLayout(chips)

        # ---- body
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(12)
        self.splitter.setChildrenCollapsible(False)
        self.sidebar = Sidebar()
        self.sidebar.folder_selected.connect(self._folder_filter)
        self.sidebar.designer_selected.connect(self._designer_filter)
        self.sidebar.tag_selected.connect(self._tag_filter)
        self.splitter.addWidget(self.sidebar)

        self.center = QStackedWidget()
        self.grid = LibraryGrid(self.model, self.thumbs)
        self.grid.set_card_size(self.s.card_size)
        self.grid.activated_item.connect(lambda it: self._act("open", [it]))
        self.grid.context_requested.connect(self._context_menu)
        self.center.addWidget(self.grid)
        self.empty = self._build_empty()
        self.center.addWidget(self.empty)
        self.splitter.addWidget(self.center)

        self.detail = DetailPanel(self.s, self.thumbs)
        self.detail.action.connect(self._act)
        self.detail.viewer.status.connect(self.log)
        self.detail.user_changed.connect(self._user_changed)
        self.detail.tag_clicked.connect(self.sidebar.select_tag)
        self.detail.closed.connect(lambda: self.b_panel.setChecked(False))
        self.detail.setVisible(False)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([260, 900, 360])
        page.addWidget(self.splitter, 1)
        page.addSpacing(10)

        # ---- status bar
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.setContentsMargins(14, 2, 14, 2)
        self.scan_lbl = QLabel("")
        self.scan_bar = QProgressBar()
        self.scan_bar.setFixedWidth(220)
        self.scan_bar.setTextVisible(False)
        self.scan_bar.setVisible(False)
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.setProperty("variant", "ghost")
        self.b_cancel.setStyleSheet("padding: 2px 10px;")
        self.b_cancel.setFixedHeight(24)
        self.b_cancel.setVisible(False)
        self.b_cancel.clicked.connect(self.cancel_scan)
        self.count_lbl = QLabel("")
        sb.addWidget(self.scan_bar)
        sb.addWidget(self.scan_lbl, 1)
        sb.addWidget(self.b_cancel)
        sb.addPermanentWidget(self.count_lbl)

        # ---- shortcuts
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence("F5"), self, self.start_scan)
        QShortcut(QKeySequence(Qt.Key_Delete), self.grid, lambda: self._act("recycle", self.grid.selected_items()))
        QShortcut(QKeySequence("Escape"), self.search, lambda: self.search.clear())
        QShortcut(QKeySequence("Ctrl+O"), self, lambda: self._act("slicer", self.grid.selected_items()))
        QShortcut(QKeySequence("Ctrl+E"), self, lambda: self._act("explorer", self.grid.selected_items()))
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, lambda: self._act("copy_path", self.grid.selected_items()))

    def _build_empty(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        self.empty_icon = QLabel(alignment=Qt.AlignCenter)
        lay.addWidget(self.empty_icon)
        self.empty_title = label("Add your model folders", "h1")
        self.empty_title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_title)
        self.empty_sub = label("Point ModelShelf at the folders where your STL, 3MF and other downloads live.\n"
                               "It builds thumbnails and a searchable library — nothing leaves this PC.", "sub")
        self.empty_sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_sub)
        row = QHBoxLayout()
        row.addStretch()
        self.empty_btn = icon_button("folder_plus", "Add a folder to the library", "Add folder…", variant="primary")
        self.empty_btn.setProperty("icon_color", "accent_text")
        self.empty_btn.clicked.connect(self.add_folder)
        row.addWidget(self.empty_btn)
        self.empty_clear = QPushButton("Clear filters")
        self.empty_clear.clicked.connect(self.clear_filters)
        row.addWidget(self.empty_clear)
        row.addStretch()
        lay.addSpacing(8)
        lay.addLayout(row)
        lay.addStretch()
        return w

    # ================================================================== data
    def log(self, msg: str):
        line = f"{dt.datetime.now():%H:%M:%S}  {msg}"
        self.log_line.emit(line)
        try:
            with open(os.path.join(os.path.dirname(self.lib.path), "modelshelf.log"), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _load_library(self):
        self.model.set_items(self.lib.all_items())
        self._rebuild_sidebar()
        self._update_kind_counts()

    def _roots_norm(self):
        return [os.path.normpath(r) for r in self.s.roots]

    def _rebuild_sidebar(self):
        self.sidebar.rebuild(list(self.model.items.values()), self._roots_norm())
        self._update_kind_counts()

    def _update_kind_counts(self):
        counts: dict[str, int] = {}
        status = {"favorite": 0, "to_print": 0, "printed": 0}
        for it in self.model.items.values():
            counts[it.kind] = counts.get(it.kind, 0) + 1
            if it.favorite:
                status["favorite"] += 1
            if it.status in status:
                status[it.status] += 1
        for kind, b in self.kind_chips.items():
            n = counts.get(kind, 0)
            b.setText(f"{kind if kind != 'GCODE' else 'G-code'} · {n}")
            b.setVisible(n > 0 or b.isChecked())
        self.status_chips[""].setText(f"All · {len(self.model.items)}")
        self.status_chips["favorite"].setText(f"★ Favourites · {status['favorite']}")
        self.status_chips["to_print"].setText(f"To print · {status['to_print']}")
        self.status_chips["printed"].setText(f"Printed · {status['printed']}")

    def _update_stats(self):
        """Disk usage of what's in view, shown at the foot of the sidebar."""
        vis = self.model.visible
        size = fmt_size(sum(i.size or 0 for i in vis))
        filtered = len(vis) != len(self.model.items)
        self.sidebar.set_footer(f"{size} on disk" + ("  ·  in view" if filtered else ""))

    def _update_count(self):
        n, total = len(self.model.visible), len(self.model.items)
        self.count_lbl.setText(f"{n} of {total} shown" if n != total else f"{total} models")
        if total == 0:
            self.center.setCurrentWidget(self.empty)
            no_roots = not self.s.roots
            self.empty_title.setText("Add your model folders" if no_roots else "Scanning your library…"
                                     if self.scan else "No models found")
            self.empty_sub.setVisible(no_roots)
            self.empty_btn.setVisible(no_roots or not self.scan)
            self.empty_clear.setVisible(False)
        elif n == 0:
            self.center.setCurrentWidget(self.empty)
            self.empty_title.setText("Nothing matches")
            self.empty_sub.setVisible(False)
            self.empty_btn.setVisible(False)
            self.empty_clear.setVisible(True)
        else:
            self.center.setCurrentWidget(self.grid)
        self.empty_icon.setPixmap(icons.pixmap("cube", 56, theme.C["muted"]))

    # ================================================================== filtering
    def _refilter_keep(self):
        """Refilter while keeping selection and scroll position."""
        sel_ids = [i.id for i in self.grid.selected_items()]
        cur = self.grid.currentIndex()
        cur_id = cur.data(Qt.UserRole + 1).id if cur.isValid() else None
        scroll = self.grid.verticalScrollBar().value()
        self.model.refilter()
        self._restore_selection(sel_ids, cur_id)
        self.grid.verticalScrollBar().setValue(scroll)

    def _restore_selection(self, ids, cur_id=None):
        sm = self.grid.selectionModel()
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        sel = QItemSelection()
        for i in ids:
            r = self.model.row_of(i)
            if r is not None:
                idx = self.model.index(r)
                sel.select(idx, idx)
        sm.select(sel, QItemSelectionModel.ClearAndSelect)
        if cur_id is not None and self.model.row_of(cur_id) is not None:
            sm.setCurrentIndex(self.model.index(self.model.row_of(cur_id)), QItemSelectionModel.NoUpdate)

    def _apply_search(self):
        self.model.filter.text = self.search.text()
        self.model.refilter()

    def _set_status_filter(self, key):
        for k, b in self.status_chips.items():
            b.setChecked(k == key)
        self.model.filter.status = key
        self.model.refilter()

    def _kinds_changed(self):
        self.model.filter.kinds = {k for k, b in self.kind_chips.items() if b.isChecked()}
        self.model.refilter()

    def _folder_filter(self, folder):
        self.model.filter.folder = folder
        self.model.refilter()

    def _designer_filter(self, d):
        self.model.filter.designer = d
        self.model.refilter()

    def _tag_filter(self, t):
        self.model.filter.tag = t
        self.model.refilter()

    def clear_filters(self):
        self.search.clear()
        self.model.filter.text = ""
        for b in self.kind_chips.values():
            b.setChecked(False)
        self.model.filter.kinds = set()
        self._set_status_filter("")
        self.model.filter.designer = None
        self.model.filter.tag = None
        self.sidebar.reset()
        self.sidebar.tree.setCurrentItem(self.sidebar.tree.topLevelItem(0))
        self.model.refilter()

    def _sort_changed(self):
        self.model.sort_key = self.sort_box.currentData()
        self.s.sort = self.model.sort_key
        self.model.refilter()

    def _card_size(self, v):
        self.s.card_size = v
        self.grid.set_card_size(v)

    def _toggle_details(self, on):
        self.s.show_details = on
        self._selection_changed()

    # ================================================================== selection & details
    def _selection_changed(self, *_):
        items = self.grid.selected_items()
        show = bool(items) and self.b_panel.isChecked()
        self.detail.setVisible(show)
        if show:
            tags = sorted({t for i in self.model.items.values() for t in i.tags}, key=str.lower)
            self.detail.set_items(items, tags)

    def _user_changed(self, ids, fields):
        self.lib.set_user(ids, **fields)
        for i in ids:
            it = self.lib.get(i)
            if it:
                self.model.upsert(it)
        if "tags" in fields:
            self._sidebar_timer.start()
        self._update_kind_counts()
        self._update_stats()
        if self.model.filter.status or self.model.filter.tag is not None:
            self._refilter_timer.start()
        sel = [self.model.items[i] for i in [x.id for x in self.grid.selected_items()] if i in self.model.items]
        if sel:
            tags = sorted({t for i in self.model.items.values() for t in i.tags}, key=str.lower)
            self.detail.set_items(sel, tags)

    # ================================================================== actions
    def _act(self, name: str, items: list):
        items = [i for i in items if i is not None]
        if not items:
            return
        if name == "open":
            for it in items[:10]:
                open_external(it.path)
        elif name == "slicer":
            if not self.s.slicer_path or not os.path.exists(self.s.slicer_path):
                QMessageBox.information(self, "No slicer set", "Choose your slicer in Settings first.")
                return
            files = self._slicer_files(items[:20])
            if not files:
                QMessageBox.information(self, "Nothing to open",
                                        f"None of the selected files can be opened in {slicer_name(self.s.slicer_path)}.")
                return
            launch([self.s.slicer_path, *files])
            self.log(f"Opened in {slicer_name(self.s.slicer_path)}: " + ", ".join(os.path.basename(f) for f in files))
            self.statusBar().showMessage(f"Opening {len(files)} file(s) in {slicer_name(self.s.slicer_path)}…", 4000)
        elif name == "explorer":
            reveal(items[0].path)
        elif name == "copied_path":
            self.statusBar().showMessage("Path copied", 2500)
        elif name == "copy_path":
            QGuiApplication.clipboard().setText("\n".join(i.path for i in items))
            self.statusBar().showMessage("Path copied", 2500)
        elif name == "reanalyze":
            self.start_scan(only_paths=[i.path for i in items])
        elif name == "recycle":
            self.recycle(items)
        elif name in ("to_print", "printed", ""):
            self._user_changed([i.id for i in items], {"status": name})
        elif name == "favorite":
            on = not all(i.favorite for i in items)
            self._user_changed([i.id for i in items], {"favorite": on})

    def _slicer_files(self, items) -> list[str]:
        """Paths a slicer can open. ZIP downloads are unpacked (models only) to a
        temp folder first — the original archive is never modified."""
        import tempfile
        import zipfile
        from core.formats import MESH_EXTS, GCODE_EXTS
        out = []
        # ElegooSlicer and OrcaSlicer import .zip downloads themselves.
        takes_zip = slicer_takes_zip(self.s.slicer_path)
        for it in items:
            if it.kind in ("STL", "3MF", "OBJ", "GCODE") or (it.kind == "ZIP" and takes_zip):
                out.append(it.path)
            elif it.kind == "ZIP":
                dest = os.path.join(tempfile.gettempdir(), "ModelShelf", "unzipped",
                                    f"{os.path.splitext(it.name)[0]}-{it.sha256[:8]}")
                try:
                    with zipfile.ZipFile(it.path) as zf:
                        for info in zf.infolist():
                            ext = os.path.splitext(info.filename)[1].lower()
                            if info.is_dir() or ext not in MESH_EXTS | GCODE_EXTS:
                                continue
                            target = os.path.join(dest, os.path.basename(info.filename))
                            if not os.path.exists(target):
                                os.makedirs(dest, exist_ok=True)
                                with zf.open(info) as src, open(target, "wb") as dst:
                                    dst.write(src.read())
                            out.append(target)
                except (OSError, zipfile.BadZipFile) as exc:
                    self.log(f"Could not unpack {it.name}: {exc}")
        return out

    def open_printer(self):
        if self.s.printer_url:
            open_external(self.s.printer_url)
        else:
            self.open_settings()

    def _update_printer_button(self):
        self.b_printer.setVisible(bool(self.s.printer_url))
        self.b_printer.setText(self.s.printer_name or "Printer")
        self.b_printer.setToolTip(f"Open the printer's web page\n{self.s.printer_url}")

    def _context_menu(self, pos):
        idx = self.grid.indexAt(pos)
        if not idx.isValid():
            return
        if not self.grid.selectionModel().isSelected(idx):
            self.grid.setCurrentIndex(idx)
        items = self.grid.selected_items()
        m = QMenu(self)
        m.addAction(icons.icon("open"), "Open", lambda: self._act("open", items))
        a = m.addAction(icons.icon("slicer"), f"Open in {slicer_name(self.s.slicer_path)}	Ctrl+O",
                        lambda: self._act("slicer", items))
        a.setEnabled(bool(self.s.slicer_path))
        m.addAction(icons.icon("explorer"), f"Show in {FILE_MANAGER}\tCtrl+E", lambda: self._act("explorer", items))
        m.addAction(icons.icon("copy"), "Copy path\tCtrl+Shift+C", lambda: self._act("copy_path", items))
        m.addSeparator()
        in_q = all(i.status == "to_print" for i in items)
        m.addAction(icons.icon("clock"), "Remove from print queue" if in_q else "Add to print queue",
                    lambda: self._act("" if in_q else "to_print", items))
        done = all(i.status == "printed" for i in items)
        m.addAction(icons.icon("check_circle"), "Unmark printed" if done else "Mark as printed",
                    lambda: self._act("" if done else "printed", items))
        fav = all(i.favorite for i in items)
        m.addAction(icons.icon("star"), "Remove favourite" if fav else "Favourite", lambda: self._act("favorite", items))
        m.addSeparator()
        m.addAction(icons.icon("refresh"), "Re-analyse", lambda: self._act("reanalyze", items))
        m.addAction(icons.icon("trash", color=theme.C["danger"]), f"Move to {TRASH}…\tDel",
                    lambda: self._act("recycle", items))
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def recycle(self, items):
        n = len(items)
        size = sum(i.size or 0 for i in items)
        names = "\n".join(f"  • {i.name}" for i in items[:8]) + (f"\n  … and {n - 8} more" if n > 8 else "")
        if QMessageBox.question(
                self, f"Move to {TRASH}",
                f"Move {n} file{'s' if n != 1 else ''} ({fmt_size(size)}) to the {TRASH}?\n\n{names}\n\n"
                f"You can restore them from the {TRASH}.") != QMessageBox.Yes:
            return False
        from send2trash import send2trash
        done = []
        for it in items:
            try:
                send2trash(os.path.normpath(it.path))
                done.append(it)
                self.log(f"Moved to {TRASH}: {it.path}")
            except Exception as exc:
                self.log(f"Could not recycle {it.path}: {exc}")
                QMessageBox.warning(self, "Could not move file", f"{it.path}\n\n{exc}")
        removed = self.lib.remove_paths([i.path for i in done])
        for _, thumb in removed:
            if thumb:
                try:
                    (thumbs_dir() / thumb).unlink()
                except OSError:
                    pass
        self.model.remove_ids([i for i, _ in removed])
        self._sidebar_timer.start()
        self._update_kind_counts()
        return True

    # ================================================================== scanning
    def start_scan(self, only_paths=None):
        if self.scan is not None:
            return
        if not self.s.roots and not only_paths:
            self.add_folder()
            return
        self.scan = ScanThread(self.lib, self.s, only_paths if isinstance(only_paths, list) else None)
        self.scan.progress.connect(self._scan_progress)
        self.scan.item_changed.connect(self._scan_item)
        self.scan.items_removed.connect(self._scan_removed)
        self.scan.log.connect(self.log)
        self.scan.finished_stats.connect(self._scan_done)
        self.b_scan.setEnabled(False)
        self.b_scan.setText("Scanning…")
        self.scan_bar.setVisible(True)
        self.scan_bar.setRange(0, 0)
        self.b_cancel.setVisible(True)
        self.b_cancel.setEnabled(True)
        self._scan_started = time.monotonic()
        self._eta_samples = []
        self._update_count()
        self.scan.start()

    def cancel_scan(self):
        if self.scan:
            self.scan.cancel()
            self.b_cancel.setEnabled(False)
            self.scan_lbl.setText("Stopping — finishing the files already in progress…")

    def _scan_progress(self, phase, done, total, detail):
        if phase == "discover":
            self.scan_bar.setRange(0, 0)
            self.scan_lbl.setText(f"Looking for files… {done} found   {detail}")
        else:
            self.scan_bar.setRange(0, max(total, 1))
            self.scan_bar.setValue(done)
            now = time.monotonic()
            self._eta_samples.append((now, done))
            self._eta_samples = [s for s in self._eta_samples if now - s[0] < 20]
            eta = ""
            if len(self._eta_samples) > 2 and done < total:
                t0, d0 = self._eta_samples[0]
                rate = (done - d0) / max(now - t0, 1e-3)
                if rate > 0:
                    rem = (total - done) / rate
                    eta = f" · about {int(rem // 60)}m {int(rem % 60):02d}s left" if rem >= 60 else f" · about {int(rem)}s left"
            self.scan_lbl.setText(f"Analysing {done} / {total}{eta}   —   {detail}")

    def _scan_item(self, item_id):
        it = self.lib.get(item_id)
        if it is None:                     # hidden (e.g. a zip that holds no models)
            if item_id in self.model.items:
                self.model.remove_ids([item_id])
            return
        old = self.model.items.get(item_id)
        if old and old.thumb and old.thumb != it.thumb:
            self.thumbs.forget(old.thumb)
            self.grid.delegate.forget(old.thumb)
        if self.model.upsert(it) or old is None:
            if not self._refilter_timer.isActive():
                self._refilter_timer.start()
        self._sidebar_timer.start()

    def _scan_removed(self, ids):
        self.model.remove_ids(ids)

    def _scan_done(self, stats: ScanStats):
        self.scan.wait()
        self.scan = None
        self.b_scan.setEnabled(True)
        self.b_scan.setText("Scan library")
        self.scan_bar.setVisible(False)
        self.b_cancel.setVisible(False)
        if stats.cancelled:
            msg = f"Scan cancelled — {stats.analyzed} files analysed before stopping."
        else:
            parts = [f"{stats.found} files" if stats.found else "", f"{stats.analyzed} analysed",
                     f"{stats.unchanged} unchanged" if stats.unchanged else "",
                     f"{stats.removed} removed" if stats.removed else "",
                     f"{stats.failed} without preview" if stats.failed else ""]
            msg = "Scan complete: " + ", ".join(p for p in parts if p) + f" ({stats.seconds:.1f} s)"
        self.scan_lbl.setText(msg)
        self.log(msg)
        self._refilter_keep()
        self._rebuild_sidebar()
        self._update_count()
        self._selection_changed()

    # ================================================================== dialogs
    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder with 3D models")
        if not d:
            return
        self._add_roots([d])

    def _save_settings(self):
        save_settings(self.s)
        from core.settings import _path as settings_path
        self.log(f"Settings saved to {settings_path()} — library folders: {self.s.roots or 'none'}")

    def _add_roots(self, dirs):
        added = False
        for d in dirs:
            d = os.path.normpath(d)
            if d not in self._roots_norm():
                self.s.roots.append(d)
                added = True
        if added:
            self._save_settings()
            self._rebuild_sidebar()
            self.start_scan()

    def open_settings(self):
        old_roots = list(self.s.roots)
        dlg = SettingsDialog(self.s, self)
        if dlg.exec():
            self._save_settings()
            self.theme_mgr.set_preference(self.s.theme)
            self._update_printer_button()
            if dlg.rerender:
                self.lib.clear_thumb_versions()
            self._update_stats()
            self._rebuild_sidebar()
            self._selection_changed()
            if dlg.rerender or sorted(old_roots) != sorted(self.s.roots):
                self.start_scan()

    def show_about(self):
        AboutDialog(__version__, os.path.dirname(self.lib.path), self).exec()

    def show_duplicates(self):
        dlg = DuplicatesDialog(list(self.model.items.values()), self.lib.ignored_duplicates(), self.thumbs, self)
        dlg.recycle.connect(self._dupes_recycle)
        dlg.ignore.connect(self.lib.ignore_duplicate)
        dlg.reveal.connect(lambda it: self._act("explorer", [it]))
        dlg.exec()

    def _dupes_recycle(self, items):
        self.recycle(items)

    def _theme_menu(self):
        m = QMenu(self)
        for key, txt, ic in (("system", "Follow Windows", "monitor"), ("dark", "Dark", "moon"), ("light", "Light", "sun")):
            a = m.addAction(icons.icon(ic), txt, lambda k=key: self._set_theme(k))
            a.setCheckable(True)
            a.setChecked(self.s.theme == key)
        m.exec(self.b_theme.mapToGlobal(self.b_theme.rect().bottomLeft()))

    def _set_theme(self, key):
        self.s.theme = key
        save_settings(self.s)
        self.theme_mgr.set_preference(key)

    def _on_theme(self):
        self.b_theme.setProperty("icon_name", "moon" if theme.MODE == "dark" else "sun")
        refresh_icons(self)
        self.search.refresh_icon()
        self.grid.delegate._scaled.clear()
        self.grid.viewport().update()
        self._rebuild_sidebar()
        self._update_count()
        self._update_stats()
        self.detail.viewer.update()
        if self.detail.isVisible():
            self.detail.items = []
            self._selection_changed()

    # ================================================================== window
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(os.path.isdir(u.toLocalFile()) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        dirs = [u.toLocalFile() for u in e.mimeData().urls() if os.path.isdir(u.toLocalFile())]
        if dirs:
            self._add_roots(dirs)

    def closeEvent(self, e):
        if self.scan:
            # Stop listening first: results still queued from the scan thread
            # must not reach a database that is about to be closed.
            for sig in (self.scan.item_changed, self.scan.items_removed, self.scan.progress,
                        self.scan.finished_stats):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self.scan.cancel()
            # Closing should be quick: don't wait for workers to finish the file
            # they are on. Everything already analysed is saved.
            stop_workers()
            self.scan.wait(8000)
        self.s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.s.splitter_state = bytes(self.splitter.saveState().toBase64()).decode()
        save_settings(self.s)
        self.lib.close()
        super().closeEvent(e)


def _install_error_hook(win: "MainWindow") -> None:
    """In the windowed .exe there is no console, so an exception inside a button
    handler would vanish without a trace. Log it and tell the user instead."""
    import traceback

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            win.log("ERROR: " + text.rstrip())
        except Exception:
            pass
        QMessageBox.warning(win, "Something went wrong",
                            f"{value}\n\nDetails are in the log (the ≡ button in the top bar).")

    sys.excepthook = hook


def _viewer_selftest(path: str) -> None:
    """MODELSHELF_VIEWER_TEST=<model file>: open the 3D view on screen, write
    the outcome to viewer-test.txt next to the log and exit. For checking the
    OpenGL path inside the frozen .exe."""
    from core.settings import data_dir
    from ui.viewer3d import ModelViewer
    out = data_dir() / "viewer-test.txt"
    lines: list[str] = []
    v = ModelViewer()
    v.status.connect(lines.append)
    v.resize(360, 300)
    v.show()

    def done(ok):
        info = ""
        if v.ctx is not None:
            info = f"{v.ctx.info['GL_VERSION']} | {v.ctx.info['GL_RENDERER']}"
        lines.insert(0, f"ready={ok} gl_failed={v.gl_failed} context={info or 'none'}")
        out.write_text("\n".join(lines), encoding="utf-8")
        QApplication.quit()

    v.ready.connect(done)
    QTimer.singleShot(200, lambda: v.show_path(path, None, True, "#C8CDD6"))
    QTimer.singleShot(15000, lambda: done("timeout"))
    run._selftest_viewer = v          # keep a reference while the loop runs


def _launch_selftest() -> None:
    """MODELSHELF_LAUNCH_TEST=1: report which library folder a child process
    inherits, started the plain way and through core.system. Writes
    launch-test.txt next to the log. For checking the packaged Windows build."""
    from core.settings import data_dir
    from core.system import child_env, external_launch
    ps = ("Add-Type -Namespace W -Name K -MemberDefinition '[DllImport(\"kernel32.dll\", CharSet=CharSet.Unicode)] "
          "public static extern int GetDllDirectoryW(int n, System.Text.StringBuilder b);'; "
          "$b = New-Object System.Text.StringBuilder 1024; [void][W.K]::GetDllDirectoryW(1024, $b); "
          "'[' + $b.ToString() + ']'")
    cmd = ["powershell", "-NoProfile", "-Command", ps]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    plain = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags).stdout.strip()
    with external_launch():
        fixed = subprocess.run(cmd, capture_output=True, text=True, env=child_env(), creationflags=flags).stdout.strip()
    (data_dir() / "launch-test.txt").write_text(
        f"temp folder: {getattr(sys, '_MEIPASS', '(not packaged)')}\n"
        f"child started the plain way inherits: {plain}\n"
        f"child started through core.system inherits: {fixed}\n", encoding="utf-8")


def run():
    from core.system import prepare_qt
    prepare_qt()
    from PySide6.QtGui import QSurfaceFormat
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ModelShelf.App")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("ModelShelf")
    from core.settings import load
    s = load()
    tm = theme.ThemeManager(app, s.theme)
    tm.apply()
    if os.environ.get("MODELSHELF_LAUNCH_TEST"):
        _launch_selftest()
        sys.exit(0)
    if os.environ.get("MODELSHELF_VIEWER_TEST"):
        _viewer_selftest(os.environ["MODELSHELF_VIEWER_TEST"])
        sys.exit(app.exec())
    w = MainWindow(s, tm)
    _install_error_hook(w)
    w._on_theme()
    w.show()
    sys.exit(app.exec())
