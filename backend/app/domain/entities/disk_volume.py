from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiskVolume:
    id: str
    mount_path: Path
    priority: int
    is_active: bool
