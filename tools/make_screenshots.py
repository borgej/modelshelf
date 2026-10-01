r"""Regenerates the README screenshots from a folder of models.

    python tools/make_screenshots.py "D:\path\to\some models"

Uses its own throw-away data folder (never your real library) and keeps the
window off screen. Output goes to docs/screenshots/.
"""

import multiprocessing
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "screenshots"


def main(library: str) -> None:
    home = Path(tempfile.gettempdir()) / "modelshelf-screenshots"
    os.environ["MODELSHELF_HOME"] = str(home)
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from core.system import prepare_qt
    prepare_qt()
    app = QApplication(sys.argv)

    from core.settings import Settings, save
    from ui import theme
    from ui.dialogs import DuplicatesDialog, SettingsDialog
    from ui.main_window import MainWindow

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    s = Settings(roots=[os.path.normpath(library)], theme="dark", printer_url="http://printer.local",
                 printer_name="Printer", card_size=205, sort="designer")
    save(s)
    tm = theme.ThemeManager(app, "dark")
    tm.apply()
    w = MainWindow(s, tm)
    w._on_theme()
    w.setAttribute(Qt.WA_DontShowOnScreen)
    w.resize(1500, 920)
    w.show()
    while w.scan is None:
        wait(100)
    while w.scan is not None:
        wait(200)

    def find(text):
        return next(i for i in w.model.items.values() if text.lower() in i.name.lower())

    # A lived-in library: favourites, a print queue, tags and a note.
    marks = [("Line+Art", dict(favorite=True, tags=["office", "shelf"], status="printed",
                               notes="Printed in PETG, 0.2 mm, 3 walls. Fits a 16 mm shelf.")),
             ("Sushi", dict(status="to_print", tags=["kitchen"])),
             ("Battery+Box.", dict(status="to_print", tags=["workshop", "storage"])),
             ("Filament+Snag", dict(favorite=True, tags=["printer upgrade"])),
             ("Garbage_Bag", dict(status="printed", tags=["kitchen"])),
             ("Cable_organizer", dict(tags=["office"])),
             ("DeWalt", dict(tags=["workshop"], favorite=True))]
    for text, fields in marks:
        try:
            w._user_changed([find(text).id], fields)
        except StopIteration:
            pass
    w._rebuild_sidebar()
    wait(300)

    def select(text):
        it = find(text)
        row = w.model.row_of(it.id)
        w.grid.setCurrentIndex(w.model.index(row))
        w.grid.scrollToTop()
        for _ in range(60):
            wait(100)
            if w.detail.viewer.mesh is not None and w.detail.viewer.vao is not None:
                break
        wait(700)

    def shot(widget, name):
        OUT.mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(OUT / name))
        print("wrote", name)

    select("Line+Art")
    shot(w, "library-dark.png")

    s.theme = "light"
    tm.set_preference("light")
    wait(400)
    gcode = next(i for i in w.model.items.values() if i.kind == "GCODE" and "spice" in i.name.lower())
    w.grid.setCurrentIndex(w.model.index(w.model.row_of(gcode.id)))
    for _ in range(80):
        wait(100)
        if w.detail.viewer.vao is not None:
            break
    wait(700)
    shot(w, "library-light.png")

    w.search.setText("holder")
    wait(600)
    w.grid.setCurrentIndex(w.model.index(0))
    wait(1500)
    shot(w, "search.png")
    w.search.clear()
    wait(400)

    dlg = DuplicatesDialog(list(w.model.items.values()), set(), w.thumbs, w)
    dlg.setAttribute(Qt.WA_DontShowOnScreen)
    dlg.resize(900, 520)
    dlg.show()
    wait(800)
    dlg._build()
    dlg._select_extras()
    wait(300)
    shot(dlg, "duplicates.png")
    dlg.close()

    s.theme = "dark"
    tm.set_preference("dark")
    wait(300)
    sd = SettingsDialog(s, w)
    sd.setAttribute(Qt.WA_DontShowOnScreen)
    sd.resize(640, 900)
    sd.show()
    wait(500)
    shot(sd, "settings.png")
    sd.close()
    w.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
