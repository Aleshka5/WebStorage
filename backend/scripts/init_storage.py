from pathlib import Path

from loguru import logger

from config import get_settings

STORAGE_SUBDIRS = ("users", "shared", "_meta/backups")


def init_storage() -> None:
    settings = get_settings()
    root = Path(settings.storage.root)
    disk_ids = [disk_id.strip() for disk_id in settings.storage.disks.split(",") if disk_id.strip()]

    if not disk_ids:
        logger.error("STORAGE_DISKS must contain at least one disk identifier")
        raise SystemExit(1)

    for disk_id in disk_ids:
        disk_root = root / disk_id
        for subdir in STORAGE_SUBDIRS:
            target = disk_root / subdir
            target.mkdir(parents=True, exist_ok=True)
            logger.info("Ensured storage directory exists: {}", target)

    logger.info("Storage initialization completed for {} disk(s)", len(disk_ids))


def main() -> None:
    init_storage()


if __name__ == "__main__":
    main()
