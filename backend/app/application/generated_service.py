import math
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import yaml
from loguru import logger

from app.application.file_service import FileService
from app.domain.entities.file_record import FileSection
from app.domain.exceptions import (
    FileNotFoundError,
    GeneratedMetaInvalidError,
    GeneratedValidationError,
    QuotaExceededError,
)
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.storage_quota import StorageQuota
from app.infrastructure.database.repositories.quota_repo import QuotaRepository

META_FILENAME = "meta.yaml"
RESULT_FILENAME = "result.png"
MAX_REFERENCE_COUNT = 10
RUN_ID_SUFFIX_HEX_CHARS = 8
RUN_ID_ALLOCATION_ATTEMPTS = 5

_RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
_REFERENCE_NAME_PATTERN = re.compile(r"^ref-([1-9][0-9]*)\.png$")
_CREATED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class StoredGeneratedRun:
    id: str
    created_at: str


@dataclass(frozen=True)
class GeneratedListItem:
    id: str
    created_at: str
    prompt: str


@dataclass(frozen=True)
class GeneratedMeta:
    prompt: str
    negative_prompt: str
    seed: int
    steps: int
    true_cfg_scale: float
    width: int
    height: int
    duration: float
    created_at: str
    references: list[str]
    result: str


class _QuotedStringDumper(yaml.SafeDumper):
    """Quote every string so values such as ``yes`` stay strings in meta.yaml."""


