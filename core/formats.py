"""Readers for the model formats ModelShelf understands.

Everything here is pure data-in/data-out and runs inside worker processes, so
nothing may import Qt. Every reader takes raw bytes (so the same code reads a
loose file or a member of a zip archive) and returns a `LoadResult`.

Big files dominate the cost, so parsing avoids per-element Python work where
it can: binary STL is a numpy view, and 3MF/ASCII STL numbers are pulled out
with one regex pass and converted by numpy in bulk.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from dataclasses import dataclass, field

import numpy as np

from core.mesh import Mesh, arrange, hex_to_rgb, merge

MESH_EXTS = {".stl", ".3mf", ".obj"}
GCODE_EXTS = {".gcode", ".gco", ".g"}
ARCHIVE_EXTS = {".zip"}
# Listed in the library, but ModelShelf cannot draw them (would need a CAD kernel).
CAD_EXTS = {".step", ".stp", ".f3d", ".scad", ".blend", ".fcstd", ".iges", ".igs", ".skp"}
ALL_EXTS = MESH_EXTS | GCODE_EXTS | ARCHIVE_EXTS | CAD_EXTS
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

KIND_BY_EXT = {
    **{e: e[1:].upper() for e in MESH_EXTS},
    **{e: "GCODE" for e in GCODE_EXTS},
    ".zip": "ZIP",
    **{e: "CAD" for e in CAD_EXTS},
}

# Safety valve: a single model file larger than this inside a zip is skipped
# rather than decompressed into memory.
MAX_ZIP_MEMBER = 400 * 1024 * 1024


@dataclass
class LoadResult:
    mesh: Mesh | None = None
    colors: list[str] = field(default_factory=list)       # filament / material colours
    designer: str = ""
    title: str = ""
    embedded_thumb: bytes | None = None                    # PNG/JPG from the slicer
    photos: list[str] = field(default_factory=list)        # member names inside the archive
    sliced_weight: float | None = None                     # grams, from slicer output
    print_time: float | None = None                        # seconds, from slicer output
    note: str = ""
    # Set when the mesh is a packed arrangement (multi-plate project): the
    # arrangement's outline is not a size anyone can print, the biggest part is.
    size_override: tuple[float, float, float] | None = None


# --------------------------------------------------------------------------- STL

_STL_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")
_STL_DTYPE = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def read_stl(data: bytes) -> LoadResult:
    tris = None
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], "little")
        if 84 + 50 * n == len(data) or (not data[:5].lower() == b"solid" and 84 + 50 * n <= len(data)):
            tris = np.frombuffer(data, _STL_DTYPE, count=n, offset=84)["v"].astype(np.float32)
    if tris is None:
        nums = _STL_VERTEX.findall(data)
        if not nums:
            raise ValueError("no triangles found in STL")
        tris = np.array(nums).astype(np.float32).reshape(-1, 3, 3)
    return LoadResult(mesh=Mesh(_finite(tris)))


def _finite(tris: np.ndarray) -> np.ndarray:
    ok = np.isfinite(tris).all(axis=(1, 2))
    return tris if ok.all() else tris[ok]


# --------------------------------------------------------------------------- OBJ

def read_obj(data: bytes) -> LoadResult:
    verts: list[bytes] = []
    faces: list[list[int]] = []
    for line in data.splitlines():
        if line.startswith(b"v "):
            verts.append(line[2:])
        elif line.startswith(b"f "):
            idx = []
            for tok in line[2:].split():
                i = int(tok.split(b"/", 1)[0])
                idx.append(i - 1 if i > 0 else len(verts) + i)
            for k in range(1, len(idx) - 1):          # fan-triangulate polygons
                faces.append([idx[0], idx[k], idx[k + 1]])
    if not faces:
        raise ValueError("no faces found in OBJ")
    v = np.array([row.split()[:3] for row in verts]).astype(np.float32)
    f = np.array(faces, np.int64)
    f = f[(f >= 0).all(axis=1) & (f < len(v)).all(axis=1)]
    return LoadResult(mesh=Mesh(v[f]))


# --------------------------------------------------------------------------- 3MF

_OBJECT = re.compile(rb"<(?:\w+:)?object\b([^>]*)>(.*?)</(?:\w+:)?object>", re.S)
_ATTR = re.compile(rb'([\w:]+)\s*=\s*"([^"]*)"')
_VERTEX_FAST = re.compile(rb'<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"')
_VERTEX_TAG = re.compile(rb"<vertex\b([^>]*?)/?>")
_TRI_FAST = re.compile(rb'<triangle\s+v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"\s*/>')
_TRI_TAG = re.compile(rb"<triangle\b([^>]*?)/?>")
_COMPONENT = re.compile(rb"<(?:\w+:)?component\b([^>]*?)/?>")
_ITEM = re.compile(rb"<(?:\w+:)?item\b([^>]*?)/?>")
_META = re.compile(rb'<metadata\s+name="([^"]+)"[^>]*>(.*?)</metadata>', re.S)
_BASEMAT = re.compile(rb'<(?:\w+:)?basematerials\b([^>]*)>(.*?)</(?:\w+:)?basematerials>', re.S)
_COLORGROUP = re.compile(rb'<(?:\w+:)?colorgroup\b([^>]*)>(.*?)</(?:\w+:)?colorgroup>', re.S)
_BASE = re.compile(rb'<(?:\w+:)?base\b[^>]*displaycolor="([^"]+)"')
_COLOR = re.compile(rb'<(?:\w+:)?color\b[^>]*color="([^"]+)"')


def _attrs(blob: bytes) -> dict[str, str]:
    return {k.decode(): v.decode("utf-8", "replace") for k, v in _ATTR.findall(blob)}


def _transform(value: str | None) -> tuple[np.ndarray, np.ndarray] | None:
    if not value:
        return None
    nums = [float(x) for x in value.split()]
    if len(nums) != 12:
        return None
    return np.array(nums[:9], np.float64).reshape(3, 3), np.array(nums[9:], np.float64)


def _decode_paint(code: str) -> int:
    """Dominant filament state from a PrusaSlicer/Bambu 'paint_color' string.

    The string is a bit-packed triangle subdivision tree, read nibble by nibble
    from the END. Split nodes carry their child count in the low two bits; leaf
    nodes carry the state in the upper bits (states >= 3 spill into the next
    nibble). Leaves are counted (not area-weighted) — plenty for a thumbnail.
    """
    nibbles = [int(c, 16) for c in reversed(code) if c in "0123456789abcdefABCDEF"]
    pos = 0
    counts: dict[int, int] = {}

    def node(depth: int) -> None:
        nonlocal pos
        if pos >= len(nibbles) or depth > 12:
            return
        n = nibbles[pos]; pos += 1
        split = n & 3
        if split:
            for _ in range(split + 1):
                node(depth + 1)
        else:
            state = n >> 2
            if state == 3 and pos < len(nibbles):
                state = nibbles[pos] + 3; pos += 1
            counts[state] = counts.get(state, 0) + 1

    node(0)
    painted = {k: v for k, v in counts.items() if k}
    return max(painted, key=painted.get) if painted else 0


class _ModelFile:
    """One parsed *.model part: objects, colour resources, metadata."""

    def __init__(self, data: bytes):
        self.objects: dict[str, dict] = {}
        self.materials: dict[str, list[tuple[float, float, float]]] = {}
        self.meta: dict[str, str] = {}
        for m in _META.finditer(data[:200_000]):
            self.meta[m.group(1).decode()] = _xml_text(m.group(2))
        for regex, inner in ((_BASEMAT, _BASE), (_COLORGROUP, _COLOR)):
            for m in regex.finditer(data):
                gid = _attrs(m.group(1)).get("id")
                cols = [hex_to_rgb(c.decode()) for c in inner.findall(m.group(2))]
                if gid:
                    self.materials[gid] = [c or (0.8, 0.8, 0.8) for c in cols]
        for m in _OBJECT.finditer(data):
            attrs = _attrs(m.group(1))
            body = m.group(2)
            obj: dict = {"attrs": attrs, "components": [], "mesh": None}
            if b"<mesh" in body or b":mesh" in body:
                obj["mesh"] = body
            for c in _COMPONENT.finditer(body):
                obj["components"].append(_attrs(c.group(1)))
            self.objects[attrs.get("id", "")] = obj
        self.items = [_attrs(m.group(1)) for m in _ITEM.finditer(data)]


def _xml_text(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace").strip()
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'"))


def _parse_mesh_body(body: bytes, want_tri_attrs: bool):
    verts = _VERTEX_FAST.findall(body)
    if len(verts) != body.count(b"<vertex"):
        verts = []
        for t in _VERTEX_TAG.finditer(body):
            a = _attrs(t.group(1))
            verts.append((a.get("x", "0"), a.get("y", "0"), a.get("z", "0")))
    v = np.array(verts).astype(np.float64).reshape(-1, 3)
    tri_attrs = None
    if not want_tri_attrs:
        idx = _TRI_FAST.findall(body)
        if len(idx) != body.count(b"<triangle"):
            want_tri_attrs = True
    if want_tri_attrs:
        tri_attrs = [_attrs(t.group(1)) for t in _TRI_TAG.finditer(body)]
        idx = [(a.get("v1", 0), a.get("v2", 0), a.get("v3", 0)) for a in tri_attrs]
    f = np.array(idx).astype(np.int64).reshape(-1, 3)
    good = (f >= 0).all(axis=1) & (f < len(v)).all(axis=1)
    if not good.all():
        f = f[good]
        if tri_attrs is not None:
            tri_attrs = [a for a, g in zip(tri_attrs, good) if g]
    return v, f, tri_attrs


def read_3mf(data: bytes) -> LoadResult:
    res = LoadResult()
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    lower = {n.lower(): n for n in names}

    root_name = _root_model_name(zf, lower)
    files: dict[str, _ModelFile] = {}

    def model_file(path: str | None) -> _ModelFile | None:
        key = (path or root_name).lstrip("/")
        real = lower.get(key.lower())
        if real is None:
            return None
        if real not in files:
            files[real] = _ModelFile(zf.read(real))
        return files[real]

    root = model_file(None)
    if root is None:
        raise ValueError("3MF has no model part")
    res.designer = root.meta.get("Designer", "") or root.meta.get("Author", "")
    title = root.meta.get("Title", "").strip()
    res.title = "" if title.lower() in ("", "(unsaved)", "untitled", "unnamed") else title

    # --- Bambu / Orca: filament colours and per-object extruder assignment ---
    filament: list[tuple[float, float, float]] = []
    settings_name = lower.get("metadata/project_settings.config")
    if settings_name:
        try:
            ps = json.loads(zf.read(settings_name))
            for c in ps.get("filament_colour", []) or []:
                rgb = hex_to_rgb(str(c))
                if rgb:
                    filament.append(rgb)
        except (ValueError, KeyError):
            pass
    obj_extruder, part_extruder = _bambu_extruders(zf, lower)
    _slice_info(zf, lower, res)

    meshes: list[Mesh] = []
    used_colors: list[tuple[float, float, float]] = []

    def filament_rgb(ext: int) -> tuple[float, float, float] | None:
        if filament and ext >= 1:
            return filament[min(ext, len(filament)) - 1]
        return None

    def emit(mf: _ModelFile, obj: dict, xf: list, root_id: str, part_id: str, parent_pid):
        attrs = obj["attrs"]
        v, f, tri_attrs = _parse_mesh_body(
            obj["mesh"],
            want_tri_attrs=(b"paint_color" in obj["mesh"] or b"mmu_segmentation" in obj["mesh"]
                            or b' p1="' in obj["mesh"] or b" pid=" in obj["mesh"]),
        )
        if not len(f):
            return
        for rot, trans in xf:
            v = v @ rot + trans
        tris = v[f].astype(np.float32)

        base_ext = part_extruder.get((root_id, part_id)) or obj_extruder.get(root_id) or 0
        base_rgb = filament_rgb(base_ext)
        pid = attrs.get("pid") or parent_pid
        if base_rgb is None and pid in mf.materials:
            mats = mf.materials[pid]
            pidx = int(attrs.get("pindex", "0") or 0)
            if mats:
                base_rgb = mats[min(pidx, len(mats) - 1)]

        colors = None
        if tri_attrs is not None:
            per = []
            varied = False
            for a in tri_attrs:
                rgb = base_rgb
                paint = a.get("paint_color") or a.get("slic3rpe:mmu_segmentation")
                if paint:
                    st = _decode_paint(paint)
                    if st and filament_rgb(st):
                        rgb = filament_rgb(st); varied = True
                elif "p1" in a:
                    mats = mf.materials.get(a.get("pid") or pid or "")
                    if mats:
                        rgb = mats[min(int(a["p1"] or 0), len(mats) - 1)]; varied = True
                per.append(rgb or (-1.0, -1.0, -1.0))
            if varied:
                colors = np.array(per, np.float32)
                for c in {tuple(round(x, 3) for x in c) for c in colors[:, :3].tolist()}:
                    if c[0] >= 0:
                        used_colors.append(c)
        if colors is None and base_rgb is not None:
            colors = np.tile(np.array(base_rgb, np.float32), (len(tris), 1))
            used_colors.append(base_rgb)
        meshes.append(Mesh(tris, colors))

    def walk(mf: _ModelFile, oid: str, xf: list, root_id: str, part_id: str, depth: int, pid=None):
        obj = mf.objects.get(oid)
        if obj is None or depth > 16:
            return
        if obj["attrs"].get("type") in ("support", "other"):
            return
        if obj["mesh"] is not None:
            emit(mf, obj, xf, root_id, part_id, pid)
        for comp in obj["components"]:
            sub = model_file(comp.get("p:path")) if comp.get("p:path") else mf
            if sub is None:
                continue
            t = _transform(comp.get("transform"))
            walk(sub, comp.get("objectid", ""), ([t] if t else []) + xf, root_id,
                 comp.get("objectid", ""), depth + 1, obj["attrs"].get("pid") or pid)

    items = root.items or [{"objectid": k} for k in root.objects]
    per_item: list[Mesh] = []
    for it in items:
        if it.get("printable") == "0":
            continue
        t = _transform(it.get("transform"))
        oid = it.get("objectid", "")
        start = len(meshes)
        walk(root, oid, [t] if t else [], oid, oid, 0)
        if len(meshes) > start:
            per_item.append(merge(meshes[start:]))

    # Multi-plate projects (Bambu/Orca) spread their plates far apart on a
    # virtual bed; drawn as-is the parts become specks. Pack them instead.
    plates = _plate_count(zf, lower)
    if plates > 1 and len(per_item) > 1:
        packed = arrange(per_item)
        meshes = packed
        res.note = f"{plates} plates · size of largest part"
        sizes = [m.size() for m in per_item]
        res.size_override = max(sizes, key=lambda sz: sz[0] * sz[1] * max(sz[2], 1e-6))

    if meshes:
        m = merge(meshes, default_rgb=None)
        if m.colors is not None:
            # Parts without a resolvable colour were marked -1: give them the first used colour.
            missing = m.colors[:, 0] < 0
            if missing.any():
                if missing.all():
                    m.colors = None
                else:
                    m.colors[missing] = m.colors[~missing][0]
        m.parts = len(meshes)
        res.mesh = m

    # Colour dots: filaments actually used, else declared ones.
    seen = []
    for c in used_colors or filament:
        h = "#%02X%02X%02X" % tuple(int(round(x * 255)) for x in c)
        if h not in seen:
            seen.append(h)
    res.colors = seen[:8]

    res.embedded_thumb = _pick_thumbnail(zf, lower)
    res.photos = [n for n in names
                  if n.lower().startswith("auxiliaries/model pictures/")
                  and n.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "webp")]
    return res


def _root_model_name(zf: zipfile.ZipFile, lower: dict[str, str]) -> str:
    rels = lower.get("_rels/.rels")
    if rels:
        txt = zf.read(rels)
        for m in re.finditer(rb"<Relationship\b([^>]*)/?>", txt):
            a = _attrs(m.group(1))
            if a.get("Type", "").endswith("/3dmodel"):
                return a.get("Target", "").lstrip("/")
    for n in lower:
        if n.startswith("3d/") and n.endswith(".model"):
            return lower[n]
    return "3D/3dmodel.model"


def _bambu_extruders(zf, lower):
    obj_ext: dict[str, int] = {}
    part_ext: dict[tuple[str, str], int] = {}
    name = lower.get("metadata/model_settings.config")
    if not name:
        return obj_ext, part_ext
    txt = zf.read(name)
    for om in re.finditer(rb'<object\s+id="([^"]+)"[^>]*>(.*?)</object>', txt, re.S):
        oid = om.group(1).decode()
        body = om.group(2)
        head = body.split(b"<part", 1)[0]
        m = re.search(rb'key="extruder"\s+value="(\d+)"', head)
        if m:
            obj_ext[oid] = int(m.group(1))
        for pm in re.finditer(rb'<part\s+id="([^"]+)"[^>]*>(.*?)</part>', body, re.S):
            m = re.search(rb'key="extruder"\s+value="(\d+)"', pm.group(2))
            if m and int(m.group(1)):
                part_ext[(oid, pm.group(1).decode())] = int(m.group(1))
    return obj_ext, part_ext


def _plate_count(zf, lower) -> int:
    name = lower.get("metadata/model_settings.config")
    if not name:
        return 1
    return max(1, zf.read(name).count(b"<plate>"))


def _slice_info(zf, lower, res: LoadResult) -> None:
    name = lower.get("metadata/slice_info.config")
    if not name:
        return
    txt = zf.read(name)
    weights = [float(x) for x in re.findall(rb'key="weight"\s+value="([\d.]+)"', txt)]
    times = [float(x) for x in re.findall(rb'key="prediction"\s+value="([\d.]+)"', txt)]
    if weights and sum(weights) > 0:
        res.sliced_weight = sum(weights)
    if times and sum(times) > 0:
        res.print_time = sum(times)


def _pick_thumbnail(zf, lower) -> bytes | None:
    for cand in ("metadata/plate_1.png", "auxiliaries/.thumbnails/thumbnail_middle.png",
                 "metadata/thumbnail.png", "auxiliaries/.thumbnails/thumbnail_3mf.png",
                 "metadata/plate_1_small.png"):
        if cand in lower:
            return zf.read(lower[cand])
    for n in lower:
        if "thumbnail" in n and n.endswith((".png", ".jpg")):
            return zf.read(lower[n])
    return None


# --------------------------------------------------------------------------- G-code

_THUMB_BLOCK = re.compile(
    rb";\s*thumbnail(?:_(\w+))?\s+begin\s+(\d+)x(\d+)\s+\d+\s*\r?\n(.*?);\s*thumbnail(?:_\w+)?\s+end",
    re.S,
)


def read_gcode(head: bytes, tail: bytes) -> LoadResult:
    res = LoadResult()
    best = None
    for m in _THUMB_BLOCK.finditer(head):
        fmt = (m.group(1) or b"PNG").upper()
        if fmt not in (b"PNG", b"JPG", b"JPEG"):
            continue
        w, h = int(m.group(2)), int(m.group(3))
        payload = b"".join(line.strip().lstrip(b";").strip() for line in m.group(4).splitlines())
        try:
            img = base64.b64decode(payload)
        except ValueError:
            continue
        if best is None or w * h > best[0]:
            best = (w * h, img)
    if best:
        res.embedded_thumb = best[1]

    text = head[:200_000] + b"\n" + tail
    m = re.search(rb";\s*(?:total )?filament used \[g\]\s*=\s*([\d.]+)", text)
    if m:
        res.sliced_weight = float(m.group(1))
    m = re.search(rb";\s*estimated printing time(?: \(normal mode\))?\s*=\s*([^\r\n]+)", text)
    if m:
        res.print_time = _duration(m.group(1).decode(errors="replace"))
    m = re.search(rb";\s*(?:filament_colour|extruder_colour)\s*=\s*([^\r\n]+)", text)
    if m:
        cols = [c.strip() for c in m.group(1).decode(errors="replace").replace(",", ";").split(";")]
        res.colors = [c.upper() for c in cols if hex_to_rgb(c)][:8]
    return res


_WORD = re.compile(rb"([XYZEF])(-?\d*\.?\d+)")
MAX_TOOLPATH_SEGMENTS = 450_000


def gcode_toolpath(data: bytes, colors: list[str]) -> Mesh | None:
    """Rebuild the printed part from the extrusion moves of a G-code file.

    Every extruding move becomes a small flat-topped bar (top + two sides),
    the width of a typical line and one layer high, coloured by the active
    tool. Travel moves, retractions and purge lines outside the part are
    dropped by only keeping moves that add filament.
    """
    x = y = z = e = 0.0
    abs_xyz, abs_e = True, True
    tool = 0
    segs: list[tuple] = []
    # Slicers mark the real print with ;TYPE:/;LAYER comments. Anything
    # extruded before the first marker is the start script's purge line at
    # the edge of the bed, which would otherwise shrink the part to a speck.
    # PrusaSlicer tags the start/end scripts as ;TYPE:Custom — skip those too.
    markers = (b";TYPE:", b";LAYER:", b";LAYER_CHANGE")
    started = not any(m in data for m in markers)
    custom = False
    for raw in data.splitlines():
        if raw.startswith(markers):
            started = True
            if raw.startswith(b";TYPE:"):
                custom = raw[6:].strip().lower() == b"custom"
        if not started:
            continue
        line = raw.split(b";", 1)[0].strip()
        if not line:
            continue
        c0 = line[:1]
        if c0 == b"G":
            head = line.split(None, 1)[0]
            if head in (b"G1", b"G0", b"G01", b"G00", b"G2", b"G3", b"G02", b"G03"):
                nx, ny, nz, ne = x, y, z, None
                for k, v in _WORD.findall(line):
                    fv = float(v)
                    if k == b"X":
                        nx = fv if abs_xyz else x + fv
                    elif k == b"Y":
                        ny = fv if abs_xyz else y + fv
                    elif k == b"Z":
                        nz = fv if abs_xyz else z + fv
                    elif k == b"E":
                        ne = fv
                if ne is not None:
                    de = (ne - e) if abs_e else ne
                    if abs_e:
                        e = ne
                    if de > 0 and not custom and (nx != x or ny != y):
                        segs.append((x, y, nx, ny, nz, tool))
                x, y, z = nx, ny, nz
            elif head == b"G90":
                abs_xyz = True
            elif head == b"G91":
                abs_xyz = False
            elif head == b"G92":
                for k, v in _WORD.findall(line):
                    if k == b"E":
                        e = float(v)
        elif c0 == b"M":
            if line.startswith(b"M82"):
                abs_e = True
            elif line.startswith(b"M83"):
                abs_e = False
        elif c0 == b"T" and line[1:2].isdigit():
            try:
                tool = int(line[1:].split()[0])
            except ValueError:
                pass
    if len(segs) < 10:
        return None
    a = np.array(segs, np.float32)
    zs, layer_idx = np.unique(a[:, 4], return_inverse=True)
    layer = float(np.median(np.diff(zs))) if len(zs) > 1 else 0.2
    layer = min(max(layer, 0.05), 0.6)
    if len(a) > MAX_TOOLPATH_SEGMENTS:
        # Too much to draw: keep every k-th layer, drawn k layers thick, so the
        # part still looks solid (thinning segments instead leaves holes).
        k = int(np.ceil(len(a) / MAX_TOOLPATH_SEGMENTS))
        a = a[layer_idx % k == k - 1]
        layer *= k
    p0 = np.stack([a[:, 0], a[:, 1]], 1)
    p1 = np.stack([a[:, 2], a[:, 3]], 1)
    d = p1 - p0
    ln = np.linalg.norm(d, axis=1, keepdims=True)
    keep = ln[:, 0] > 1e-4
    p0, p1, d, ln, a = p0[keep], p1[keep], d[keep], ln[keep], a[keep]
    nrm = np.stack([-d[:, 1], d[:, 0]], 1) / ln * 0.22        # half line width
    top = a[:, 4:5]
    bot = top - layer
    def v(p, off, zz):
        q = p + off
        return np.concatenate([q, zz], 1)
    a0t, a1t = v(p0, nrm, top), v(p1, nrm, top)
    b0t, b1t = v(p0, -nrm, top), v(p1, -nrm, top)
    a0b, a1b = v(p0, nrm, bot), v(p1, nrm, bot)
    b0b, b1b = v(p0, -nrm, bot), v(p1, -nrm, bot)
    tris = np.stack([
        np.stack([a0t, b0t, b1t], 1), np.stack([a0t, b1t, a1t], 1),      # top
        np.stack([a0b, a0t, a1t], 1), np.stack([a0b, a1t, a1b], 1),      # side A
        np.stack([b0b, b1t, b0t], 1), np.stack([b0b, b1b, b1t], 1),      # side B
    ], 1).reshape(-1, 3, 3).astype(np.float32)
    mesh = Mesh(tris)
    pal = [hex_to_rgb(c) for c in colors if hex_to_rgb(c)]
    if pal:
        idx = np.minimum(a[:, 5].astype(int), len(pal) - 1)
        per = np.array(pal, np.float32)[idx]
        mesh.colors = np.repeat(per, 6, axis=0)
    return mesh


def _duration(s: str) -> float | None:
    total = 0.0
    for num, unit in re.findall(r"(\d+)\s*([dhms])", s):
        total += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total or None


# --------------------------------------------------------------------------- ZIP

_THINGIVERSE = re.compile(r"created by Thingiverse user\s+([^\s,]+)", re.I)
_BY_LINE = re.compile(r"^\s*(?:designer|author|created by|by)\s*[:\-]\s*(.+)$", re.I | re.M)


def designer_from_readme(text: str) -> str:
    m = _THINGIVERSE.search(text) or _BY_LINE.search(text)
    return m.group(1).strip()[:80] if m else ""


def zip_has_models(path: str) -> bool:
    """True if the archive holds anything ModelShelf lists (reads only its directory).
    Unreadable archives count as 'yes' so they still show up, with their error."""
    try:
        with zipfile.ZipFile(path) as zf:
            return any(_ext(n) in MESH_EXTS | GCODE_EXTS | CAD_EXTS for n in zf.namelist())
    except (OSError, zipfile.BadZipFile, ValueError):
        return True


def read_zip(data_or_path) -> LoadResult:
    res = LoadResult()
    zf = zipfile.ZipFile(data_or_path)
    infos = [i for i in zf.infolist() if not i.is_dir()]
    models = [i for i in infos if _ext(i.filename) in MESH_EXTS and i.file_size <= MAX_ZIP_MEMBER]
    res.photos = [i.filename for i in infos if _ext(i.filename) in IMAGE_EXTS][:40]
    for i in infos:
        if i.filename.lower().endswith(("readme.txt", "readme.md")) and i.file_size < 200_000:
            res.designer = designer_from_readme(zf.read(i).decode("utf-8", "replace"))
            if res.designer:
                break
    all_models = [i for i in infos if _ext(i.filename) in MESH_EXTS | CAD_EXTS | GCODE_EXTS]
    res.note = f"{len(all_models)} model file{'s' if len(all_models) != 1 else ''} inside"
    if not models:
        return res
    # Render several models side by side, but keep the job bounded.
    models.sort(key=lambda i: -i.file_size)
    chosen = models[:12]
    loaded: list[Mesh] = []
    budget = 0
    for info in chosen:
        if budget > 250 * 1024 * 1024:
            break
        try:
            r = read_bytes(zf.read(info), _ext(info.filename))
        except Exception:
            continue
        budget += info.file_size
        if r.mesh is not None:
            loaded.append(r.mesh)
            for c in r.colors:
                if c not in res.colors:
                    res.colors.append(c)
            res.designer = res.designer or r.designer
        if r.embedded_thumb and res.embedded_thumb is None:
            res.embedded_thumb = r.embedded_thumb
    if loaded:
        res.mesh = merge(arrange(loaded))
        res.mesh.parts = len(loaded)
    if res.embedded_thumb is None:
        pngs = [p for p in res.photos if _ext(p) in (".png", ".jpg", ".jpeg")]
        if pngs and not loaded:
            res.embedded_thumb = zf.read(pngs[0])
    return res


# --------------------------------------------------------------------------- dispatch

def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""


def read_bytes(data: bytes, ext: str) -> LoadResult:
    if ext == ".stl":
        return read_stl(data)
    if ext == ".3mf":
        return read_3mf(data)
    if ext == ".obj":
        return read_obj(data)
    if ext in GCODE_EXTS:
        res = read_gcode(data[:4_000_000], data[-300_000:])
        if res.embedded_thumb is None:
            res.mesh = gcode_toolpath(data, res.colors)
        return res
    if ext == ".zip":
        return read_zip(io.BytesIO(data))
    return LoadResult()


def load_path(path: str) -> LoadResult:
    """Full load of a file on disk (used by the interactive viewer)."""
    ext = _ext(path)
    if ext == ".zip":
        return read_zip(path)
    with open(path, "rb") as fh:
        data = fh.read()
    if ext in GCODE_EXTS:
        # The viewer always wants geometry, even when the file carries a
        # slicer picture (that picture is what the card shows).
        res = read_gcode(data[:4_000_000], data[-300_000:])
        res.mesh = gcode_toolpath(data, res.colors)
        return res
    return read_bytes(data, ext)
