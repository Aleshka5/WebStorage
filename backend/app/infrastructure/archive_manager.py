import asyncio
from pathlib import Path

import zstandard as zstd
from loguru import logger

ZSTD_COMPRESSION_LEVEL = 22
VALID_COMPRESS_MODES = frozenset({"pre_encrypt", "post_encrypt"})


class ArchiveManager:
    def compress(self, source_path: Path, dest_path: Path, compress_mode: str) -> None:
        if compress_mode not in VALID_COMPRESS_MODES:
            raise ValueError(
                f"Unsupported compress_mode {compress_mode!r}, "
                f"expected one of {sorted(VALID_COMPRESS_MODES)}",
            )

        if not source_path.is_file():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        logger.info(
            "Compressing {} to {} (mode={})",
            source_path,
            dest_path,
            compress_mode,
        )
        data = source_path.read_bytes()
        compressed = zstd.compress(data, level=ZSTD_COMPRESSION_LEVEL)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(compressed)
        logger.info(
            "Compressed {} bytes to {} bytes (mode={})",
            len(data),
            len(compressed),
            compress_mode,
        )

    def decompress(self, source_path: Path, dest_path: Path) -> None:
        if not source_path.is_file():
            raise FileNotFoundError(f"Archive file not found: {source_path}")

        logger.info("Decompressing {} to {}", source_path, dest_path)
        compressed = source_path.read_bytes()
        data = zstd.decompress(compressed)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(data)
        logger.info(
            "Decompressed {} bytes to {} bytes",
            len(compressed),
            len(data),
        )

    async def compress_async(
        self,
        source_path: Path,
        dest_path: Path,
        compress_mode: str,
    ) -> None:
        await asyncio.to_thread(self.compress, source_path, dest_path, compress_mode)

    async def decompress_async(self, source_path: Path, dest_path: Path) -> None:
        await asyncio.to_thread(self.decompress, source_path, dest_path)