def _represent_quoted_str(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


_QuotedStringDumper.add_representer(str, _represent_quoted_str)


class GeneratedService:
    """Stores one generated run as a directory plus meta.yaml on FileService.

    This service does not call the image generator. Bytes are plain and count
    toward the user's total quota.
    """

    def __init__(
        self,
        file_service: FileService,
        quota_repo: QuotaRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._file_service = file_service
        self._quota_repo = quota_repo
        self._clock = clock or (lambda: datetime.now(UTC))

    async def store_run(
        self,
        user_id: UUID,
        *,
        prompt: str | None,
        negative_prompt: str | None,
        seed: str | None,
        steps: str | None,
        true_cfg_scale: str | None,
        width: str | None,
        height: str | None,
        duration: str | None,
        references: list[bytes],
        result: bytes | None,
    ) -> StoredGeneratedRun:
        parsed_prompt = self._require_prompt(user_id, prompt)
        parsed_negative = negative_prompt if negative_prompt is not None else ""
        parsed_seed = self._require_int(user_id, seed, "seed")
        parsed_steps = self._require_int(user_id, steps, "steps")
        parsed_cfg = self._require_float(user_id, true_cfg_scale, "true_cfg_scale")
        parsed_width = self._require_int(user_id, width, "width")
        parsed_height = self._require_int(user_id, height, "height")
        parsed_duration = self._require_float(user_id, duration, "duration")
        if result is None:
            raise self._invalid(user_id, "result image is required")
        self._require_reference_count(user_id, len(references))

        created_at = self._now()
        created_at_text = self._format_created_at(created_at)
        reference_names = [f"ref-{index}.png" for index in range(1, len(references) + 1)]
        meta_bytes = self._render_meta(
            prompt=parsed_prompt,
            negative_prompt=parsed_negative,
            seed=parsed_seed,
            steps=parsed_steps,
            true_cfg_scale=parsed_cfg,
            width=parsed_width,
            height=parsed_height,
            duration=parsed_duration,
            created_at=created_at_text,
            references=reference_names,
        )
        total_bytes = len(meta_bytes) + sum(len(item) for item in references) + len(result)
        await self._ensure_quota(user_id, total_bytes)

        run_id = await self._allocate_run_id(user_id, created_at)
        logger.info(
            "Storing generated run {} for user {} ({} bytes, {} references)",
            run_id,
            user_id,
            total_bytes,
            len(references),
        )
        await self._file_service.create_directory(user_id, "", run_id)
        try:
            await self._write_bytes(user_id, run_id, META_FILENAME, meta_bytes)
            for name, payload in zip(reference_names, references, strict=True):
                await self._write_bytes(user_id, run_id, name, payload)
            await self._write_bytes(user_id, run_id, RESULT_FILENAME, result)
        except Exception:
            logger.exception(
                "Failed to store generated run {} for user {}, removing partial directory",
                run_id,
                user_id,
            )
            await self._remove_partial_run(user_id, run_id)
            raise

        logger.info("Stored generated run {} for user {}", run_id, user_id)
        return StoredGeneratedRun(id=run_id, created_at=created_at_text)

    async def list_runs(self, user_id: UUID) -> list[GeneratedListItem]:
        nodes = await self._file_service.list_directory(user_id, "")
        items: list[tuple[datetime, GeneratedListItem]] = []
        for node in nodes:
            if not node.is_dir or node.name.startswith("."):
                continue
            if _RUN_ID_PATTERN.fullmatch(node.name) is None:
                logger.warning(
                    "Skipping generated directory {!r} for user {} because its name is not a run id",
                    node.name,
                    user_id,
                )
                continue
            meta = await self._try_read_meta(user_id, node.name)
            if meta is None:
                continue
            created_at = self._parse_created_at(meta.created_at, user_id, node.name)
            items.append(
                (
                    created_at,
                    GeneratedListItem(
                        id=node.name,
                        created_at=meta.created_at,
                        prompt=meta.prompt,
                    ),
                )
            )

        items.sort(key=lambda item: item[0], reverse=True)
        listed = [item for _, item in items]
        logger.info("Listed {} generated runs for user {}", len(listed), user_id)
        return listed

    async def get_run(self, user_id: UUID, run_id: str) -> GeneratedMeta:
        self._ensure_known_run(user_id, run_id)
        if not await self._file_service.path_exists(run_id):
            logger.warning("Generated run {} not found for user {}", run_id, user_id)
            raise FileNotFoundError(f"Generated run {run_id} not found")

        meta = await self._read_meta(user_id, run_id)
        logger.info("Read generated run {} for user {}", run_id, user_id)
        return meta

    async def prepare_file(self, user_id: UUID, run_id: str, name: str) -> str:
        """Return the section-relative path of a listed image, or raise if it is not listed."""
        self._ensure_known_run(user_id, run_id)
        if not await self._file_service.path_exists(run_id):
            logger.warning("Generated run {} not found for user {}", run_id, user_id)
            raise FileNotFoundError(f"Generated run {run_id} not found")

        allowed = await self._listed_file_names(user_id, run_id)
        if name not in allowed:
            logger.warning(
                "Rejected unlisted generated file {!r} for user {} run {}",
                name,
                user_id,
                run_id,
            )
            raise FileNotFoundError(f"Generated file {name!r} not found")

        relative_path = f"{run_id}/{name}"
        if not await self._file_service.readable_object_exists(user_id, relative_path):
            logger.warning(
                "Listed generated file {!r} is missing for user {} run {}",
                name,
                user_id,
                run_id,
            )
            raise FileNotFoundError(f"Generated file {name!r} not found")

        logger.info("Streaming generated file {!r} for user {} run {}", name, user_id, run_id)
        return relative_path

    def iter_file(self, user_id: UUID, relative_path: str) -> AsyncIterator[bytes]:
        return self._file_service.read_by_path(user_id, relative_path)

    async def delete_run(self, user_id: UUID, run_id: str) -> None:
        self._ensure_known_run(user_id, run_id)
        released = await self._file_service.delete_directory_recursive(user_id, run_id)
        logger.info(
            "Deleted generated run {} for user {} ({} bytes released)",
            run_id,
            user_id,
            released,
        )

    async def _listed_file_names(self, user_id: UUID, run_id: str) -> frozenset[str]:
        try:
            meta = await self._read_meta(user_id, run_id)
        except GeneratedMetaInvalidError:
            logger.warning(
                "Cannot list files for generated run {} of user {} because meta.yaml is unreadable",
                run_id,
                user_id,
            )
            return frozenset()
        return frozenset((meta.result, *meta.references))

    async def _try_read_meta(self, user_id: UUID, run_id: str) -> GeneratedMeta | None:
        try:
            return await self._read_meta(user_id, run_id)
        except GeneratedMetaInvalidError:
            logger.warning(
                "Skipping generated run {} for user {} because meta.yaml is unreadable",
                run_id,
                user_id,
            )
            return None

    async def _read_meta(self, user_id: UUID, run_id: str) -> GeneratedMeta:
        meta_path = f"{run_id}/{META_FILENAME}"
        raw = await self._read_bytes(user_id, meta_path)
        if raw is None:
            raise self._meta_invalid(user_id, run_id, "meta.yaml is missing")
        return self._parse_meta(raw, user_id, run_id)

    async def _read_bytes(self, user_id: UUID, relative_path: str) -> bytes | None:
        chunks: list[bytes] = []
        try:
            async for chunk in self._file_service.read_by_path(user_id, relative_path):
                chunks.append(chunk)
        except FileNotFoundError:
            logger.warning(
                "Generated file {} disappeared while reading for user {}",
                relative_path,
                user_id,
            )
            return None
        return b"".join(chunks)

    def _parse_meta(self, raw: bytes, user_id: UUID, run_id: str) -> GeneratedMeta:
        text = raw.decode("utf-8", errors="replace")
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            logger.error(
                "generated meta.yaml contains invalid YAML for user {} run {}",
                user_id,
                run_id,
            )
            raise GeneratedMetaInvalidError(
                "meta.yaml is not valid YAML and was not overwritten",
            ) from None

        if not isinstance(loaded, dict):
            raise self._meta_invalid(user_id, run_id, "meta.yaml must be a mapping")

        prompt = loaded.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise self._meta_invalid(user_id, run_id, "meta.yaml prompt must be a non-empty string")

        negative_prompt = loaded.get("negative_prompt")
        if not isinstance(negative_prompt, str):
            raise self._meta_invalid(user_id, run_id, "meta.yaml negative_prompt must be a string")

        created_at = loaded.get("created_at")
        if not isinstance(created_at, str) or _CREATED_AT_PATTERN.fullmatch(created_at) is None:
            raise self._meta_invalid(user_id, run_id, "meta.yaml created_at is not a UTC timestamp")
        self._parse_created_at(created_at, user_id, run_id)

        references = self._parse_references(loaded.get("references"), user_id, run_id)
        result = loaded.get("result")
        if result != RESULT_FILENAME:
            raise self._meta_invalid(user_id, run_id, "meta.yaml result must be result.png")

        return GeneratedMeta(
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=self._parse_meta_int(loaded.get("seed"), "seed", user_id, run_id),
            steps=self._parse_meta_int(loaded.get("steps"), "steps", user_id, run_id),
            true_cfg_scale=self._parse_meta_float(
                loaded.get("true_cfg_scale"),
                "true_cfg_scale",
                user_id,
                run_id,
            ),
            width=self._parse_meta_int(loaded.get("width"), "width", user_id, run_id),
            height=self._parse_meta_int(loaded.get("height"), "height", user_id, run_id),
            duration=self._parse_meta_float(loaded.get("duration"), "duration", user_id, run_id),
            created_at=created_at,
            references=references,
            result=result,
        )

    def _parse_references(self, raw: object, user_id: UUID, run_id: str) -> list[str]:
        if not isinstance(raw, list):
            raise self._meta_invalid(user_id, run_id, "meta.yaml references must be a list")

        names: list[str] = []
        seen: set[str] = set()
        for entry in raw:
            if not isinstance(entry, str) or _REFERENCE_NAME_PATTERN.fullmatch(entry) is None:
                raise self._meta_invalid(
                    user_id,
                    run_id,
                    "meta.yaml references must be ref-N.png names",
                )
            if entry in seen:
                raise self._meta_invalid(user_id, run_id, "meta.yaml references must be unique")
            seen.add(entry)
            names.append(entry)
        return names

    def _parse_meta_int(self, value: object, field_name: str, user_id: UUID, run_id: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._meta_invalid(user_id, run_id, f"meta.yaml {field_name} must be an integer")
        return value

    def _parse_meta_float(
        self,
        value: object,
        field_name: str,
        user_id: UUID,
        run_id: str,
    ) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self._meta_invalid(user_id, run_id, f"meta.yaml {field_name} must be a number")
        number = float(value)
        if not math.isfinite(number):
            raise self._meta_invalid(user_id, run_id, f"meta.yaml {field_name} must be finite")
        return number

    def _parse_created_at(self, value: str, user_id: UUID, run_id: str) -> datetime:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            raise self._meta_invalid(user_id, run_id, "meta.yaml created_at is not a UTC timestamp") from None
        return parsed

    @staticmethod
    def _meta_invalid(user_id: UUID, run_id: str, message: str) -> GeneratedMetaInvalidError:
        logger.error(
            "Generated meta.yaml rejected for user {} run {}: {}",
            user_id,
            run_id,
            message,
        )
        return GeneratedMetaInvalidError(message)

    async def _write_bytes(self, user_id: UUID, run_id: str, filename: str, payload: bytes) -> None:
        await self._file_service.upload_file(
            user_id=user_id,
            path=run_id,
            filename=filename,
            data=_bytes_iter(payload),
            size=len(payload),
            section=FileSection.GENERATED,
        )

    async def _remove_partial_run(self, user_id: UUID, run_id: str) -> None:
        try:
            await self._file_service.delete_directory_recursive(user_id, run_id)
        except Exception:
            logger.exception(
                "Failed to remove partial generated run {} for user {}",
                run_id,
                user_id,
            )

    async def _ensure_quota(self, user_id: UUID, total_bytes: int) -> None:
        usage = await self._quota_repo.get_by_user_id(user_id)
        quota = StorageQuota(used_bytes=usage.total_bytes, limit_bytes=usage.limit_bytes)
        if quota.would_exceed(total_bytes):
            available = quota.available_bytes()
            logger.warning(
                "Generated run exceeds quota for user {}: used={}, limit={}, requested={}",
                user_id,
                usage.total_bytes,
                usage.limit_bytes,
                total_bytes,
            )
            raise QuotaExceededError(
                f"Generated run size {total_bytes} bytes exceeds available quota "
                f"({available} bytes remaining)",
                available_bytes=available,
            )

    async def _allocate_run_id(self, user_id: UUID, created_at: datetime) -> str:
        stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        for _ in range(RUN_ID_ALLOCATION_ATTEMPTS):
            run_id = f"{stamp}-{uuid4().hex[:RUN_ID_SUFFIX_HEX_CHARS]}"
            if not await self._file_service.path_exists(run_id):
                return run_id
        logger.error("Could not allocate a generated run id for user {}", user_id)
        raise GeneratedValidationError(
            "Could not allocate a generated run id",
            error_code=ErrorCode.GENERATED_INVALID,
        )

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return current.astimezone(UTC).replace(microsecond=0)

    @staticmethod
    def _format_created_at(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _require_prompt(self, user_id: UUID, prompt: str | None) -> str:
        if prompt is None or not prompt.strip():
            raise self._invalid(user_id, "prompt is required")
        return prompt

    def _require_reference_count(self, user_id: UUID, count: int) -> None:
        if count > MAX_REFERENCE_COUNT:
            raise self._invalid(
                user_id,
                f"at most {MAX_REFERENCE_COUNT} reference images are allowed",
            )

    def _require_int(self, user_id: UUID, raw: str | None, field_name: str) -> int:
        text = (raw or "").strip()
        if not text:
            raise self._invalid(user_id, f"{field_name} is required")
        try:
            if any(character in text for character in ".eE"):
                number = float(text)
                if not math.isfinite(number) or not number.is_integer():
                    raise ValueError
                return int(number)
            return int(text)
        except ValueError:
            raise self._invalid(user_id, f"{field_name} must be an integer") from None

    def _require_float(self, user_id: UUID, raw: str | None, field_name: str) -> float:
        text = (raw or "").strip()
        if not text:
            raise self._invalid(user_id, f"{field_name} is required")
        try:
            number = float(text)
        except ValueError:
            raise self._invalid(user_id, f"{field_name} must be a number") from None
        if not math.isfinite(number):
            raise self._invalid(user_id, f"{field_name} must be a number")
        return number

    @staticmethod
    def _invalid(user_id: UUID, message: str) -> GeneratedValidationError:
        logger.warning("Rejected generated run for user {}: {}", user_id, message)
        return GeneratedValidationError(message, error_code=ErrorCode.GENERATED_INVALID)

    def _ensure_known_run(self, user_id: UUID, run_id: str) -> None:
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            logger.warning("Rejected unknown generated run id for user {}", user_id)
            raise FileNotFoundError("Generated run not found")

    @staticmethod
    def _render_meta(
        *,
        prompt: str,
        negative_prompt: str,
        seed: int,
        steps: int,
        true_cfg_scale: float,
        width: int,
        height: int,
        duration: float,
        created_at: str,
        references: list[str],
    ) -> bytes:
        document = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "steps": steps,
            "true_cfg_scale": true_cfg_scale,
            "width": width,
            "height": height,
            "duration": duration,
            "created_at": created_at,
            "references": references,
            "result": RESULT_FILENAME,
        }
        payload = yaml.dump(
            document,
            Dumper=_QuotedStringDumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        return payload.encode("utf-8")


async def _bytes_iter(payload: bytes) -> AsyncIterator[bytes]:
    yield payload
