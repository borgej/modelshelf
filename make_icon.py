"""Writes ModelShelf.ico (Windows) and ModelShelf.png (Linux menu entry) from
the same drawing the app uses for its window icon."""

import sys

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication


def main(path: str = "ModelShelf.ico") -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    from ui.icons import app_icon_pixmap
    from PIL import Image
    import io
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    app_icon_pixmap(256).save(buf, "PNG")
    img = Image.open(io.BytesIO(bytes(buf.data())))
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save("ModelShelf.png")
    print(f"wrote {path} and ModelShelf.png")


if __name__ == "__main__":
    main()
