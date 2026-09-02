from dataclasses import dataclass


@dataclass(frozen=True)
class DiskVolume:
    id: str
    bucket: str
    priority: int
    is_active: bool
