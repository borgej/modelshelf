"""Builds ModelShelf into a single stand-alone .exe.

    python build_exe.py            # normal build (bumps the patch version)
    python build_exe.py --console  # keep a console window for debugging
    python build_exe.py --no-bump  # rebuild the same version
    python build_exe.py --clean    # clear PyInstaller's cache first

The version is part of the file name (dist/ModelShelf-0.1.3.exe) on purpose:
a new build never overwrites a copy that is running.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
VERSION_FILE = ROOT / "version.py"


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
    exe = ROOT / "dist" / f"ModelShelf-{version}.exe"
    print(f"\nBuilt {exe}  ({exe.stat().st_size / 1e6:.0f} MB, {time.time() - t0:.0f} s)")
    stable = publish_stable(exe)
    print(f"Updated {stable}  (fixed name — point your desktop shortcut here)")
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
            pass                                  # still running — try next time
    if stable.exists():
        try:
            stable.unlink()
        except OSError:
            aside = dist / f"ModelShelf.old-{int(time.time())}.exe"
            stable.rename(aside)
            print(f"ModelShelf.exe was running — the open copy was moved to {aside.name}")
    shutil.copy2(exe, stable)
    return stable


if __name__ == "__main__":
    sys.exit(main())
