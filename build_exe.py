"""Builds ModelShelf into a single stand-alone program for the system it runs on.

    python build_exe.py            # normal build (bumps the patch version)
    python build_exe.py --console  # Windows: keep a console window for debugging
    python build_exe.py --no-bump  # rebuild the same version
    python build_exe.py --clean    # clear PyInstaller's cache first

Windows:  dist/ModelShelf-1.2.3.exe  and  dist/ModelShelf.exe
Linux:    dist/ModelShelf-1.2.3-linux-x86_64.tar.gz  and  dist/ModelShelf-linux-x86_64.tar.gz

The version is part of the file name on purpose: a new build never overwrites
a copy that is running. The second file always has the same name, for desktop
shortcuts and for the "latest release" download link.
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).parent
VERSION_FILE = ROOT / "version.py"
IS_WINDOWS = sys.platform == "win32"
EXE = ".exe" if IS_WINDOWS else ""


def read_version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', VERSION_FILE.read_text(encoding="utf-8")).group(1)


def bump(part: str) -> str:
    major, minor, patch = (int(x) for x in read_version().split("."))
    if part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"
    VERSION_FILE.write_text(f'__version__ = "{new}"\n', encoding="utf-8")
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--no-bump", action="store_true")
    ap.add_argument("--minor", action="store_true")
    args = ap.parse_args()
    os.chdir(ROOT)
    if args.clean:
        shutil.rmtree(ROOT / "build", ignore_errors=True)
    version = read_version() if args.no_bump else bump("minor" if args.minor else "patch")
    subprocess.check_call([sys.executable, "make_icon.py"])
    env = dict(os.environ, MODELSHELF_CONSOLE="1" if args.console else "0")
    t0 = time.time()
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "ModelShelf.spec"], env=env)
    exe = ROOT / "dist" / f"ModelShelf-{version}{EXE}"
    print(f"\nBuilt {exe}  ({exe.stat().st_size / 1e6:.0f} MB, {time.time() - t0:.0f} s)")
    if IS_WINDOWS:
        stable = publish_stable(exe)
        print(f"Updated {stable}  (fixed name: point your desktop shortcut here)")
    else:
        for archive in package_linux(exe, version):
            print(f"Packed  {archive}  ({archive.stat().st_size / 1e6:.0f} MB)")
    return 0


def publish_stable(exe: Path) -> Path:
    """Copy the versioned build to dist/ModelShelf.exe.

    A running .exe can't be overwritten on Windows, but it CAN be renamed. So a
    copy that's open is moved aside (it keeps running undisturbed) and the new
    build takes its name; the leftovers are cleaned up on a later build once
    nothing uses them any more.
    """
    dist = exe.parent
    stable = dist / "ModelShelf.exe"
    for old in dist.glob("ModelShelf.old-*.exe"):
        try:
            old.unlink()
        except OSError:
            pass                                  # still running: try next time
    if stable.exists():
        try:
            stable.unlink()
        except OSError:
            aside = dist / f"ModelShelf.old-{int(time.time())}.exe"
            stable.rename(aside)
            print(f"ModelShelf.exe was running: the open copy was moved to {aside.name}")
    shutil.copy2(exe, stable)
    return stable


def package_linux(exe: Path, version: str) -> list[Path]:
    """A .tar.gz keeps the executable bit (a bare download would lose it) and
    carries the icon, menu entry and installer alongside the program."""
    dist = exe.parent
    arch = platform.machine() or "x86_64"
    stage = dist / "ModelShelf"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copy2(exe, stage / "ModelShelf")
    (stage / "ModelShelf").chmod(0o755)
    extras = ROOT / "packaging" / "linux"
    for name in ("modelshelf.desktop", "install.sh", "README.txt"):
        shutil.copy2(extras / name, stage / name)
    (stage / "install.sh").chmod(0o755)
    shutil.copy2(ROOT / "ModelShelf.png", stage / "modelshelf.png")
    for name in ("LICENSE.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, stage / name)
    versioned = dist / f"ModelShelf-{version}-linux-{arch}.tar.gz"
    with tarfile.open(versioned, "w:gz") as tar:
        tar.add(stage, arcname="ModelShelf")
    stable = dist / f"ModelShelf-linux-{arch}.tar.gz"
    shutil.copy2(versioned, stable)
    shutil.rmtree(stage, ignore_errors=True)
    return [versioned, stable]


if __name__ == "__main__":
    sys.exit(main())
