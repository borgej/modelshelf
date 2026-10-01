# Third-party software

ModelShelf is built on these open source projects. Each is used unmodified and
keeps its own license. The ModelShelf license (see LICENSE.md) covers only the
ModelShelf source code itself.

| Project | Used for | License |
| :-- | :-- | :-- |
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/) | User interface | LGPL-3.0 |
| [moderngl](https://github.com/moderngl/moderngl) | GPU rendering of thumbnails and the 3D view | MIT |
| [NumPy](https://numpy.org/) | Mesh maths | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | Image handling | MIT-CMU |
| [psutil](https://github.com/giampaolo/psutil) | Low priority scanning | BSD-3-Clause |
| [Send2Trash](https://github.com/arsenetar/send2trash) | Moving files to the Recycle Bin | BSD-3-Clause |
| [PyInstaller](https://pyinstaller.org/) | Packaging the Windows build | GPL-2.0 with a bootloader exception that allows distributing the packaged app |

## Qt and the LGPL

PySide6 and Qt are licensed under the GNU Lesser General Public License v3.
The Qt source code is available from <https://download.qt.io/official_releases/qt/>.
You may replace the Qt libraries used by ModelShelf with your own build: run
ModelShelf from source (see the README) against the PySide6 version of your choice.
