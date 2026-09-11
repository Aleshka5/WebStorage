import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import yaml
from loguru import logger

from app.application.file_service import FileService
from app.domain.entities.file_record import FileSection
from app.domain.exceptions import (
    FileNotFoundError,
    ResumeMetaInvalidError,
    ResumeNodeExistsError,
    ResumeValidationError,
)
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.storage.base_adapter import FileNode

STATUSES_FILENAME = "statuses.yaml"
VACANCY_META_FILENAME = "meta.yaml"
RESERVED_NODE_NAMES = frozenset({STATUSES_FILENAME, VACANCY_META_FILENAME})
MAX_NODE_NAME_LENGTH = 128
MAX_TREE_DEPTH = 3
DEFAULT_STATUSES: tuple[tuple[str, str], ...] = (
    ("Applied", "#38BDF8"),
    ("Interview", "#A78BFA"),
    ("Offer", "#34D399"),
    ("Rejected", "#F87171"),
)

_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_URL_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


class ResumeLevel(StrEnum):
    COUNTRY = "COUNTRY"
    COMPANY = "COMPANY"
    VACANCY = "VACANCY"


LEVEL_BY_DEPTH: tuple[ResumeLevel, ...] = (
    ResumeLevel.COUNTRY,
    ResumeLevel.COMPANY,
    ResumeLevel.VACANCY,
)


@dataclass(frozen=True)
class ResumeNode:
    name: str
    path: str
    level: ResumeLevel | None
    child_count: int
    modified_at: datetime
    status_id: str | None = None
    website_url: str | None = None


@dataclass(frozen=True)
class ResumeTree:
    path: str
    level: ResumeLevel | None
    items: list[ResumeNode]


@dataclass(frozen=True)
class VacancyField:
    name: str
    value: str


@dataclass(frozen=True)
class VacancyMeta:
    path: str
    name: str
    website_url: str
    status_id: str | None
    fields: list[VacancyField]


@dataclass(frozen=True)
class ResumeStatus:
    id: str
    name: str
    color: str


@dataclass(frozen=True)
class VacancyListItem:
    country: str
    company: str
    name: str
    path: str
    status_id: str | None
    website_url: str
    modified_at: datetime


