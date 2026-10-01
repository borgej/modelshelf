"""The few places where Windows, Linux and macOS differ, kept in one file.

Everything else in ModelShelf is written against Qt and the Python standard
library and behaves the same everywhere.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC

# Words that differ between systems, for menus and messages.
FILE_MANAGER = "File Explorer" if IS_WINDOWS else "Finder" if IS_MAC else "file manager"
TRASH = "Recycle Bin" if IS_WINDOWS else "Trash" if IS_MAC else "trash"


def user_data_dir(app: str) -> Path:
    """Where the library database, thumbnails and settings live."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif IS_MAC:
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / app


def lower_priority() -> None:
    """Make this process yield to interactive programs (used by scan workers)."""
    try:
        import psutil
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if IS_WINDOWS else 10)
    except Exception:
        pass


def reveal(path: str) -> None:
    """Open the system file manager with `path` selected (or at least its folder)."""
    path = os.path.normpath(path)
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", path])
            return
        if IS_MAC:
            subprocess.Popen(["open", "-R", path])
            return
        # Linux: the FileManager1 D-Bus interface selects the file in Nautilus,
        # Dolphin, Nemo, Thunar… Fall back to just opening the folder.
        uri = Path(path).as_uri()
        for tool in (["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                      "--object-path", "/org/freedesktop/FileManager1",
                      "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
                     ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                      "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                      f"array:string:{uri}", "string:"]):
            if shutil.which(tool[0]):
                if subprocess.run(tool, capture_output=True, timeout=5).returncode == 0:
                    return
        subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except (OSError, subprocess.SubprocessError):
        pass


# --------------------------------------------------------------------------- slicers

# Recognised by a fragment of the program's file name (lower case, no separators).
SLICER_NAMES = {
    "elegooslicer": "ElegooSlicer", "orcaslicer": "OrcaSlicer", "bambustudio": "Bambu Studio",
    "prusaslicer": "PrusaSlicer", "superslicer": "SuperSlicer", "cura": "Cura",
    "crealityprint": "Creality Print", "anycubicslicer": "Anycubic Slicer",
}
# These import a .zip download directly; others get the models unpacked first.
ZIP_CAPABLE = ("elegooslicer", "orcaslicer")

_WINDOWS_GLOBS = [
    r"C:\Program Files\ElegooSlicer\elegoo-slicer.exe",
    r"C:\Program Files\Bambu Studio\bambu-studio.exe",
    r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
    r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
    r"C:\Program Files\Ultimaker Cura*\UltiMaker-Cura.exe",
    r"C:\Program Files\Ultimaker Cura*\Ultimaker-Cura.exe",
    r"C:\Program Files\Creality\Creality Print*\CrealityPrint.exe",
    r"C:\Program Files\Anycubic Slicer*\AnycubicSlicer*.exe",
    r"C:\Program Files\SuperSlicer*\superslicer.exe",
]
_LINUX_COMMANDS = ["elegoo-slicer", "ElegooSlicer", "orca-slicer", "OrcaSlicer", "bambu-studio",
                   "BambuStudio", "prusa-slicer", "PrusaSlicer", "superslicer", "cura", "UltiMaker-Cura",
                   "CrealityPrint"]
_FLATPAK_IDS = ["io.github.softfever.OrcaSlicer", "com.bambulab.BambuStudio", "com.prusa3d.PrusaSlicer",
                "com.ultimaker.cura", "com.elegoo.ElegooSlicer"]
_MAC_APPS = ["ElegooSlicer", "OrcaSlicer", "BambuStudio", "PrusaSlicer", "Original Prusa Drivers/PrusaSlicer",
             "UltiMaker Cura", "Creality Print", "SuperSlicer"]


def _key(path: str) -> str:
    stem = Path(path).name.lower()
    for ext in (".exe", ".appimage"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
    return "".join(ch for ch in stem if ch.isalnum())


def slicer_name(path: str) -> str:
    if not path:
        return "slicer"
    key = _key(path)
    for fragment, name in SLICER_NAMES.items():
        if fragment in key:
            return name
    return Path(path).stem


def slicer_takes_zip(path: str) -> bool:
    return any(fragment in _key(path) for fragment in ZIP_CAPABLE)


def find_slicers() -> list[str]:
    """Installed slicers, best guess first. Never raises."""
    found: list[str] = []
    try:
        if IS_WINDOWS:
            for pattern in _WINDOWS_GLOBS:
                found.extend(glob.glob(pattern))
            local = os.environ.get("LOCALAPPDATA", "")
            for p in glob.glob(os.path.join(local, "Programs", "*", "*.exe")):
                low = p.lower()
                if any(k in low for k in ("slicer", "cura", "bambu")) and "uninst" not in low:
                    found.append(p)
        elif IS_MAC:
            for app in _MAC_APPS:
                for p in glob.glob(f"/Applications/{app}.app/Contents/MacOS/*"):
                    if os.access(p, os.X_OK) and os.path.isfile(p):
                        found.append(p)
                        break
        else:
            for cmd in _LINUX_COMMANDS:
                p = shutil.which(cmd)
                if p:
                    found.append(p)
            for fid in _FLATPAK_IDS:
                for base in ("/var/lib/flatpak/exports/bin", str(Path.home() / ".local/share/flatpak/exports/bin")):
                    p = os.path.join(base, fid)
                    if os.path.exists(p):
                        found.append(p)
            for folder in ("Applications", "AppImages", ".local/bin", "Downloads"):
                for p in glob.glob(str(Path.home() / folder / "*.AppImage")):
                    if any(fragment in _key(p) for fragment in SLICER_NAMES) and os.access(p, os.X_OK):
                        found.append(p)
    except OSError:
        pass
    return list(dict.fromkeys(found))


def slicer_browse_hint() -> tuple[str, str]:
    """(start folder, file filter) for the 'choose slicer' dialog."""
    if IS_WINDOWS:
        return r"C:\Program Files", "Programs (*.exe)"
    if IS_MAC:
        return "/Applications", "All files (*)"
    return str(Path.home()), "Programs (*.AppImage *);;All files (*)"


# --------------------------------------------------------------------------- OpenGL

# moderngl's Linux backends look for "libGL.so" and "libX11.so" by default.
# Those unversioned names only exist where development packages are installed;
# a normal desktop has the versioned ones.
_X11_LIBS = {"libgl": "libGL.so.1", "libx11": "libX11.so.6"}
_EGL_LIBS = {"backend": "egl", "libgl": "libGL.so.1", "libegl": "libEGL.so.1"}


def prepare_qt() -> None:
    """Call before QApplication is created.

    On Linux the 3D view attaches to Qt's OpenGL context through GLX, so ask Qt
    for the X11 platform with GLX. On a Wayland desktop that runs through
    XWayland, like most other applications. Anything the user (or a test run)
    has set explicitly is left alone.
    """
    if IS_LINUX:
        if os.environ.get("DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
        os.environ.setdefault("QT_XCB_GL_INTEGRATION", "xcb_glx")


def _try_contexts(make, attempts):
    errors = []
    for kwargs in attempts:
        try:
            ctx = make(**kwargs)
            if ctx.version_code >= 330:
                return ctx
            errors.append(f"OpenGL {ctx.version_code} is too old")
        except Exception as exc:
            errors.append(f"{kwargs.get('backend', 'default')}: {exc}")
    raise RuntimeError("; ".join(dict.fromkeys(errors)))


def standalone_gl_context():
    """A windowless OpenGL 3.3 context for thumbnail rendering.

    Windows and macOS have one way to make it. On Linux the X11 route needs a
    display; EGL covers Wayland-only sessions and machines without a screen.
    """
    import moderngl
    attempts = [{}] if not IS_LINUX else [_X11_LIBS, _EGL_LIBS, {}, {"backend": "egl"}]
    try:
        return _try_contexts(moderngl.create_standalone_context, attempts)
    except RuntimeError as exc:
        raise RuntimeError(f"no OpenGL 3.3 context ({exc})") from None


def attach_gl_context():
    """A moderngl context for the OpenGL context Qt has made current."""
    import moderngl
    attempts = [{}] if not IS_LINUX else [_X11_LIBS, {}]
    try:
        return _try_contexts(moderngl.create_context, attempts)
    except RuntimeError as exc:
        raise RuntimeError(f"cannot use Qt's OpenGL context ({exc})") from None
