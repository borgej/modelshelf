"""Off-screen thumbnail rendering on the GPU (moderngl, no window needed).

Each worker process owns one standalone OpenGL context, created lazily on the
first render. The camera looks at the model from the front-right and slightly
above (Z is up, as in every slicer), and the orthographic frame is fitted to
the model's actual projected outline, so thin or tall models fill the card as
well as cubes do. The background is transparent — the card behind it paints
its own backdrop, which is what makes one set of thumbnails work in both the
dark and the light theme.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter

from core.mesh import Mesh

VERT = """
#version 330
uniform mat4 mvp;
uniform mat3 view_rot;
in vec3 in_pos;
in vec3 in_norm;
in vec3 in_col;
out vec3 v_norm;
out vec3 v_col;
void main() {
    gl_Position = mvp * vec4(in_pos, 1.0);
    v_norm = view_rot * in_norm;
    v_col = in_col;
}
"""

FRAG = """
#version 330
in vec3 v_norm;
in vec3 v_col;
out vec4 f_color;
void main() {
    vec3 n = normalize(v_norm);
    if (!gl_FrontFacing) n = -n;          // tolerate inverted / open meshes
    vec3 key  = normalize(vec3(-0.45, 0.55, 0.70));
    vec3 fill = normalize(vec3(0.70, -0.15, 0.45));
    float d = max(dot(n, key), 0.0) * 0.72 + max(dot(n, fill), 0.0) * 0.26;
    float hemi = 0.5 + 0.5 * n.y;                          // sky above, ground below
    float rim = pow(1.0 - max(n.z, 0.0), 3.0) * 0.18;
    vec3 base = pow(v_col, vec3(2.2));                     // work in linear light
    vec3 lit = base * (0.20 + 0.18 * hemi + d) + rim * vec3(0.9, 0.95, 1.0);
    float spec = pow(max(dot(reflect(-key, n), vec3(0.0, 0.0, 1.0)), 0.0), 28.0) * 0.22;
    lit += spec;
    f_color = vec4(pow(lit, vec3(1.0 / 2.2)), 1.0);
}
"""

# Triangle budget for one draw: beyond this the mesh is thinned out. At
# thumbnail size the difference is invisible, and it bounds GPU memory.
MAX_RENDER_TRIS = 3_000_000

AZIMUTH = math.radians(-35.0)
ELEVATION = math.radians(28.0)


def look_rotation(az: float, el: float) -> np.ndarray:
    """World→view rotation (rows = right, up, back) for a Z-up orbit camera."""
    back = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
    right = np.cross(np.array([0.0, 0.0, 1.0]), back)
    if np.linalg.norm(right) < 1e-6:
        right = np.array([1.0, 0.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(back, right)
    return np.stack([right, up, back])


def ortho_mvp(tris: np.ndarray, rot: np.ndarray, margin: float = 0.06, zoom: float = 1.0,
              pan=(0.0, 0.0), aspect: float = 1.0, bounds=None, fit_half: float | None = None) -> np.ndarray:
    """MVP that fits the projected model into the viewport.

    Without `bounds` the frame hugs the model's projected outline (thumbnails).
    With `bounds` (world-space min/max) it fits the bounding sphere instead, so
    the model keeps a constant size while it is being orbited.
    """
    if bounds is not None:
        blo, bhi = (np.asarray(b, np.float64) for b in bounds)
        c = rot @ ((blo + bhi) / 2)
        rad = float(np.linalg.norm(bhi - blo)) / 2
        lo, hi = c - rad, c + rad
        if fit_half:
            # Keep the scale fixed at the start view's tight fit while orbiting.
            lo[:2], hi[:2] = c[:2] - fit_half, c[:2] + fit_half
    else:
        pts = tris.reshape(-1, 3)
        if len(pts) > 400_000:
            pts = pts[:: len(pts) // 400_000 + 1]
        vs = pts.astype(np.float64) @ rot.T
        lo, hi = vs.min(axis=0), vs.max(axis=0)
    cx, cy = (lo[0] + hi[0]) / 2 + pan[0], (lo[1] + hi[1]) / 2 + pan[1]
    half = (fit_half * (1 + margin * 2) if fit_half and bounds is not None
            else max(hi[0] - lo[0], (hi[1] - lo[1]) * aspect) / 2 * (1 + margin * 2)) / zoom
    half = max(half, 1e-3)
    hx, hy = half, half / aspect
    near, far = -hi[2] - 1.0, -lo[2] + 1.0
    depth = max(far - near, 1e-3)
    proj = np.array([
        [1 / hx, 0, 0, -cx / hx],
        [0, 1 / hy, 0, -cy / hy],
        [0, 0, -2 / depth, -(far + near) / depth],
        [0, 0, 0, 1],
    ])
    view = np.eye(4)
    view[:3, :3] = rot
    return proj @ view


def vertex_data(mesh: Mesh, default_rgb) -> np.ndarray:
    tris = mesh.tris
    colors = mesh.colors
    if len(tris) > MAX_RENDER_TRIS:
        step = len(tris) // MAX_RENDER_TRIS + 1
        tris = tris[::step]
        colors = colors[::step] if colors is not None else None
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    if colors is None:
        colors = np.tile(np.asarray(default_rgb, np.float32), (len(tris), 1))
    data = np.empty((len(tris), 3, 9), np.float32)
    data[:, :, 0:3] = tris
    data[:, :, 3:6] = n[:, None, :]
    data[:, :, 6:9] = colors[:, None, :3]
    return data.reshape(-1)


class Renderer:
    def __init__(self):
        self._ctx = None
        self._prog = None

    def _ensure(self):
        if self._ctx is None:
            import moderngl
            self._ctx = moderngl.create_standalone_context()
            self._prog = self._ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        return self._ctx

    def render(self, mesh: Mesh, size: int = 384, default_rgb=(0.8, 0.82, 0.86),
               shadow: bool = True) -> Image.Image:
        ctx = self._ensure()
        import moderngl
        rot = look_rotation(AZIMUTH, ELEVATION)
        mvp = ortho_mvp(mesh.tris, rot, margin=0.08 if shadow else 0.04)
        data = vertex_data(mesh, default_rgb)

        ss = 2                                   # supersample on top of MSAA
        w = h = size * ss
        samples = min(8, ctx.max_samples)
        color_ms = ctx.renderbuffer((w, h), 4, samples=samples)
        depth_ms = ctx.depth_renderbuffer((w, h), samples=samples)
        fbo_ms = ctx.framebuffer(color_attachments=[color_ms], depth_attachment=depth_ms)
        color = ctx.renderbuffer((w, h), 4)
        fbo = ctx.framebuffer(color_attachments=[color])
        vbo = ctx.buffer(data.tobytes())
        vao = ctx.vertex_array(self._prog, [(vbo, "3f 3f 3f", "in_pos", "in_norm", "in_col")])
        try:
            fbo_ms.use()
            ctx.viewport = (0, 0, w, h)
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.disable(moderngl.CULL_FACE)
            ctx.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
            self._prog["mvp"].write(mvp.T.astype("f4").tobytes())
            self._prog["view_rot"].write(rot.T.astype("f4").tobytes())
            vao.render(moderngl.TRIANGLES)
            ctx.copy_framebuffer(fbo, fbo_ms)
            raw = fbo.read(components=4, alignment=1)
        finally:
            for obj in (vao, vbo, fbo, color, fbo_ms, color_ms, depth_ms):
                obj.release()

        arr = np.frombuffer(raw, np.uint8).reshape(h, w, 4)[::-1].astype(np.float32)
        # MSAA resolve blends edge pixels with the transparent-black clear colour;
        # un-premultiply so silhouettes don't get a dark fringe.
        a = arr[:, :, 3:4] / 255.0
        arr[:, :, :3] = np.where(a > 0, arr[:, :, :3] / np.maximum(a, 1e-6), 0)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
        img = img.resize((size, size), Image.LANCZOS)
        return add_shadow(img) if shadow else img


def add_shadow(img: Image.Image) -> Image.Image:
    """Soft contact shadow under the model, so it sits on the card instead of floating."""
    w, h = img.size
    alpha = img.getchannel("A")
    shadow_a = alpha.point(lambda v: int(v * 0.38))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_a)
    shadow = shadow.transform(img.size, Image.AFFINE, (1, 0, 0, 0, 1, -max(3, h // 70)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, w // 60)))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(shadow)
    out.alpha_composite(img)
    return out


_renderer: Renderer | None = None


def renderer() -> Renderer:
    global _renderer
    if _renderer is None:
        _renderer = Renderer()
    return _renderer
