from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import yaml
from loguru import logger

from app.application.file_service import FileService
from app.domain.entities.file_record import FileSection
from app.domain.exceptions import FileNotFoundError, KeysValidationError, KeysYamlInvalidError
from app.domain.value_objects.error_codes import ErrorCode

KEYS_FOLDER_NAME = "Keys"
KEYS_FILENAME = "keys.yaml"
KEYS_RELATIVE_PATH = f"{KEYS_FOLDER_NAME}/{KEYS_FILENAME}"


@dataclass(frozen=True)
class KeyEntry:
    name: str
    value: str


class KeysRegistryService:
    """Read and write the private vault's flat Keys/keys.yaml mapping."""

    def __init__(self, file_service: FileService) -> None:
        self._file_service = file_service

    async def list_keys(self, user_id: UUID) -> list[KeyEntry]:
        await self._ensure_keys_folder(user_id)
        raw = await self._read_keys_file(user_id)
        if raw is None:
            await self._write_mapping(user_id, {})
            logger.info("Created empty keys.yaml for user {}", user_id)
            return []

        mapping = self._parse_mapping(raw, user_id)
        entries = [KeyEntry(name=name, value=value) for name, value in mapping.items()]
        logger.info("Loaded keys registry for user {} ({} keys)", user_id, len(entries))
        return entries

    async def save_keys(self, user_id: UUID, keys: list[KeyEntry]) -> list[KeyEntry]:
        normalized = self._validate_entries(keys)
        mapping = {entry.name: entry.value for entry in normalized}
        await self._ensure_keys_folder(user_id)
        await self._write_mapping(user_id, mapping)
        logger.info("Saved keys registry for user {} ({} keys)", user_id, len(normalized))
        return normalized

    async def _ensure_keys_folder(self, user_id: UUID) -> None:
        if await self._file_service.path_exists(KEYS_FOLDER_NAME):
            logger.info("Keys folder already existed for user {}", user_id)
            return

        await self._file_service.create_directory(user_id, "", KEYS_FOLDER_NAME)
        logger.info("Created Keys folder for user {}", user_id)

    async def _read_keys_file(self, user_id: UUID) -> bytes | None:
        if not await self._file_service.path_exists(KEYS_RELATIVE_PATH):
            logger.info("keys.yaml is missing for user {}", user_id)
            return None

        chunks: list[bytes] = []
        try:
            async for chunk in self._file_service.read_by_path(user_id, KEYS_RELATIVE_PATH):
                chunks.append(chunk)
        except FileNotFoundError:
            logger.warning(
                "keys.yaml disappeared while reading for user {}",
                user_id,
            )
            return None

        return b"".join(chunks)

    async def _write_mapping(self, user_id: UUID, mapping: dict[str, str]) -> None:
        payload = self._dump_mapping(mapping).encode("utf-8")
        await self._file_service.overwrite_file(
            user_id=user_id,
            path=KEYS_FOLDER_NAME,
            filename=KEYS_FILENAME,
            data=_bytes_iter(payload),
            size=len(payload),
            section=FileSection.PRIVATE,
        )

    @staticmethod
    def _dump_mapping(mapping: dict[str, str]) -> str:
        dumped = yaml.dump(
            mapping,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        return dumped if dumped else "{}\n"

    @staticmethod
    def _parse_mapping(raw: bytes, user_id: UUID) -> dict[str, str]:
        text = raw.decode("utf-8")
        if not text.strip():
            return {}

        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            logger.error("keys.yaml contains invalid YAML for user {}", user_id)
            raise KeysYamlInvalidError(
                "keys.yaml is not valid YAML and was not overwritten",
            ) from None

        if loaded is None:
            return {}

        if not isinstance(loaded, dict):
            logger.error("keys.yaml root is not a mapping for user {}", user_id)
            raise KeysYamlInvalidError(
                "keys.yaml must be a flat name/value mapping",
            )

        parsed: dict[str, str] = {}
        for name, value in loaded.items():
            if not isinstance(name, str) or not name:
                logger.error("keys.yaml has a non-string or empty key name for user {}", user_id)
                raise KeysYamlInvalidError(
                    "keys.yaml keys must be non-empty strings",
                )
            if isinstance(value, (dict, list)):
                logger.error("keys.yaml has a nested value for user {}", user_id)
                raise KeysYamlInvalidError(
                    "keys.yaml values must be scalars, not nested mappings or lists",
                )
            parsed[name] = "" if value is None else str(value)

        return parsed

    @staticmethod
    def _validate_entries(keys: list[KeyEntry]) -> list[KeyEntry]:
        seen: set[str] = set()
        normalized: list[KeyEntry] = []
        for entry in keys:
            name = entry.name.strip()
            value = entry.value.strip()
            if not name:
                raise KeysValidationError(
                    "Key name cannot be empty",
                    error_code=ErrorCode.KEY_NAME_EMPTY,
                )
            if not value:
                raise KeysValidationError(
                    "Key value cannot be empty",
                    error_code=ErrorCode.KEY_VALUE_EMPTY,
                )
            if name in seen:
                raise KeysValidationError(
                    "Key names must be unique",
                    error_code=ErrorCode.KEY_NAME_DUPLICATE,
                )
            seen.add(name)
            normalized.append(KeyEntry(name=name, value=value))
        return normalized


async def _bytes_iter(payload: bytes) -> AsyncIterator[bytes]:
    yield payload
