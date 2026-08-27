from dataclasses import dataclass


@dataclass(frozen=True)
class StorageQuota:
    used_bytes: int
    limit_bytes: int

    def is_unlimited(self) -> bool:
        return self.limit_bytes <= 0

    def available_bytes(self) -> int:
        if self.is_unlimited():
            return 0
        return max(0, self.limit_bytes - self.used_bytes)

    def is_exceeded(self) -> bool:
        if self.is_unlimited():
            return False
        return self.used_bytes >= self.limit_bytes

    def would_exceed(self, additional_bytes: int) -> bool:
        if self.is_unlimited():
            return False
        return self.used_bytes + additional_bytes > self.limit_bytes
