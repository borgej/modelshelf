"""Core tests: format readers, geometry, and scanner safety rules.

Run with:  venv\\Scripts\\python -m pytest -q
"""

import base64
import io
import os
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import formats  # noqa: E402
from core.mesh import Mesh  # noqa: E402

# A 10 mm cube as 12 triangles.
V = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
              [0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10]], np.float32)
F = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
              [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])


def cube_stl_binary() -> bytes:
    out = bytearray(b"\0" * 80) + struct.pack("<I", len(F))
    for f in F:
        out += struct.pack("<3f", 0, 0, 0)
        for i in f:
            out += struct.pack("<3f", *V[i])
        out += b"\0\0"
    return bytes(out)


def cube_stl_ascii() -> bytes:
    lines = ["solid cube"]
    for f in F:
        lines += ["facet normal 0 0 0", " outer loop"]
        lines += [f"  vertex {V[i][0]} {V[i][1]} {V[i][2]}" for i in f]
        lines += [" endloop", "endfacet"]
    return ("\n".join(lines + ["endsolid cube"])).encode()


def cube_3mf(transform="1 0 0 0 1 0 0 0 1 5 5 0", color="#FF0000") -> bytes:
    verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in V)
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F)
    model = f"""<?xml version="1.0"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <metadata name="Title">Test cube</metadata>
 <metadata name="Designer">Tester</metadata>
 <resources>
  <basematerials id="9"><base name="red" displaycolor="{color}"/></basematerials>
  <object id="1" type="model" pid="9" pindex="0"><mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh></object>
  <object id="2" type="model"><components><component objectid="1" transform="{transform}"/></components></object>
 </resources>
 <build><item objectid="2" transform="2 0 0 0 1 0 0 0 1 0 0 0"/></build>
</model>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("3D/3dmodel.model", model)
        z.writestr("_rels/.rels", '<Relationships><Relationship Target="/3D/3dmodel.model" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    return buf.getvalue()


def test_stl_binary_and_ascii_agree():
    a = formats.read_stl(cube_stl_binary()).mesh
    b = formats.read_stl(cube_stl_ascii()).mesh
    assert a.triangle_count == b.triangle_count == 12
    assert np.allclose(a.size(), (10, 10, 10))
    assert a.volume() == pytest.approx(1000, rel=1e-4)
    assert b.area() == pytest.approx(600, rel=1e-4)


def test_ascii_stl_starting_with_solid_but_binary_length():
    # Some exporters write binary files whose 80-byte header begins with "solid".
    data = bytearray(cube_stl_binary())
    data[:5] = b"solid"
    assert formats.read_stl(bytes(data)).mesh.triangle_count == 12


def test_3mf_transforms_colors_and_metadata():
    res = formats.read_3mf(cube_3mf())
    m = res.mesh
    # Component shifts by (5,5,0), then the build item doubles X.
    lo, hi = m.bounds()
    assert np.allclose(lo, (10, 5, 0)) and np.allclose(hi, (30, 15, 10))
    assert m.volume() == pytest.approx(2000, rel=1e-4)
    assert res.colors == ["#FF0000"]
    assert res.title == "Test cube" and res.designer == "Tester"


def test_3mf_junk_title_ignored():
    data = cube_3mf().replace(b"x", b"x")
    buf = io.BytesIO(data)
    z = zipfile.ZipFile(buf)
    model = z.read("3D/3dmodel.model").replace(b">Test cube<", b">(Unsaved)<")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as w:
        w.writestr("3D/3dmodel.model", model)
    assert formats.read_3mf(out.getvalue()).title == ""


def test_obj_polygons_are_triangulated():
    obj = b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1/1 2/2/2 3/3/3 4/4/4\n"
    assert formats.read_obj(obj).mesh.triangle_count == 2


def test_paint_color_decoding():
    assert formats._decode_paint("8") == 2          # leaf, state 2
    assert formats._decode_paint("4") == 1
    assert formats._decode_paint("0C") == 3         # state >= 3 spills into next nibble
    assert formats._decode_paint("") == 0


def test_gcode_thumbnail_metadata_and_toolpath():
    from PIL import Image
    png = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(png, "PNG")
    b64 = base64.b64encode(png.getvalue()).decode()
    lines = [f"; {b64[i:i + 76]}" for i in range(0, len(b64), 76)]
    head = ("; thumbnail begin 16x16 %d\n" % len(b64) + "\n".join(lines) + "\n; thumbnail end\n").encode()
    body = b"G90\nM82\nG1 X0 Y0 E0\n;TYPE:Custom\nG1 X200 Y0 E5\n;LAYER_CHANGE\n;TYPE:Perimeter\n"
    e = 5.0
    for layer in range(1, 6):
        z = layer * 0.2
        body += f"G1 Z{z:.1f}\nG1 X10 Y10\n".encode()
        for x, y in ((20, 10), (20, 20), (10, 20), (10, 10)):
            e += 0.5
            body += f"G1 X{x} Y{y} E{e:.3f}\n".encode()
    tail = b"; filament used [g] = 12.34\n; estimated printing time (normal mode) = 1h 2m 3s\n"
    res = formats.read_gcode(head, tail)
    assert res.embedded_thumb == png.getvalue()
    assert res.sliced_weight == pytest.approx(12.34)
    assert res.print_time == 3723
    mesh = formats.gcode_toolpath(head + body + tail, ["#00FF00"])
    sx, sy, sz = mesh.size()
    # The 200 mm purge line in the Custom block must not count.
    assert sx < 15 and sy < 15 and sz == pytest.approx(1.0, abs=0.01)
    assert mesh.colors is not None


def test_zip_with_models_and_readme(tmp_path):
    p = tmp_path / "thing.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("files/a.stl", cube_stl_binary())
        z.writestr("files/b.stl", cube_stl_ascii())
        z.writestr("images/pic.png", b"not really")
        z.writestr("README.txt", "This thing was created by Thingiverse user MakerMe, and is licensed")
    res = formats.read_zip(str(p))
    assert res.designer == "MakerMe"
    assert res.mesh.parts == 2 and res.mesh.triangle_count == 24
    assert res.photos == ["images/pic.png"]
    assert "2 model files" in res.note


def test_weight_estimate():
    from core.settings import Settings, estimate_grams
    s = Settings(infill=0, wall_mm=1.0, material="PLA")
    # 10 mm cube: shell ~= area*wall = 600 mm³ -> 0.6 cm³ * 1.24
    assert estimate_grams(1000, 600, s) == pytest.approx(0.744, rel=1e-3)
    s.infill = 100
    assert estimate_grams(1000, 600, s) == pytest.approx(1.24, rel=1e-3)


# --------------------------------------------------------------------------- scanner

def _gpu_available() -> bool:
    try:
        import moderngl
        from core.system import standalone_gl_context
        standalone_gl_context().release()
        return True
    except Exception:
        return False


def _scan(home, roots):
    os.environ["MODELSHELF_HOME"] = str(home)
    from core.db import Library
    from core.scanner import Scanner
    from core.settings import Settings
    lib = Library(str(home / "lib.sqlite"))
    s = Settings(roots=[str(r) for r in roots], workers=2, low_priority=False)
    logs = []
    stats = Scanner(lib, s, progress=lambda *a: None, item_changed=lambda i: None,
                    items_removed=lambda i: None, log=logs.append).run()
    return lib, stats


def test_scanner_incremental_offline_nested_and_moves(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    lib_dir = tmp_path / "models"
    (lib_dir / "sub").mkdir(parents=True)
    (lib_dir / "a.stl").write_bytes(cube_stl_binary())
    (lib_dir / "sub" / "b.stl").write_bytes(cube_stl_ascii())

    # Nested roots must not list files twice.
    lib, st = _scan(home, [lib_dir, lib_dir / "sub"])
    assert st.found == 2 and len(lib.all_items()) == 2
    b = lib.by_path(str(lib_dir / "sub" / "b.stl"))
    assert b.dims
    if _gpu_available():                 # build servers have no OpenGL 3.3
        assert b.thumb
    lib.set_user([b.id], tags=["keep-me"], status="to_print")
    lib.close()

    # Unchanged files are not re-analysed.
    lib, st = _scan(home, [lib_dir])
    assert st.analyzed == 0 and st.unchanged == 2
    lib.close()

    # A library folder that is temporarily unavailable (unplugged drive)
    # keeps its models and their tags.
    offline = lib_dir.with_name("unplugged")
    os.replace(lib_dir, offline)
    lib, st = _scan(home, [lib_dir])
    assert st.removed == 0 and len(lib.all_items()) == 2
    lib.close()
    os.replace(offline, lib_dir)

    # Moving a file keeps tags/status (matched by content hash).
    os.replace(lib_dir / "sub" / "b.stl", lib_dir / "moved.stl")
    lib, st = _scan(home, [lib_dir])
    moved = lib.by_path(str(lib_dir / "moved.stl"))
    assert moved.tags == ["keep-me"] and moved.status == "to_print"
    assert lib.by_path(str(lib_dir / "sub" / "b.stl")) is None
    lib.close()


def test_scanner_forgets_removed_root(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    d = tmp_path / "m"
    d.mkdir()
    (d / "a.stl").write_bytes(cube_stl_binary())
    lib, _ = _scan(home, [d])
    lib.close()
    other = tmp_path / "other"
    other.mkdir()
    lib, st = _scan(home, [other])       # folder removed from the library on purpose
    assert st.removed == 1 and lib.all_items() == []
    lib.close()


def test_zip_without_models_is_hidden(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    d = tmp_path / "dl"
    d.mkdir()
    with zipfile.ZipFile(d / "driver.zip", "w") as z:
        z.writestr("setup.exe", b"MZ" + b"\0" * 1000)
        z.writestr("readme.txt", "hello")
    with zipfile.ZipFile(d / "thing.zip", "w") as z:
        z.writestr("files/a.stl", cube_stl_binary())
    lib, st = _scan(home, [d])
    names = [i.name for i in lib.all_items()]
    assert names == ["thing.zip"]
    lib.close()
    lib, st = _scan(home, [d])           # checked once, not re-opened on the next scan
    assert st.unchanged == 2 and st.analyzed == 0
    lib.close()


def test_old_database_gets_hidden_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, root TEXT, name TEXT, "
                "ext TEXT, kind TEXT, size INTEGER, mtime REAL, sha256 TEXT, dim_x REAL, dim_y REAL, dim_z REAL, "
                "volume REAL, area REAL, triangles INTEGER, parts INTEGER, colors TEXT, designer TEXT, title TEXT, "
                "photos TEXT, note TEXT, sliced_weight REAL, print_time REAL, thumb TEXT, thumb_source TEXT, "
                "error TEXT, analyzer INTEGER DEFAULT 0, added_at REAL, analyzed_at REAL, status TEXT DEFAULT '', "
                "favorite INTEGER DEFAULT 0, tags TEXT DEFAULT '[]', notes TEXT DEFAULT '')")
    con.execute("INSERT INTO files (path, name, ext, kind, size, mtime, note) VALUES "
                "('C:/a/sw.zip','sw.zip','.zip','ZIP',1,1,'0 model files inside'),"
                "('C:/a/m.zip','m.zip','.zip','ZIP',1,1,'2 model files inside')")
    con.commit()
    con.close()
    from core.db import Library
    lib = Library(str(db))
    assert [i.name for i in lib.all_items()] == ["m.zip"]
    lib.close()


def test_norwegian_date_format():
    import datetime as dt
    from core.mesh import fmt_date
    ts = dt.datetime(2026, 5, 16, 14, 3).timestamp()
    assert fmt_date(ts) == "16.05.2026"
    assert fmt_date(ts, with_time=True) == "16.05.2026 14:03"
    assert fmt_date(None) == ""


def test_viewer_loads_toolpath_even_with_embedded_thumbnail(tmp_path):
    import base64
    from PIL import Image
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG")
    b64 = base64.b64encode(png.getvalue()).decode()
    g = f"; thumbnail begin 8x8 {len(b64)}\n; {b64}\n; thumbnail end\nG90\nM82\n;LAYER_CHANGE\n;TYPE:Perimeter\n"
    e = 0.0
    for layer in range(1, 4):
        g += f"G1 Z{layer * 0.2:.1f}\nG1 X0 Y0\n"
        for x, y in ((10, 0), (10, 10), (0, 10), (0, 0)):
            e += 0.4
            g += f"G1 X{x} Y{y} E{e:.2f}\n"
    p = tmp_path / "part.gcode"
    p.write_text(g)
    res = formats.load_path(str(p))
    assert res.embedded_thumb and res.mesh is not None and res.mesh.triangle_count > 0