class ResumeService:
    """Country/company/vacancy tree and its YAML metadata on top of FileService."""

    def __init__(self, file_service: FileService) -> None:
        self._file_service = file_service

    async def list_nodes(self, user_id: UUID, path: str) -> ResumeTree:
        normalized = self._normalize_path(path)
        depth = self._depth(normalized)
        await self._ensure_root(user_id)
        await self._ensure_node_exists(normalized)

        child_level = LEVEL_BY_DEPTH[depth] if depth < MAX_TREE_DEPTH else None
        items: list[ResumeNode] = []
        for node in await self._list_child_dirs(user_id, normalized):
            items.append(
                await self._build_node(
                    user_id,
                    self._join_path(normalized, node.name),
                    child_level,
                    node.modified_at,
                )
            )

        logger.info(
            "Listed {} resume nodes for user {} at path {!r} (level={})",
            len(items),
            user_id,
            normalized,
            child_level.value if child_level else None,
        )
        return ResumeTree(path=normalized, level=child_level, items=items)

    async def create_node(
        self,
        user_id: UUID,
        path: str,
        name: str,
        status_id: str | None = None,
    ) -> ResumeNode:
        parent = self._normalize_path(path)
        depth = self._depth(parent)
        if depth >= MAX_TREE_DEPTH:
            logger.warning(
                "Rejected resume create below vacancy depth for user {} at path {!r}",
                user_id,
                parent,
            )
            raise ResumeValidationError(
                "Resume tree is limited to country, company and vacancy levels",
                error_code=ErrorCode.RESUME_DEPTH_INVALID,
            )

        clean_name = self._validate_node_name(name)
        await self._ensure_root(user_id)
        await self._ensure_node_exists(parent)
        await self._ensure_unique_sibling(user_id, parent, clean_name)

        level = LEVEL_BY_DEPTH[depth]
        node_path = self._join_path(parent, clean_name)
        resolved_status_id: str | None = None
        if level == ResumeLevel.VACANCY:
            resolved_status_id = await self._resolve_status_id(user_id, status_id)

        await self._file_service.create_directory(user_id, parent, clean_name)

        if level == ResumeLevel.VACANCY:
            await self._write_meta(
                user_id,
                node_path,
                website_url="",
                status_id=resolved_status_id,
                fields=[],
            )

        logger.info(
            "Created resume node {!r} ({}) for user {}",
            node_path,
            level.value,
            user_id,
        )
        return await self._build_node(user_id, node_path, level)

    async def rename_node(self, user_id: UUID, path: str, new_name: str) -> ResumeNode:
        normalized = self._normalize_path(path)
        depth = self._depth(normalized)
        self._ensure_node_depth(user_id, normalized, depth)

        clean_name = self._validate_node_name(new_name)
        await self._ensure_node_exists(normalized, required=True)

        parent = self._parent_path(normalized)
        current_name = PurePosixPath(normalized).name
        if clean_name.lower() != current_name.lower():
            await self._ensure_unique_sibling(user_id, parent, clean_name)

        new_path = self._join_path(parent, clean_name)
        if clean_name != current_name:
            await self._file_service.rename_by_path(user_id, normalized, clean_name)

        logger.info(
            "Renamed resume node {!r} to {!r} for user {}",
            normalized,
            new_path,
            user_id,
        )
        return await self._build_node(user_id, new_path, LEVEL_BY_DEPTH[depth - 1])

    async def delete_node(self, user_id: UUID, path: str) -> None:
        normalized = self._normalize_path(path)
        depth = self._depth(normalized)
        self._ensure_node_depth(user_id, normalized, depth)

        released = await self._file_service.delete_directory_recursive(user_id, normalized)
        logger.info(
            "Deleted resume node {!r} for user {} ({} bytes released)",
            normalized,
            user_id,
            released,
        )

    async def get_vacancy(self, user_id: UUID, path: str) -> VacancyMeta:
        normalized = self._normalize_vacancy_path(user_id, path)
        await self._ensure_node_exists(normalized, required=True)

        meta = await self._read_meta(user_id, normalized)
        logger.info("Read vacancy meta {!r} for user {}", normalized, user_id)
        return meta

    async def save_vacancy(
        self,
        user_id: UUID,
        path: str,
        website_url: str | None,
        status_id: str | None,
        fields: list[VacancyField],
    ) -> VacancyMeta:
        normalized = self._normalize_vacancy_path(user_id, path)
        await self._ensure_node_exists(normalized, required=True)

        normalized_url = self._normalize_url(website_url)
        normalized_fields = self._validate_fields(fields)
        normalized_status_id = (status_id or "").strip() or None

        await self._write_meta(
            user_id,
            normalized,
            website_url=normalized_url,
            status_id=normalized_status_id,
            fields=normalized_fields,
        )
        logger.info(
            "Saved vacancy meta {!r} for user {} ({} fields)",
            normalized,
            user_id,
            len(normalized_fields),
        )
        return VacancyMeta(
            path=normalized,
            name=PurePosixPath(normalized).name,
            website_url=normalized_url,
            status_id=normalized_status_id,
            fields=normalized_fields,
        )

    async def list_all_vacancies(self, user_id: UUID) -> list[VacancyListItem]:
        """Walk the whole tree and return every vacancy with its country and company.

        Costs one listing per country and per company plus one ``meta.yaml`` read per
        vacancy — the cross-tree read ADR-010 accepted in exchange for keeping the
        hierarchy in object storage.
        """
        await self._ensure_root(user_id)

        items: list[VacancyListItem] = []
        for country in await self._list_child_dirs(user_id, ""):
            country_path = country.name
            for company in await self._list_child_dirs(user_id, country_path):
                company_path = self._join_path(country_path, company.name)
                for vacancy in await self._list_child_dirs(user_id, company_path):
                    vacancy_path = self._join_path(company_path, vacancy.name)
                    meta = await self._try_read_meta(user_id, vacancy_path)
                    items.append(
                        VacancyListItem(
                            country=country.name,
                            company=company.name,
                            name=vacancy.name,
                            path=vacancy_path,
                            status_id=meta.status_id if meta else None,
                            website_url=meta.website_url if meta else "",
                            modified_at=vacancy.modified_at,
                        )
                    )

        logger.info("Listed {} vacancies across the tree for user {}", len(items), user_id)
        return items

    async def list_statuses(self, user_id: UUID) -> list[ResumeStatus]:
        await self._ensure_root(user_id)
        raw = await self._read_file(user_id, STATUSES_FILENAME)
        if raw is None:
            seeded = [
                ResumeStatus(id=uuid4().hex, name=name, color=color)
                for name, color in DEFAULT_STATUSES
            ]
            await self._write_statuses(user_id, seeded)
            logger.info("Seeded {} resume statuses for user {}", len(seeded), user_id)
            return seeded

        statuses = self._parse_statuses(raw, user_id)
        logger.info("Loaded {} resume statuses for user {}", len(statuses), user_id)
        return statuses

    async def save_statuses(
        self,
        user_id: UUID,
        statuses: list[ResumeStatus],
    ) -> list[ResumeStatus]:
        normalized = self._validate_statuses(statuses)
        await self._ensure_root(user_id)
        await self._write_statuses(user_id, normalized)
        logger.info("Saved {} resume statuses for user {}", len(normalized), user_id)
        return normalized

    async def _build_node(
        self,
        user_id: UUID,
        node_path: str,
        level: ResumeLevel | None,
        modified_at: datetime | None = None,
    ) -> ResumeNode:
        name = PurePosixPath(node_path).name
        resolved_modified_at = modified_at or await self._modified_at(user_id, node_path)
        child_count = await self._count_children(user_id, node_path, level)

        status_id: str | None = None
        website_url: str | None = None
        if level == ResumeLevel.VACANCY:
            meta = await self._try_read_meta(user_id, node_path)
            status_id = meta.status_id if meta else None
            website_url = meta.website_url if meta else ""

        return ResumeNode(
            name=name,
            path=node_path,
            level=level,
            child_count=child_count,
            modified_at=resolved_modified_at,
            status_id=status_id,
            website_url=website_url,
        )

    async def _list_child_dirs(self, user_id: UUID, path: str) -> list[FileNode]:
        try:
            nodes = await self._file_service.list_directory(user_id, path)
        except FileNotFoundError:
            return []

        return sorted(
            (node for node in nodes if node.is_dir),
            key=lambda node: node.name.lower(),
        )

    async def _modified_at(self, user_id: UUID, node_path: str) -> datetime:
        parent = self._parent_path(node_path)
        name = PurePosixPath(node_path).name
        try:
            nodes = await self._file_service.list_directory(user_id, parent)
        except FileNotFoundError:
            return datetime.now(tz=UTC)

        for node in nodes:
            if node.is_dir and node.name == name:
                return node.modified_at
        return datetime.now(tz=UTC)

    async def _count_children(
        self,
        user_id: UUID,
        node_path: str,
        level: ResumeLevel | None,
    ) -> int:
        try:
            nodes = await self._file_service.list_directory(user_id, node_path)
        except FileNotFoundError:
            return 0

        if level == ResumeLevel.VACANCY:
            return len(
                [
                    node
                    for node in nodes
                    if node.is_dir or node.name != VACANCY_META_FILENAME
                ]
            )
        return len([node for node in nodes if node.is_dir])

    async def _ensure_root(self, user_id: UUID) -> None:
        if await self._file_service.path_exists(""):
            return

        await self._file_service.create_directory(user_id, "", "")
        logger.info("Created resumes root for user {}", user_id)

    async def _ensure_node_exists(self, path: str, *, required: bool = False) -> None:
        if not path:
            if required:
                raise FileNotFoundError("Resume node path is required")
            return

        if not await self._file_service.path_exists(path):
            raise FileNotFoundError(f"Resume node {path!r} not found")

    def _ensure_node_depth(self, user_id: UUID, path: str, depth: int) -> None:
        if 1 <= depth <= MAX_TREE_DEPTH:
            return

        logger.warning(
            "Rejected resume operation at invalid depth {} for user {} path {!r}",
            depth,
            user_id,
            path,
        )
        raise ResumeValidationError(
            "Resume tree is limited to country, company and vacancy levels",
            error_code=ErrorCode.RESUME_DEPTH_INVALID,
        )

    def _normalize_vacancy_path(self, user_id: UUID, path: str) -> str:
        normalized = self._normalize_path(path)
        if self._depth(normalized) != MAX_TREE_DEPTH:
            logger.warning(
                "Rejected vacancy operation on non-vacancy path {!r} for user {}",
                normalized,
                user_id,
            )
            raise ResumeValidationError(
                "Vacancy metadata is only available at country/company/vacancy depth",
                error_code=ErrorCode.RESUME_DEPTH_INVALID,
            )
        return normalized

    async def _ensure_unique_sibling(self, user_id: UUID, parent: str, name: str) -> None:
        try:
            nodes = await self._file_service.list_directory(user_id, parent)
        except FileNotFoundError:
            return

        lowered = name.lower()
        for node in nodes:
            if node.name.lower() == lowered:
                logger.warning(
                    "Duplicate resume node {!r} under {!r} for user {}",
                    name,
                    parent,
                    user_id,
                )
                raise ResumeNodeExistsError(
                    f"A node named {name!r} already exists at this level",
                )

    async def _resolve_status_id(self, user_id: UUID, status_id: str | None) -> str | None:
        candidate = (status_id or "").strip()
        if not candidate:
            return None

        statuses = await self.list_statuses(user_id)
        if candidate not in {status.id for status in statuses}:
            logger.warning(
                "Unknown status id {} used on create for user {}",
                candidate,
                user_id,
            )
            raise ResumeValidationError(
                "Status does not exist",
                error_code=ErrorCode.RESUME_STATUS_INVALID,
            )
        return candidate

    async def _read_file(self, user_id: UUID, relative_path: str) -> bytes | None:
        if not await self._file_service.path_exists(relative_path):
            return None

        chunks: list[bytes] = []
        try:
            async for chunk in self._file_service.read_by_path(user_id, relative_path):
                chunks.append(chunk)
        except FileNotFoundError:
            logger.warning(
                "{} disappeared while reading for user {}",
                relative_path,
                user_id,
            )
            return None

        return b"".join(chunks)

    async def _read_meta(self, user_id: UUID, vacancy_path: str) -> VacancyMeta:
        meta_path = self._join_path(vacancy_path, VACANCY_META_FILENAME)
        raw = await self._read_file(user_id, meta_path)
        name = PurePosixPath(vacancy_path).name
        if raw is None:
            logger.info("meta.yaml is missing for vacancy {!r}", vacancy_path)
            return VacancyMeta(
                path=vacancy_path,
                name=name,
                website_url="",
                status_id=None,
                fields=[],
            )

        return self._parse_meta(raw, vacancy_path, user_id)

    async def _try_read_meta(self, user_id: UUID, vacancy_path: str) -> VacancyMeta | None:
        try:
            return await self._read_meta(user_id, vacancy_path)
        except ResumeMetaInvalidError:
            logger.warning(
                "Skipping invalid meta.yaml for vacancy {!r} while listing",
                vacancy_path,
            )
            return None

    async def _write_meta(
        self,
        user_id: UUID,
        vacancy_path: str,
        *,
        website_url: str,
        status_id: str | None,
        fields: list[VacancyField],
    ) -> None:
        document = {
            "website_url": website_url,
            "status_id": status_id,
            "fields": [{"name": item.name, "value": item.value} for item in fields],
        }
        await self._write_yaml(user_id, vacancy_path, VACANCY_META_FILENAME, document)

    async def _write_statuses(self, user_id: UUID, statuses: list[ResumeStatus]) -> None:
        document = {
            "statuses": [
                {"id": status.id, "name": status.name, "color": status.color}
                for status in statuses
            ],
        }
        await self._write_yaml(user_id, "", STATUSES_FILENAME, document)

    async def _write_yaml(
        self,
        user_id: UUID,
        path: str,
        filename: str,
        document: dict,
    ) -> None:
        payload = yaml.dump(
            document,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
        await self._file_service.overwrite_file(
            user_id=user_id,
            path=path,
            filename=filename,
            data=_bytes_iter(payload),
            size=len(payload),
            section=FileSection.RESUMES,
        )

    def _parse_meta(self, raw: bytes, vacancy_path: str, user_id: UUID) -> VacancyMeta:
        name = PurePosixPath(vacancy_path).name
        loaded = self._load_yaml(raw, VACANCY_META_FILENAME, user_id)
        if loaded is None:
            return VacancyMeta(
                path=vacancy_path,
                name=name,
                website_url="",
                status_id=None,
                fields=[],
            )

        if not isinstance(loaded, dict):
            raise self._meta_invalid("meta.yaml must be a mapping", user_id)

        website_url = loaded.get("website_url")
        if website_url is None:
            website_url = ""
        if not isinstance(website_url, str):
            raise self._meta_invalid("meta.yaml website_url must be a string", user_id)

        status_id = loaded.get("status_id")
        if status_id is not None and not isinstance(status_id, str):
            raise self._meta_invalid("meta.yaml status_id must be a string", user_id)
        status_id = (status_id or "").strip() or None

        raw_fields = loaded.get("fields")
        if raw_fields is None:
            raw_fields = []
        if not isinstance(raw_fields, list):
            raise self._meta_invalid("meta.yaml fields must be a list", user_id)

        fields: list[VacancyField] = []
        for entry in raw_fields:
            if not isinstance(entry, dict):
                raise self._meta_invalid("meta.yaml fields must be mappings", user_id)
            field_name = entry.get("name")
            if not isinstance(field_name, str) or not field_name.strip():
                raise self._meta_invalid(
                    "meta.yaml field names must be non-empty strings",
                    user_id,
                )
            field_value = entry.get("value")
            if isinstance(field_value, (dict, list)):
                raise self._meta_invalid("meta.yaml field values must be scalars", user_id)
            fields.append(
                VacancyField(
                    name=field_name,
                    value="" if field_value is None else str(field_value),
                )
            )

        return VacancyMeta(
            path=vacancy_path,
            name=name,
            website_url=website_url,
            status_id=status_id,
            fields=fields,
        )

    def _parse_statuses(self, raw: bytes, user_id: UUID) -> list[ResumeStatus]:
        loaded = self._load_yaml(raw, STATUSES_FILENAME, user_id)
        if loaded is None:
            return []

        if not isinstance(loaded, dict) or "statuses" not in loaded:
            raise self._meta_invalid(
                "statuses.yaml must be a mapping with a statuses list",
                user_id,
            )

        raw_statuses = loaded.get("statuses")
        if raw_statuses is None:
            return []
        if not isinstance(raw_statuses, list):
            raise self._meta_invalid("statuses.yaml statuses must be a list", user_id)

        statuses: list[ResumeStatus] = []
        for entry in raw_statuses:
            if not isinstance(entry, dict):
                raise self._meta_invalid("statuses.yaml entries must be mappings", user_id)
            status_id = entry.get("id")
            name = entry.get("name")
            color = entry.get("color")
            if not isinstance(status_id, str) or not status_id.strip():
                raise self._meta_invalid(
                    "statuses.yaml ids must be non-empty strings",
                    user_id,
                )
            if not isinstance(name, str) or not name.strip():
                raise self._meta_invalid(
                    "statuses.yaml names must be non-empty strings",
                    user_id,
                )
            if not isinstance(color, str) or not _COLOR_PATTERN.match(color):
                raise self._meta_invalid(
                    "statuses.yaml colors must look like #RRGGBB",
                    user_id,
                )
            statuses.append(ResumeStatus(id=status_id, name=name, color=color))

        return statuses

    @staticmethod
    def _load_yaml(raw: bytes, filename: str, user_id: UUID) -> object | None:
        text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            return None

        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            logger.error("{} contains invalid YAML for user {}", filename, user_id)
            raise ResumeMetaInvalidError(
                f"{filename} is not valid YAML and was not overwritten",
            ) from None

    @staticmethod
    def _meta_invalid(message: str, user_id: UUID) -> ResumeMetaInvalidError:
        logger.error("Resumes YAML rejected for user {}: {}", user_id, message)
        return ResumeMetaInvalidError(message)

    @staticmethod
    def _validate_node_name(name: str) -> str:
        clean = name.strip()
        if (
            not clean
            or len(clean) > MAX_NODE_NAME_LENGTH
            or "/" in clean
            or "\\" in clean
            or clean in (".", "..")
            or clean.lower() in RESERVED_NODE_NAMES
        ):
            raise ResumeValidationError(
                "Node name must be 1-128 characters, without slashes or reserved names",
                error_code=ErrorCode.RESUME_NAME_INVALID,
            )
        return clean

    @staticmethod
    def _validate_fields(fields: list[VacancyField]) -> list[VacancyField]:
        seen: set[str] = set()
        normalized: list[VacancyField] = []
        for entry in fields:
            name = entry.name.strip()
            if not name:
                raise ResumeValidationError(
                    "Vacancy field name cannot be empty",
                    error_code=ErrorCode.RESUME_FIELD_INVALID,
                )
            if name.lower() in seen:
                raise ResumeValidationError(
                    "Vacancy field names must be unique",
                    error_code=ErrorCode.RESUME_FIELD_INVALID,
                )
            seen.add(name.lower())
            normalized.append(VacancyField(name=name, value=(entry.value or "").strip()))
        return normalized

    @staticmethod
    def _validate_statuses(statuses: list[ResumeStatus]) -> list[ResumeStatus]:
        seen_names: set[str] = set()
        seen_ids: set[str] = set()
        normalized: list[ResumeStatus] = []
        for entry in statuses:
            name = entry.name.strip()
            color = entry.color.strip()
            status_id = (entry.id or "").strip() or uuid4().hex
            if not name:
                raise ResumeValidationError(
                    "Status name cannot be empty",
                    error_code=ErrorCode.RESUME_STATUS_INVALID,
                )
            if name.lower() in seen_names:
                raise ResumeValidationError(
                    "Status names must be unique",
                    error_code=ErrorCode.RESUME_STATUS_INVALID,
                )
            if not _COLOR_PATTERN.match(color):
                raise ResumeValidationError(
                    "Status color must look like #RRGGBB",
                    error_code=ErrorCode.RESUME_STATUS_INVALID,
                )
            if status_id in seen_ids:
                raise ResumeValidationError(
                    "Status ids must be unique",
                    error_code=ErrorCode.RESUME_STATUS_INVALID,
                )
            seen_names.add(name.lower())
            seen_ids.add(status_id)
            normalized.append(ResumeStatus(id=status_id, name=name, color=color))
        return normalized

    @staticmethod
    def _normalize_url(website_url: str | None) -> str:
        url = (website_url or "").strip()
        if not url:
            return ""
        if _URL_SCHEME_PATTERN.match(url):
            return url
        return f"https://{url}"

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.strip().replace("\\", "/").strip("/")

    @staticmethod
    def _depth(normalized_path: str) -> int:
        if not normalized_path:
            return 0
        return len(normalized_path.split("/"))

    @staticmethod
    def _parent_path(normalized_path: str) -> str:
        if "/" not in normalized_path:
            return ""
        return normalized_path.rsplit("/", 1)[0]

    @staticmethod
    def _join_path(directory: str, name: str) -> str:
        if directory and name:
            return f"{directory}/{name}"
        return name or directory


async def _bytes_iter(payload: bytes) -> AsyncIterator[bytes]:
    yield payload
