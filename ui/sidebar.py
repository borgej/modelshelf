"""Left sidebar: browse by folder, designer or tag, each with live counts."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QListWidget, QListWidgetItem,
                               QPushButton, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from core.db import Item
from ui import icons, theme

PathRole = Qt.UserRole + 10


class Sidebar(QFrame):
    folder_selected = Signal(str)          # "" = all
    designer_selected = Signal(object)     # None = all
    tag_selected = Signal(object)          # None = all

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "panel")
        self.setMinimumWidth(220)
        self.setMaximumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 10)
        lay.setSpacing(6)

        tabs = QHBoxLayout()
        tabs.setSpacing(14)
        self.group = QButtonGroup(self)
        self.stack = QStackedWidget()
        for i, name in enumerate(("FOLDERS", "DESIGNERS", "TAGS")):
            b = QPushButton(name)
            b.setProperty("variant", "seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, i)
            tabs.addWidget(b)
        tabs.addStretch()
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        lay.addLayout(tabs)
        lay.addWidget(self.stack, 1)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self._tree_sel)
        self.stack.addWidget(self.tree)

        self.designers = QListWidget()
        self.designers.itemSelectionChanged.connect(
            lambda: self._list_sel(self.designers, self.designer_selected))
        self.stack.addWidget(self.designers)

        self.tags = QListWidget()
        self.tags.itemSelectionChanged.connect(lambda: self._list_sel(self.tags, self.tag_selected))
        self.stack.addWidget(self.tags)
        foot = QFrame()
        foot.setProperty("role", "sidefooter")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(4, 9, 4, 1)
        from PySide6.QtWidgets import QLabel
        self.footer_icon = QLabel()
        self.footer = QLabel("")
        self.footer.setProperty("role", "footer")
        fl.addWidget(self.footer_icon)
        fl.addWidget(self.footer, 1)
        lay.addWidget(foot)
        self._expanded: set[str] = set()
        self._built = False

    def set_footer(self, text: str) -> None:
        self.footer_icon.setPixmap(icons.pixmap("folder", 14, theme.C["muted"]))
        self.footer.setText(text)

    # ------------------------------------------------------------------ build
    def rebuild(self, items: list[Item], roots: list[str]) -> None:
        sel_folder = self.current_folder()
        if self._built:
            self._expanded = {i.data(0, PathRole) for i in self._iter_tree() if i.isExpanded()}
        self.tree.blockSignals(True)
        self.tree.clear()

        counts: dict[str, int] = {}
        for it in items:
            d = it.folder
            while True:
                counts[d] = counts.get(d, 0) + 1
                parent = os.path.dirname(d)
                if d in roots or parent == d or not any(d.startswith(r) for r in roots):
                    break
                d = parent

        all_item = QTreeWidgetItem(["All models", str(len(items))])
        all_item.setData(0, PathRole, "")
        all_item.setIcon(0, icons.icon("cube", 14))
        self.tree.addTopLevelItem(all_item)
        norm_roots = [os.path.normpath(r) for r in roots]
        nodes: dict[str, QTreeWidgetItem] = {}
        for root in norm_roots:
            node = QTreeWidgetItem([os.path.basename(root) or root, str(counts.get(root, 0))])
            node.setData(0, PathRole, root)
            node.setToolTip(0, root)
            node.setIcon(0, icons.icon("folder", 14))
            self.tree.addTopLevelItem(node)
            nodes[root] = node
        for d in sorted(counts, key=lambda x: x.lower()):
            if d in nodes:
                continue
            parent = os.path.dirname(d)
            pnode = nodes.get(parent)
            if pnode is None:
                continue
            node = QTreeWidgetItem([os.path.basename(d), str(counts[d])])
            node.setData(0, PathRole, d)
            node.setToolTip(0, d)
            pnode.addChild(node)
            nodes[d] = node
        for i in range(self.tree.topLevelItemCount()):
            self._style_counts(self.tree.topLevelItem(i))
        for path, node in nodes.items():
            if path in self._expanded or (not self._built and path in norm_roots):
                node.setExpanded(True)
        self.tree.resizeColumnToContents(1)
        self.tree.header().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        target = next((n for n in self._iter_tree() if n.data(0, PathRole) == sel_folder), all_item)
        target.setSelected(True)
        self.tree.setCurrentItem(target)
        self.tree.blockSignals(False)
        self._built = True

        dcount: dict[str, int] = {}
        tcount: dict[str, int] = {}
        for it in items:
            if it.designer:
                dcount[it.designer] = dcount.get(it.designer, 0) + 1
            for t in it.tags:
                tcount[t] = tcount.get(t, 0) + 1
        self._fill_list(self.designers, dcount, "All designers", "user",
                        empty="No designers found yet.\nThey're read from 3MF metadata\nand README files.")
        self._fill_list(self.tags, tcount, "All tags", "tag",
                        empty="No tags yet.\nAdd tags in the details panel.")

    def _style_counts(self, node: QTreeWidgetItem):
        node.setForeground(1, theme.qc("muted"))
        node.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        for i in range(node.childCount()):
            self._style_counts(node.child(i))

    def _fill_list(self, lw: QListWidget, counts: dict, all_label: str, icon: str, empty: str):
        prev = lw.currentItem().data(PathRole) if lw.currentItem() else None
        lw.blockSignals(True)
        lw.clear()
        if not counts:
            it = QListWidgetItem(empty)
            it.setFlags(Qt.NoItemFlags)
            lw.addItem(it)
            lw.blockSignals(False)
            return
        first = QListWidgetItem(f"{all_label}")
        first.setData(PathRole, None)
        lw.addItem(first)
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())):
            it = QListWidgetItem(icons.icon(icon, 14), f"{name}   ·  {n}")
            it.setData(PathRole, name)
            lw.addItem(it)
            if name == prev:
                it.setSelected(True)
                lw.setCurrentItem(it)
        lw.blockSignals(False)

    def _iter_tree(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(n.child(i) for i in range(n.childCount()))

    # ------------------------------------------------------------------ selection
    def current_folder(self) -> str:
        it = self.tree.currentItem()
        return it.data(0, PathRole) if it else ""

    def _tree_sel(self):
        self.folder_selected.emit(self.current_folder() or "")

    def _list_sel(self, lw: QListWidget, sig):
        it = lw.currentItem()
        sig.emit(it.data(PathRole) if it and it.flags() & Qt.ItemIsEnabled else None)

    def select_tag(self, tag: str):
        self.group.button(2).click()
        for i in range(self.tags.count()):
            if self.tags.item(i).data(PathRole) == tag:
                self.tags.setCurrentRow(i)
                return

    def reset(self):
        for lw in (self.designers, self.tags):
            lw.blockSignals(True)
            lw.setCurrentRow(0)
            lw.blockSignals(False)
