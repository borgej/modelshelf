# PyInstaller recipe for ModelShelf. Build with:  python build_exe.py
import os
import re

from PyInstaller.utils.hooks import collect_submodules

_version = re.search(r'__version__\s*=\s*"([^"]+)"', open("version.py", encoding="utf-8").read()).group(1)
_console = os.environ.get("MODELSHELF_CONSOLE") == "1"

# moderngl loads its OpenGL backend (glcontext) dynamically — not found by static analysis.
hiddenimports = collect_submodules("glcontext") + ["moderngl"]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib", "pytest", "tkinter", "scipy",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNetworkAuth",
        "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
        "PySide6.QtTextToSpeech", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name=f"ModelShelf-{_version}",
    debug=False, strip=False, upx=False, runtime_tmpdir=None,
    console=_console,
    icon="ModelShelf.ico",
)
