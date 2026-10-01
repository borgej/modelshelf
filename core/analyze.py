"""Per-file analysis job, run in a pool of worker processes.

Reads a file once, and from that one read produces everything the library
needs: content hash, geometry figures, colours, metadata and a thumbnail PNG.
Files are only ever opened for reading — nothing here touches the originals.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import traceback

from PIL import Image

from core import formats
from core.mesh import hex_to_rgb

# Bump when analysis output changes so existing libraries get re-analysed.
ANALYZER_VERSION = 6
THUMB_SIZE = 384
HASH_CHUNK = 4 * 1024 * 1024
# Loose files bigger than this are hashed but not parsed (they'd need GBs of RAM).
MAX_PARSE_BYTES = 600 * 1024 * 1024
MAX_GCODE_TOOLPATH_BYTES = 250 * 1024 * 1024


def _init_worker(low_priority: bool) -> None:
    if low_priority:
        try:
            import psutil
            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _image_png(data: bytes, size: int) -> bytes | None:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    img = img.convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=False)
    return out.getvalue()


def analyze(path: str, options: dict) -> dict:
    """Returns a plain dict (it crosses a process boundary)."""
    t0 = time.perf_counter()
    ext = os.path.splitext(path)[1].lower()
    out: dict = {"path": path, "error": "", "thumb_png": None, "thumb_source": ""}
    try:
        st = os.stat(path)
        out["size"], out["mtime"] = st.st_size, st.st_mtime

        if ext in formats.CAD_EXTS:
            out["sha256"] = _hash_file(path)
            return out

        if ext in formats.ARCHIVE_EXTS and not formats.zip_has_models(path):
            # Software, drivers, mod packs… Only the archive's table of contents
            # (at the end of the file) was read; no need to hash hundreds of MB.
            out["not_model"] = True
            out["note"] = "no 3D model files inside"
            return out

        if ext in formats.ARCHIVE_EXTS:
            # Hash in chunks and pull models out one at a time — never the whole
            # archive in memory (downloads folders hold multi-GB zips).
            out["sha256"] = _hash_file(path)
            res = formats.read_zip(path)
        elif ext in formats.GCODE_EXTS:
            out["sha256"] = _hash_file(path)
            with open(path, "rb") as fh:
                head = fh.read(4_000_000)
                fh.seek(max(0, st.st_size - 300_000))
                tail = fh.read()
            res = formats.read_gcode(head, tail)
            if res.embedded_thumb is None and st.st_size <= MAX_GCODE_TOOLPATH_BYTES:
                with open(path, "rb") as fh:
                    res.mesh = formats.gcode_toolpath(fh.read(), res.colors)
                res.note = "toolpath preview"
        elif st.st_size > MAX_PARSE_BYTES:
            out["sha256"] = _hash_file(path)
            out["error"] = "file too large to preview"
            return out
        else:
            with open(path, "rb") as fh:
                data = fh.read()
            out["sha256"] = hashlib.sha256(data).hexdigest()
            res = formats.read_bytes(data, ext)
            del data

        out["colors"] = res.colors
        out["designer"] = res.designer
        out["title"] = res.title
        out["photos"] = res.photos
        out["sliced_weight"] = res.sliced_weight
        out["print_time"] = res.print_time
        out["note"] = res.note

        mesh = res.mesh
        if mesh is not None and mesh.triangle_count:
            sx, sy, sz = res.size_override or mesh.size()
            out.update(dim_x=sx, dim_y=sy, dim_z=sz, parts=mesh.parts)
            if ext not in formats.GCODE_EXTS:
                # A toolpath is drawn as open ribbons, not a closed solid — its
                # "volume" means nothing. G-code weight comes from the slicer.
                out.update(volume=mesh.volume(), area=mesh.area(), triangles=mesh.triangle_count)

        prefer_embedded = options.get("prefer_embedded") and res.embedded_thumb
        if mesh is not None and mesh.triangle_count and not prefer_embedded:
            try:
                from core.render import renderer
                default = hex_to_rgb(options.get("default_color", "#C8CDD6")) or (0.8, 0.8, 0.85)
                img = renderer().render(mesh, THUMB_SIZE, default_rgb=default)
                buf = io.BytesIO()
                img.save(buf, "PNG")
                out["thumb_png"], out["thumb_source"] = buf.getvalue(), "render"
            except Exception as exc:  # GPU trouble: fall back to the embedded picture
                out["error"] = f"render failed: {exc}"
        if out["thumb_png"] is None and res.embedded_thumb:
            png = _image_png(res.embedded_thumb, THUMB_SIZE)
            if png:
                out["thumb_png"], out["thumb_source"] = png, "embedded"
    except MemoryError:
        out["error"] = "out of memory while reading"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["trace"] = traceback.format_exc(limit=4)
    out["seconds"] = time.perf_counter() - t0
    return out
