"""Library scan: find files, work out what changed, analyse the changes.

Framework-free: the UI runs `Scanner.run()` on a background thread and receives
progress through the callbacks. Analysis happens in a process pool (parsing
and hashing are CPU-bound, and each process renders on its own GL context).

Incremental: a file whose size and modification time match the catalogue —
and was analysed by the current analyser version — is not opened at all, so a
rescan of an unchanged library takes about as long as listing the folders.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable

from core import formats
from core.analyze import ANALYZER_VERSION, _init_worker, analyze
from core.db import Library
from core.settings import Settings, thumbs_dir


@dataclass
class ScanStats:
    found: int = 0
    analyzed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    cancelled: bool = False
    seconds: float = 0.0


def wanted_exts(s: Settings) -> set[str]:
    exts = set(formats.MESH_EXTS)
    if s.include_gcode:
        exts |= formats.GCODE_EXTS
    if s.include_zip:
        exts |= formats.ARCHIVE_EXTS
    if s.include_cad:
        exts |= formats.CAD_EXTS
    return exts


def _under(path: str, root: str) -> bool:
    root = os.path.normcase(os.path.normpath(root)).rstrip("\\/")
    return os.path.normcase(os.path.normpath(path)).startswith(root + os.sep)


def effective_roots(roots: list[str]) -> list[str]:
    """Normalised library folders with nested ones dropped (a folder inside
    another library folder would otherwise be scanned — and listed — twice)."""
    norm = []
    for r in roots:
        n = os.path.normpath(r)
        if not any(os.path.normcase(n) == os.path.normcase(x) for x in norm):
            norm.append(n)
    return [r for r in norm if not any(o != r and _under(r, o) for o in norm)]


def thumb_name(path: str, mtime: float) -> str:
    return hashlib.sha1(f"{path}|{mtime}|{ANALYZER_VERSION}".encode("utf-8", "replace")).hexdigest()[:20] + ".png"


class Scanner:
    def __init__(self, lib: Library, settings: Settings, *,
                 progress: Callable[[str, int, int, str], None],
                 item_changed: Callable[[int], None],
                 items_removed: Callable[[list[int]], None],
                 log: Callable[[str], None]):
        self.lib = lib
        self.s = settings
        self.progress = progress
        self.item_changed = item_changed
        self.items_removed = items_removed
        self.log = log
        self.cancel_event = threading.Event()
        self._readme_cache: dict[str, str] = {}

    def cancel(self) -> None:
        self.cancel_event.set()

    # ------------------------------------------------------------------ discovery
    def _discover(self, roots: list[str]) -> list[tuple[str, str]]:
        exts = wanted_exts(self.s)
        excl = {e.lower() for e in self.s.exclude}
        found: list[tuple[str, str]] = []
        self.scanned_roots: list[str] = []
        last = 0.0
        for root in roots:
            if not os.path.isdir(root):
                self.log(f"Library folder not available, skipped (its models are kept): {root}")
                continue
            self.scanned_roots.append(root)
            stack = [root]
            while stack:
                if self.cancel_event.is_set():
                    return found
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    if e.name.lower() not in excl and not e.name.startswith("."):
                                        stack.append(e.path)
                                elif os.path.splitext(e.name)[1].lower() in exts:
                                    found.append((os.path.normpath(e.path), root))
                            except OSError:
                                continue
                except OSError as exc:
                    self.log(f"Cannot read folder {d}: {exc}")
                now = time.monotonic()
                if now - last > 0.1:
                    last = now
                    self.progress("discover", len(found), 0, d)
        return found

    # ------------------------------------------------------------------ sidecar info
    def _readme_designer(self, folder: str) -> str:
        if folder in self._readme_cache:
            return self._readme_cache[folder]
        designer = ""
        try:
            for name in os.listdir(folder):
                if name.lower() in ("readme.txt", "readme.md", "attribution_card.html"):
                    with open(os.path.join(folder, name), "rb") as fh:
                        designer = formats.designer_from_readme(fh.read(200_000).decode("utf-8", "replace"))
                    if designer:
                        break
        except OSError:
            pass
        self._readme_cache[folder] = designer
        return designer

    def _sidecars(self, rec: dict) -> None:
        """Designer from a README and photos from an images/ folder next to the model
        (the layout Thingiverse and Printables downloads unpack to)."""
        folder = os.path.dirname(rec["path"])
        parent = os.path.dirname(folder)
        if not rec.get("designer"):
            rec["designer"] = self._readme_designer(folder) or (
                self._readme_designer(parent) if os.path.basename(folder).lower() == "files" else "")
        if not rec.get("photos") and rec["path"].lower().endswith((".stl", ".obj", ".3mf")):
            candidates = [os.path.join(folder, "images")]
            if os.path.basename(folder).lower() == "files":
                candidates.append(os.path.join(parent, "images"))
            for c in candidates:
                try:
                    pics = [os.path.join(c, n) for n in sorted(os.listdir(c))
                            if os.path.splitext(n)[1].lower() in formats.IMAGE_EXTS]
                except OSError:
                    continue
                if pics:
                    rec["photos"] = pics[:40]
                    break

    # ------------------------------------------------------------------ main
    def run(self, only_paths: list[str] | None = None) -> ScanStats:
        """Scan all library folders, or re-analyse just `only_paths`."""
        t0 = time.monotonic()
        stats = ScanStats()
        roots = effective_roots(self.s.roots)
        known = self.lib.fingerprints()

        if only_paths is not None:
            todo = [(p, next((r for r in roots if p.startswith(r)), os.path.dirname(p)))
                    for p in only_paths if os.path.exists(p)]
            removed_paths: list[str] = []
        else:
            self.progress("discover", 0, 0, "")
            files = self._discover(roots)
            if self.cancel_event.is_set():
                stats.cancelled = True
                return stats
            stats.found = len(files)
            present = {p for p, _ in files}
            todo = []
            for p, root in files:
                k = known.get(p)
                if k is not None and k[2] == ANALYZER_VERSION:
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if k[0] == st.st_size and abs((k[1] or 0) - st.st_mtime) < 1e-3:
                        stats.unchanged += 1
                        continue
                todo.append((p, root))
            # Only forget files we could actually check. A folder that is offline
            # (unplugged drive, sleeping NAS) must not wipe its models and tags;
            # files outside every configured folder were removed on purpose.
            def gone(p: str) -> bool:
                if p in present:
                    return False
                if any(_under(p, r) for r in self.scanned_roots):
                    return True
                return not any(_under(p, r) for r in roots)
            removed_paths = [p for p in known if gone(p)]
            self.log(f"Found {stats.found} files: {len(todo)} to analyse, "
                     f"{stats.unchanged} unchanged, {len(removed_paths)} gone")

        carry = self.lib.user_data_for(removed_paths)
        carry_by_sha = {d["sha256"]: d for d in carry.values() if d.get("sha256")}

        # Biggest first: keeps all workers busy at the end instead of one straggler.
        def _size(p):
            try:
                return os.path.getsize(p)
            except OSError:
                return 0
        todo.sort(key=lambda pr: -_size(pr[0]))

        options = {"prefer_embedded": self.s.prefer_embedded, "default_color": self.s.default_color}
        total = len(todo)
        tdir = thumbs_dir()
        if total:
            workers = min(self.s.worker_count(), total)
            self.progress("analyze", 0, total, "")
            # Always "spawn": forking a process that already runs Qt and holds
            # OpenGL contexts (the Linux default) is not safe.
            import multiprocessing
            with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                     initargs=(self.s.low_priority,),
                                     mp_context=multiprocessing.get_context("spawn")) as pool:
                pending = {}
                queue = list(todo)
                done_count = 0
                last = 0.0
                while queue or pending:
                    while queue and len(pending) < workers * 2 and not self.cancel_event.is_set():
                        p, root = queue.pop(0)
                        pending[pool.submit(analyze, p, options)] = (p, root)
                    if not pending:
                        break
                    finished, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                    for fut in finished:
                        p, root = pending.pop(fut)
                        done_count += 1
                        try:
                            rec = fut.result()
                        except Exception as exc:     # worker crashed (e.g. out of memory)
                            rec = {"path": p, "error": f"worker failed: {exc}"}
                            try:
                                st = os.stat(p)
                                rec.update(size=st.st_size, mtime=st.st_mtime)
                            except OSError:
                                continue
                        self._store(rec, root, known, tdir, carry_by_sha, stats)
                        now = time.monotonic()
                        if now - last > 0.08 or done_count == total:
                            last = now
                            self.progress("analyze", done_count, total, os.path.basename(p))
                    if self.cancel_event.is_set():
                        for f in pending:
                            f.cancel()
                        queue.clear()
                        if not any(not f.done() for f in pending):
                            break
                        # Let running jobs finish so their work isn't wasted.
                        wait(pending, timeout=5)
                        for fut, (p, root) in list(pending.items()):
                            if fut.done() and not fut.cancelled():
                                try:
                                    self._store(fut.result(), root, known, tdir, carry_by_sha, stats)
                                except Exception:
                                    pass
                        pending.clear()
                        break

        if self.cancel_event.is_set():
            stats.cancelled = True
        elif removed_paths:
            gone = self.lib.remove_paths(removed_paths)
            for _, thumb in gone:
                if thumb:
                    try:
                        (tdir / thumb).unlink()
                    except OSError:
                        pass
            stats.removed = len(gone)
            self.items_removed([i for i, _ in gone])
        stats.seconds = time.monotonic() - t0
        return stats

    def _store(self, rec: dict, root: str, known, tdir, carry_by_sha, stats: ScanStats) -> None:
        p = rec["path"]
        if rec.get("size") is None:
            return
        if rec.get("error"):
            self.log(f"{os.path.basename(p)}: {rec['error']}")
            if rec.get("trace"):
                self.log(rec["trace"].rstrip())
        self._sidecars(rec)
        old = self.lib.by_path(p)
        thumb = ""
        if rec.get("thumb_png"):
            thumb = thumb_name(p, rec["mtime"])
            try:
                (tdir / thumb).write_bytes(rec["thumb_png"])
            except OSError as exc:
                self.log(f"Cannot write thumbnail: {exc}")
                thumb = ""
        if old and old.thumb and old.thumb != thumb:
            try:
                (tdir / old.thumb).unlink()
            except OSError:
                pass
        item_id = self.lib.upsert_analysis(rec, root, ANALYZER_VERSION, thumb)
        if old is None and rec.get("sha256") in carry_by_sha:
            self.lib.restore_user_data(item_id, carry_by_sha.pop(rec["sha256"]))
            self.log(f"Kept tags/status for moved file: {os.path.basename(p)}")
        if rec.get("error") and not rec.get("thumb_png"):
            stats.failed += 1
        stats.analyzed += 1
        self.item_changed(item_id)
