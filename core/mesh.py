"""Triangle meshes as flat numpy arrays, plus geometry helpers.

A mesh is kept "unindexed": `tris` has shape (N, 3, 3) — N triangles, three
corners, xyz. That costs memory compared to an indexed mesh, but every consumer
(bounding box, volume, flat-shaded rendering) wants per-triangle data anyway,
and it lets the loaders skip building vertex tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Mesh:
    tris: np.ndarray                      # (N, 3, 3) float32
    # Optional per-triangle colour, (N, 3) float32 in 0..1. None = use default.
    colors: np.ndarray | None = None
    parts: int = 1

    @property
    def triangle_count(self) -> int:
        return int(self.tris.shape[0])

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        flat = self.tris.reshape(-1, 3)
        return flat.min(axis=0), flat.max(axis=0)

    def size(self) -> tuple[float, float, float]:
        lo, hi = self.bounds()
        d = hi - lo
        return float(d[0]), float(d[1]), float(d[2])

    def volume(self) -> float:
        """Enclosed volume in mm³ (signed tetrahedra; abs for inverted meshes)."""
        t = self.tris.astype(np.float64)
        v = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0
        return float(abs(v))

    def area(self) -> float:
        t = self.tris.astype(np.float64)
        c = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        return float(np.linalg.norm(c, axis=1).sum() / 2.0)


def merge(meshes: list[Mesh], default_rgb: tuple[float, float, float] | None = None) -> Mesh:
    meshes = [m for m in meshes if m is not None and m.triangle_count]
    if not meshes:
        return Mesh(np.zeros((0, 3, 3), np.float32))
    if len(meshes) == 1:
        return meshes[0]
    tris = np.concatenate([m.tris for m in meshes])
    colors = None
    if any(m.colors is not None for m in meshes):
        fill = np.array(default_rgb or (0.78, 0.8, 0.84), np.float32)
        colors = np.concatenate([
            m.colors if m.colors is not None else np.tile(fill, (m.triangle_count, 1))
            for m in meshes
        ])
    return Mesh(tris, colors, parts=sum(m.parts for m in meshes))


def arrange(meshes: list[Mesh], gap: float = 6.0) -> list[Mesh]:
    """Lay separate models out on a grid (for zips holding several files)."""
    meshes = [m for m in meshes if m is not None and m.triangle_count]
    if len(meshes) < 2:
        return meshes
    cols = int(np.ceil(np.sqrt(len(meshes))))
    out, x, y, row_depth = [], 0.0, 0.0, 0.0
    for i, m in enumerate(meshes):
        if i and i % cols == 0:
            x, y, row_depth = 0.0, y + row_depth + gap, 0.0
        lo, hi = m.bounds()
        shift = np.array([x - lo[0], y - lo[1], -lo[2]], np.float32)
        out.append(Mesh(m.tris + shift, m.colors, m.parts))
        x += float(hi[0] - lo[0]) + gap
        row_depth = max(row_depth, float(hi[1] - lo[1]))
    return out


def fmt_date(ts: float | None, with_time: bool = False) -> str:
    """Norwegian date format: 16.05.2026 (and 16.05.2026 14:03)."""
    if not ts:
        return ""
    import datetime as dt
    d = dt.datetime.fromtimestamp(ts)
    return d.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    v = value.strip().lstrip("#")
    if len(v) not in (6, 8):
        return None
    try:
        return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def rgb_to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb[:3])
