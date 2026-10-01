"""Interactive 3D preview (QOpenGLWidget + moderngl, same shaders as the thumbnails).

Left-drag orbits, right/middle-drag pans, wheel zooms, double-click resets.
The mesh is loaded on a background thread; until it arrives the thumbnail is
shown, so selecting a card never blocks the UI.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core import formats
from core.mesh import hex_to_rgb
from core.render import AZIMUTH, ELEVATION, FRAG, VERT, look_rotation, ortho_mvp, vertex_data
from ui import theme


class _Loader(QThread):
    done = Signal(int, object, str)

    def __init__(self, token: int, path: str):
        super().__init__()
        self.token, self.path = token, path

    def run(self):
        try:
            res = formats.load_path(self.path)
            self.done.emit(self.token, res.mesh, "" if res.mesh is not None else "no geometry")
        except Exception as exc:
            self.done.emit(self.token, None, str(exc))


class ModelViewer(QOpenGLWidget):
    status = Signal(str)
    ready = Signal(bool)          # True = a 3D model is loaded and can be rotated

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.ctx = None
        self.prog = None
        self.vao = None
        self.vbo = None
        self.mesh = None
        self.fallback: QPixmap | None = None
        self.message = ""
        self.default_rgb = (0.8, 0.82, 0.86)
        self._token = 0
        self._loaders: list[_Loader] = []
        self.gl_failed = False
        self.reset_view()
        self._drag = None

    # ---------------------------------------------------------------- public
    def reset_view(self):
        self.az, self.el, self.zoom, self.pan = AZIMUTH, ELEVATION, 1.0, [0.0, 0.0]
        self.update()

    def show_path(self, path: str | None, thumb: QPixmap | None, renderable: bool, default_color: str):
        self._token += 1
        self._path = path
        self.default_rgb = hex_to_rgb(default_color) or self.default_rgb
        self.fallback = thumb
        self._drop_mesh()
        self.reset_view()
        if path and renderable and not self.gl_failed:
            self.message = "Loading 3D view…"
            loader = _Loader(self._token, path)
            loader.done.connect(self._loaded)
            loader.finished.connect(lambda l=loader: self._loaders.remove(l) if l in self._loaders else None)
            self._loaders.append(loader)
            loader.start()
        else:
            self.message = ""
            self.ready.emit(False)
        self.update()

    def clear(self):
        self._token += 1
        self._drop_mesh()
        self.fallback = None
        self.message = ""
        self.update()

    def _start_fit(self, mesh) -> float:
        """Half-size that frames the model tightly in the start view, measured
        around the bounding-box centre (the point the view orbits)."""
        rot = look_rotation(AZIMUTH, ELEVATION)
        pts = mesh.tris.reshape(-1, 3)
        if len(pts) > 300_000:
            pts = pts[:: len(pts) // 300_000 + 1]
        lo, hi = self._bounds
        c = rot @ ((np.asarray(lo, np.float64) + np.asarray(hi, np.float64)) / 2)
        vs = pts.astype(np.float64) @ rot.T - c
        return float(max(np.abs(vs[:, 0]).max(), np.abs(vs[:, 1]).max()))

    # ---------------------------------------------------------------- loading
    def _loaded(self, token: int, mesh, err: str):
        if token != self._token:
            return
        if err:
            self.status.emit(f"3D view: could not load {self._path}: {err}")
        if mesh is not None and self.gl_failed:
            self.status.emit("3D view: model loaded but OpenGL is unavailable")
        if mesh is None or not mesh.triangle_count or self.gl_failed:
            self.message = ""
            self.ready.emit(False)
            self.update()
            return
        self.mesh = mesh
        self._bounds = mesh.bounds()
        self._fit_half = self._start_fit(mesh)
        self.message = ""
        if self.ctx is None:
            # The widget hasn't been shown yet, so there's no GL context: a small
            # file can finish loading before the panel's first paint. Keep the
            # mesh; initializeGL uploads it.
            self.update()
            return
        self._upload()
        self.ready.emit(self.vao is not None)
        self.update()

    def _drop_mesh(self):
        self.mesh = None
        if self.ctx is not None:
            self.makeCurrent()
            for o in (self.vao, self.vbo):
                if o is not None:
                    o.release()
            self.doneCurrent()
        self.vao = self.vbo = None

    def _upload(self):
        if self.ctx is None or self.mesh is None:
            return
        self.makeCurrent()
        data = vertex_data(self.mesh, self.default_rgb)
        self.vbo = self.ctx.buffer(data.tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(self.vbo, "3f 3f 3f", "in_pos", "in_norm", "in_col")])
        self.doneCurrent()

    # ---------------------------------------------------------------- GL
    def initializeGL(self):
        try:
            import moderngl
            self.ctx = moderngl.create_context()
            self.prog = self.ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
            if self.mesh is not None and self.vao is None:
                data = vertex_data(self.mesh, self.default_rgb)       # context is current here
                self.vbo = self.ctx.buffer(data.tobytes())
                self.vao = self.ctx.vertex_array(
                    self.prog, [(self.vbo, "3f 3f 3f", "in_pos", "in_norm", "in_col")])
                self.ready.emit(True)
        except Exception as exc:
            self.gl_failed = True
            self.ctx = None
            import traceback
            self.status.emit(f"3D view unavailable (OpenGL): {exc}\n{traceback.format_exc(limit=6)}")

    def paintGL(self):
        dpr = self.devicePixelRatioF()
        w, h = int(self.width() * dpr), int(self.height() * dpr)
        if self.ctx is not None:
            import moderngl
            fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
            fbo.use()
            self.ctx.viewport = (0, 0, w, h)
            bg = QColor(theme.C["stage_out"])
            self.ctx.clear(bg.redF(), bg.greenF(), bg.blueF(), 1.0, depth=1.0)
            if self.vao is not None and self.mesh is not None:
                self.ctx.enable(moderngl.DEPTH_TEST)
                self.ctx.disable(moderngl.CULL_FACE)
                rot = look_rotation(self.az, self.el)
                mvp = ortho_mvp(self.mesh.tris, rot, margin=0.08, zoom=self.zoom,
                                pan=self.pan, aspect=w / max(h, 1),
                                bounds=self._bounds, fit_half=self._fit_half * max(1.0, w / max(h, 1)))
                self.prog["mvp"].write(mvp.T.astype("f4").tobytes())
                self.prog["view_rot"].write(rot.T.astype("f4").tobytes())
                self.vao.render(moderngl.TRIANGLES)
                self.ctx.disable(moderngl.DEPTH_TEST)
        if self.mesh is None:
            # Overlay the thumbnail / message with QPainter on top of the GL clear.
            p = QPainter(self)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            if self.ctx is None:
                p.fillRect(self.rect(), QColor(theme.C["stage_out"]))
            if self.fallback is not None and not self.fallback.isNull():
                side = min(self.width(), self.height()) - 24
                px = self.fallback
                sc = side / max(px.width(), px.height()) * px.devicePixelRatioF()
                tw, th = px.width() / px.devicePixelRatioF() * sc, px.height() / px.devicePixelRatioF() * sc
                p.drawPixmap(QRectF((self.width() - tw) / 2, (self.height() - th) / 2, tw, th), px,
                             QRectF(px.rect()))
            if self.message:
                f = QFont()
                f.setPixelSize(12)
                p.setFont(f)
                p.setPen(QColor(theme.C["text2"]))
                p.drawText(self.rect().adjusted(0, 0, 0, -12), Qt.AlignHCenter | Qt.AlignBottom, self.message)
            p.end()

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, e):
        self._drag = (e.position(), e.button())

    def mouseMoveEvent(self, e):
        if not self._drag or self.mesh is None:
            return
        pos, btn = self._drag
        d = e.position() - pos
        self._drag = (e.position(), btn)
        if btn == Qt.LeftButton:
            self.az -= d.x() * 0.01
            self.el = max(-1.45, min(1.45, self.el + d.y() * 0.01))
        else:
            lo, hi = self.mesh.bounds()
            span = float(np.linalg.norm(hi - lo)) / self.zoom
            k = span / max(self.width(), 1)
            self.pan[0] -= d.x() * k
            self.pan[1] += d.y() * k
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.reset_view()

    def wheelEvent(self, e):
        if self.mesh is None:
            return
        self.zoom = max(0.3, min(30.0, self.zoom * math.pow(1.0015, e.angleDelta().y())))
        self.update()
