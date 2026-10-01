"""User settings and on-disk locations. Everything stays on this machine."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "ModelShelf"


def data_dir() -> Path:
    from core.system import user_data_dir
    d = Path(os.environ.get("MODELSHELF_HOME") or user_data_dir(APP_NAME))
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbs_dir() -> Path:
    d = data_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


MATERIALS = {
    # name: density g/cm³
    "PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21,
    "PA (Nylon)": 1.14, "PC": 1.20, "Resin": 1.10,
}


@dataclass
class Settings:
    roots: list[str] = field(default_factory=list)
    theme: str = "system"                  # system | dark | light
    material: str = "PLA"
    infill: int = 15                       # percent
    wall_mm: float = 1.2                   # shell thickness used in the weight estimate
    spool_g: int = 1000
    default_color: str = "#C8CDD6"
    prefer_embedded: bool = False          # use slicer previews instead of our renders
    workers: int = 0                       # 0 = automatic
    low_priority: bool = True
    slicer_path: str = ""
    printer_url: str = ""                  # printer's web interface on the local network
    printer_name: str = "Printer"
    card_size: int = 220
    sort: str = "name"
    show_details: bool = True
    include_gcode: bool = True
    include_zip: bool = True
    include_cad: bool = True
    exclude: list[str] = field(default_factory=lambda: [".git", "node_modules", "$RECYCLE.BIN"])
    window_geometry: str = ""
    splitter_state: str = ""

    @property
    def density(self) -> float:
        return MATERIALS.get(self.material, 1.24)

    def worker_count(self) -> int:
        if self.workers > 0:
            return self.workers
        return max(2, min(12, (os.cpu_count() or 4) - 2))


from core.system import slicer_name  # noqa: E402,F401  (re-exported)


def _path() -> Path:
    return data_dir() / "settings.json"


def load() -> Settings:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(**known)
    except (OSError, ValueError, TypeError):
        return Settings()


def save(s: Settings) -> None:
    """Atomic write, retried: an antivirus scanner or backup tool briefly holding
    the file open must not silently lose the user's library folders."""
    import time
    text = json.dumps(asdict(s), indent=2)
    tmp = _path().with_suffix(".tmp")
    last: Exception | None = None
    for attempt in range(8):
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, _path())
            return
        except OSError as exc:
            last = exc
            time.sleep(0.1 * (attempt + 1))
    raise OSError(f"Could not save settings to {_path()}: {last}")


def estimate_grams(volume_mm3: float | None, area_mm2: float | None, s: Settings) -> float | None:
    """Rough filament use: solid shell + partial infill of the interior."""
    if not volume_mm3:
        return None
    shell = min(volume_mm3, (area_mm2 or 0.0) * s.wall_mm)
    core = max(0.0, volume_mm3 - shell)
    return (shell + core * s.infill / 100.0) / 1000.0 * s.density
