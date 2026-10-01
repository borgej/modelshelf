"""List model for the grid, with filtering/sorting done in plain Python.

A QSortFilterProxyModel would call back into Python for every comparison and
every row test; sorting a list of dataclasses is far cheaper and keeps the
filter logic in one readable place (`Filter.accepts`).
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass, field

from PySide6.QtCore import (QAbstractListModel, QMimeData, QModelIndex, QObject, QRunnable, Qt,
                            QThreadPool, QUrl, Signal)
from PySide6.QtGui import QImage, QPixmap

from core.db import Item
from core.settings import thumbs_dir

ItemRole = Qt.UserRole + 1

SORTS = {
    "name": ("Name (A–Z)", lambda i: (i.name.lower(),), False),
    "newest": ("Newest file", lambda i: (i.mtime or 0,), True),
    "added": ("Recently added", lambda i: (i.added_at or 0,), True),
    "size": ("File size", lambda i: (i.size or 0,), True),
    "model": ("Model size", lambda i: ((i.dim_x or 0) * (i.dim_y or 0) * (i.dim_z or 0),), True),
    "folder": ("Folder", lambda i: (i.folder.lower(), i.name.lower()), False),
    "designer": ("Designer", lambda i: (not i.designer, (i.designer or "").lower(), i.name.lower()), False),
}


@dataclass
class Filter:
    text: str = ""
    kinds: set[str] = field(default_factory=set)
    status: str = ""            # "" | favorite | to_print | printed
    folder: str = ""            # path prefix
    designer: str | None = None
    tag: str | None = None

    def accepts(self, it: Item, tokens: list[str]) -> bool:
        if self.kinds and it.kind not in self.kinds:
            return False
        if self.status == "favorite" and not it.favorite:
            return False
        if self.status in ("to_print", "printed") and it.status != self.status:
            return False
        if self.folder and not (it.path.startswith(self.folder + os.sep)):
            return False
        if self.designer is not None and (it.designer or "") != self.designer:
            return False
        if self.tag is not None and self.tag not in it.tags:
            return False
        for t in tokens:
            if t.startswith("-"):
                if t[1:] and t[1:] in it.search_blob:
                    return False
            elif t not in it.search_blob:
                return False
        return True


class LibraryModel(QAbstractListModel):
    visibleChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: dict[int, Item] = {}
        self.visible: list[Item] = []
        self._row: dict[int, int] = {}
        self.filter = Filter()
        self.sort_key = "name"

    # ---- Qt API ----
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self.visible[index.row()]
        if role == ItemRole:
            return it
        if role == Qt.DisplayRole:
            return it.name
        if role == Qt.ToolTipRole:
            return it.path
        return None

    def flags(self, index):
        base = super().flags(index)
        return base | Qt.ItemIsDragEnabled if index.isValid() else base

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(self.visible[i.row()].path) for i in indexes if i.isValid()])
        return md

    def supportedDragActions(self):
        return Qt.CopyAction

    # ---- data management ----
    def set_items(self, items: list[Item]) -> None:
        self.items = {i.id: i for i in items}
        self.refilter()

    def upsert(self, it: Item) -> bool:
        """Replace/insert one item. Returns True if the visible set needs a refilter."""
        self.items[it.id] = it
        row = self._row.get(it.id)
        if row is not None:
            self.visible[row] = it
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)
            return not self.filter.accepts(it, self._tokens())
        return self.filter.accepts(it, self._tokens())

    def remove_ids(self, ids) -> None:
        changed = False
        for i in ids:
            if self.items.pop(i, None) is not None:
                changed = True
        if changed:
            self.refilter()

    def _tokens(self) -> list[str]:
        return [t for t in self.filter.text.lower().split() if t]

    def refilter(self) -> None:
        tokens = self._tokens()
        _, key, rev = SORTS.get(self.sort_key, SORTS["name"])
        vis = [i for i in self.items.values() if self.filter.accepts(i, tokens)]
        vis.sort(key=key, reverse=rev)
        self.beginResetModel()
        self.visible = vis
        self._row = {it.id: r for r, it in enumerate(vis)}
        self.endResetModel()
        self.visibleChanged.emit()

    def row_of(self, item_id: int) -> int | None:
        return self._row.get(item_id)

    def refresh_row(self, item_id: int) -> None:
        r = self._row.get(item_id)
        if r is not None:
            idx = self.index(r)
            self.dataChanged.emit(idx, idx)


# --------------------------------------------------------------------------- thumbnails

class _Bridge(QObject):
    loaded = Signal(str, QImage)


class _LoadJob(QRunnable):
    def __init__(self, name: str, bridge: _Bridge):
        super().__init__()
        self.name, self.bridge = name, bridge

    def run(self):
        img = QImage(str(thumbs_dir() / self.name))
        self.bridge.loaded.emit(self.name, img)


class ThumbCache(QObject):
    """Loads thumbnail PNGs off the UI thread; keeps an LRU of decoded pixmaps."""

    ready = Signal(str)

    def __init__(self, capacity: int = 900, parent=None):
        super().__init__(parent)
        self.capacity = capacity
        self._pix: OrderedDict[str, QPixmap] = OrderedDict()
        self._pending: set[str] = set()
        self._missing: set[str] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._bridge = _Bridge()
        self._bridge.loaded.connect(self._on_loaded)

    def get(self, name: str) -> QPixmap | None:
        if not name or name in self._missing:
            return None
        px = self._pix.get(name)
        if px is not None:
            self._pix.move_to_end(name)
            return px
        if name not in self._pending:
            self._pending.add(name)
            self._pool.start(_LoadJob(name, self._bridge))
        return None

    def forget(self, name: str) -> None:
        self._pix.pop(name, None)
        self._missing.discard(name)

    def clear(self) -> None:
        self._pix.clear()
        self._missing.clear()

    def _on_loaded(self, name: str, img: QImage) -> None:
        self._pending.discard(name)
        if img.isNull():
            self._missing.add(name)
            return
        self._pix[name] = QPixmap.fromImage(img)
        while len(self._pix) > self.capacity:
            self._pix.popitem(last=False)
        self.ready.emit(name)
