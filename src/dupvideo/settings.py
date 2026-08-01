"""Window and scan settings, persisted between launches."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .cache import default_dir

FILE_NAME = "settings.json"
MAX_REMEMBERED_FOLDERS = 12


@dataclass
class Settings:
    folders: list[str] = field(default_factory=list)
    recursive: bool = True
    threshold: int = 90
    auto_choice: str = ""
    use_cache: bool = True
    geometry: str = ""
    sash: int = 0

    @property
    def path(self) -> str:
        return os.path.join(default_dir(), FILE_NAME)

    @classmethod
    def load(cls, path: str | None = None) -> Settings:
        """Read saved settings.  Anything missing or malformed → defaults."""
        target = path or os.path.join(default_dir(), FILE_NAME)
        settings = cls()
        try:
            with open(target, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return settings
        if not isinstance(data, dict):
            return settings

        # Read field by field so a hand-edited or older file can never inject
        # a wrong type into the UI; unknown keys are simply ignored.
        folders = data.get("folders")
        if isinstance(folders, list):
            settings.folders = [f for f in folders if isinstance(f, str)]
        for name in ("recursive", "use_cache"):
            value = data.get(name)
            if isinstance(value, bool):
                setattr(settings, name, value)
        for name in ("threshold", "sash"):
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(settings, name, value)
        for name in ("auto_choice", "geometry"):
            value = data.get(name)
            if isinstance(value, str):
                setattr(settings, name, value)

        settings.threshold = min(100, max(70, settings.threshold))
        settings.folders = settings.folders[:MAX_REMEMBERED_FOLDERS]
        return settings

    def save(self, path: str | None = None) -> None:
        """Write settings.  Never raises - failing to save is not worth a crash."""
        target = path or self.path
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2)
            os.replace(tmp, target)           # atomic; never a half-written file
        except OSError:
            pass
