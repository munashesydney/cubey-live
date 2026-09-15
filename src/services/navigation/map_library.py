"""Native Nav2 / SLAM Toolbox saved-map discovery.

The web UI must operate on the same artifacts that the ROS explorer writes:
``.pgm``/``.yaml`` for occupancy-map viewing and ``.posegraph``/``.data`` for
SLAM Toolbox restoration.  This module deliberately does not know about the
old SQLite display-grid cache.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_MAP_DIR = "/home/cubey/Desktop/cubey-live/data/maps"
_MAP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class NativeMap:
    """A saved map whose base path is safe to pass to SLAM Toolbox."""

    map_id: str
    base_path: Path
    modified_at: datetime
    resolution_cm: Optional[float]
    has_image: bool
    has_metadata: bool
    loadable: bool

    @property
    def display_name(self) -> str:
        return self.map_id.replace("_", " ")

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.map_id,
            "name": self.display_name,
            "resolution_cm": self.resolution_cm,
            "updated_at": self.modified_at.isoformat(),
            "has_image": self.has_image,
            "has_metadata": self.has_metadata,
            "loadable": self.loadable,
        }


class NativeMapLibrary:
    """Restrict saved-map operations to a single trusted directory."""

    def __init__(self, map_dir: Optional[os.PathLike[str] | str] = None):
        configured_dir = map_dir or os.environ.get("CUBEY_MAP_SAVE_DIR") or DEFAULT_MAP_DIR
        self.map_dir = Path(configured_dir).expanduser().resolve()

    @staticmethod
    def _is_valid_id(map_id: str) -> bool:
        return bool(_MAP_ID_RE.fullmatch(map_id))

    def _base_path(self, map_id: str) -> Optional[Path]:
        if not self._is_valid_id(map_id):
            return None
        candidate = (self.map_dir / map_id).resolve()
        try:
            candidate.relative_to(self.map_dir)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _yaml_resolution_cm(path: Path) -> Optional[float]:
        """Read only the simple scalar we need, without adding a YAML dependency."""
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "resolution":
                    return round(float(value.strip()) * 100.0, 2)
        except (OSError, UnicodeDecodeError, ValueError):
            pass
        return None

    def get(self, map_id: str) -> Optional[NativeMap]:
        base = self._base_path(map_id)
        if base is None:
            return None

        # Append extensions rather than Path.with_suffix(): a legitimate map
        # ID may itself contain a dot and must round-trip unchanged.
        posegraph = Path(f"{base}.posegraph")
        if not posegraph.is_file():
            return None
        data = Path(f"{base}.data")
        yaml_file = Path(f"{base}.yaml")
        pgm_file = Path(f"{base}.pgm")
        session_file = Path(f"{base}.session.json")
        modified_at = datetime.fromtimestamp(posegraph.stat().st_mtime, tz=timezone.utc)
        return NativeMap(
            map_id=map_id,
            base_path=base,
            modified_at=modified_at,
            resolution_cm=self._yaml_resolution_cm(yaml_file) if yaml_file.is_file() else None,
            has_image=pgm_file.is_file() and yaml_file.is_file(),
            has_metadata=session_file.is_file(),
            loadable=data.is_file(),
        )

    def list(self) -> List[NativeMap]:
        if not self.map_dir.is_dir():
            return []
        maps = [
            native_map
            for posegraph in self.map_dir.glob("*.posegraph")
            if (native_map := self.get(posegraph.stem)) is not None
        ]
        return sorted(maps, key=lambda native_map: native_map.modified_at, reverse=True)


_SHARED_LIBRARY: Optional[NativeMapLibrary] = None


def get_native_map_library() -> NativeMapLibrary:
    global _SHARED_LIBRARY
    if _SHARED_LIBRARY is None:
        _SHARED_LIBRARY = NativeMapLibrary()
    return _SHARED_LIBRARY
