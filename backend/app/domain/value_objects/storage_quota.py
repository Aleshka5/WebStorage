from dataclasses import dataclass


@dataclass(frozen=True)
class StorageQuota:
    used_bytes: int
    limit_bytes: int

    def available_bytes(self) -> int:
        return max(0, self.limit_bytes - self.used_bytes)

    def is_exceeded(self) -> bool:
        return self.used_bytes >= self.limit_bytes
