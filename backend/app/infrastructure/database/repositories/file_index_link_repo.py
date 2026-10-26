from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from loguru import logger
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.index_links import (
    UNSET,
    AdminIndexSnapshot,
    BackfillCursor,
    IndexErrorCount,
    IndexLink,
    IndexLinkRepository,
    UnsetType,
    UserIndexCounts,
)
from app.domain.entities.file_record import FileRecord as FileRecordEntity
from app.domain.entities.file_record import FileSection
from app.domain.indexing import (
    DUE_INDEX_STATES,
    IndexLinkState,
    IndexSkipCode,
    PrivateIndexRefused,
    RegistrationDecision,
    RegistrationKind,
)
from app.infrastructure.database.models import FileIndexLink
from app.infrastructure.database.models import FileRecord as FileRecordModel
from app.infrastructure.database.models import FileSection as FileSectionModel
from app.infrastructure.database.models import FileStatus as FileStatusModel
from app.infrastructure.database.repositories.file_repo import FileRepository

_INDEXED_SECTIONS = frozenset({FileSection.FILES, FileSection.PHOTOS})
_DUE_STATE_VALUES = tuple(state.value for state in DUE_INDEX_STATES)
_PRIVATE_REFUSAL = "Private objects are never indexed"


class FileIndexLinkRepository(IndexLinkRepository):
    """SQLAlchemy queue for ``file_index_links``. The caller owns ``commit``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_pending(
        self,
        record: FileRecordEntity,
        decision: RegistrationDecision,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        self._refuse_record(record)
        if self._is_excluded(record, decision):
            logger.warning(
                "Index link not created for excluded section {} file_record_id {}",
                record.section.value,
                record.id,
            )
            return None

        existing = await self._model_by_file_record_id(record.id)
        if existing is not None:
            self._refuse_model(existing)
            logger.warning(
                "Index link already exists for file_record_id {}",
                record.id,
            )
            return self._to_link(existing)

        state, error_code = _insert_state(decision)
        moment = _moment(now)
        model = FileIndexLink(
            file_record_id=record.id,
            user_id=record.user_id,
            section=FileSectionModel(record.section.value),
            s3_key=decision.s3_key,
            content_sha256=decision.content_sha256,
            content_type=decision.content_type,
            rag_file_id=None,
            state=state.value,
            attempts=0,
            next_attempt_at=None,
            last_error_code=error_code,
            created_at=moment,
            updated_at=moment,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
        except IntegrityError as exc:
            if model in self._session:
                self._session.expunge(model)
            if not _is_unique_violation(exc):
                raise
            logger.warning(
                "Index link already exists for file_record_id {}",
                record.id,
            )
            raced = await self._model_by_file_record_id(record.id)
            if raced is None:
                logger.exception(
                    "Index link insert conflicted for file_record_id {} and no row exists",
                    record.id,
                )
                raise
            self._refuse_model(raced)
            return self._to_link(raced)

        await self._session.refresh(model)
        _log_state(model)
        return self._to_link(model)

    async def get_by_file_record_id(self, file_record_id: UUID) -> IndexLink | None:
        model = await self._model_by_file_record_id(file_record_id)
        if model is None:
            return None
        return self._to_link(model)

    async def get_by_rag_file_id(self, rag_file_id: UUID) -> IndexLink | None:
        result = await self._session.execute(
            select(FileIndexLink).where(FileIndexLink.rag_file_id == rag_file_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_link(model)

    async def list_by_user_state(
        self,
        user_id: UUID,
        state: IndexLinkState,
    ) -> list[IndexLink]:
        result = await self._session.execute(
            select(FileIndexLink)
            .where(
                FileIndexLink.user_id == user_id,
                FileIndexLink.state == IndexLinkState(state).value,
            )
            .order_by(FileIndexLink.created_at.asc(), FileIndexLink.id.asc())
        )
        return [self._to_link(model) for model in result.scalars().all()]

    async def mark_content_changed(
        self,
        file_record_id: UUID,
        decision: RegistrationDecision,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        model = await self._require_file_link(file_record_id, "content change")
        if model is None:
            return None
        next_state = (
            IndexLinkState.REINDEX_PENDING
            if model.rag_file_id is not None
            else IndexLinkState.PENDING
        )
        return await self.apply_transition(
            model.id,
            state=next_state,
            attempts=0,
            s3_key=decision.s3_key,
            content_sha256=decision.content_sha256,
            content_type=decision.content_type,
            last_error_code=None,
            next_attempt_at=None,
            now=now,
        )

    async def mark_rekey(
        self,
        file_record_id: UUID,
        s3_key: str,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        model = await self._require_file_link(file_record_id, "rekey")
        if model is None:
            return None
        moment = _moment(now)
        if model.rag_file_id is None:
            if model.s3_key != s3_key:
                model.s3_key = s3_key
                model.updated_at = moment
                await self._session.flush()
                await self._session.refresh(model)
                _log_payload(model)
            return self._to_link(model)
        return await self.apply_transition(
            model.id,
            state=IndexLinkState.PENDING,
            s3_key=s3_key,
            next_attempt_at=moment,
            now=moment,
        )

    async def mark_removed(
        self,
        file_record_ids: Sequence[UUID],
        *,
        now: datetime | None = None,
    ) -> list[IndexLink]:
        ordered_ids = list(dict.fromkeys(file_record_ids))
        if not ordered_ids:
            return []
        result = await self._session.execute(
            select(FileIndexLink).where(FileIndexLink.file_record_id.in_(ordered_ids))
        )
        by_file_id = {
            model.file_record_id: model
            for model in result.scalars().all()
            if model.file_record_id is not None
        }
        if not by_file_id:
            logger.warning(
                "No index links found to remove for {} file records",
                len(ordered_ids),
            )
            return []

        moment = _moment(now)
        removed: list[IndexLink] = []
        for file_record_id in ordered_ids:
            model = by_file_id.get(file_record_id)
            if model is None:
                continue
            link = await self.apply_transition(
                model.id,
                state=IndexLinkState.REMOVED,
                next_attempt_at=None,
                now=moment,
            )
            if link is not None:
                removed.append(link)
        return removed

    async def mark_archived(
        self,
        file_record_id: UUID,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        model = await self._model_by_file_record_id(file_record_id)
        if model is None:
            logger.warning(
                "Index link for file_record_id {} not found for archive",
                file_record_id,
            )
            return None
        return await self.apply_transition(
            model.id,
            state=IndexLinkState.ARCHIVED,
            next_attempt_at=None,
            now=now,
        )

    async def claim_due(self, limit: int, now: datetime) -> list[IndexLink]:
        _require_limit(limit)
        moment = _as_utc(now)
        stmt = (
            select(FileIndexLink)
            .where(
                FileIndexLink.state.in_(_DUE_STATE_VALUES),
                FileIndexLink.section != FileSectionModel.PRIVATE,
                or_(
                    FileIndexLink.next_attempt_at.is_(None),
                    FileIndexLink.next_attempt_at <= moment,
                ),
            )
            .order_by(FileIndexLink.created_at.asc(), FileIndexLink.id.asc())
            .limit(limit)
        )
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        result = await self._session.execute(stmt)
        links = [self._to_link(model) for model in result.scalars().all()]
        if not links:
            logger.warning("No due index links to claim")
            return []
        logger.info("Claimed {} due index links", len(links))
        return links

    async def apply_transition(
        self,
        link_id: UUID,
        *,
        state: IndexLinkState | UnsetType = UNSET,
        attempts: int | UnsetType = UNSET,
        next_attempt_at: datetime | None | UnsetType = UNSET,
        rag_file_id: UUID | None | UnsetType = UNSET,
        last_error_code: str | None | UnsetType = UNSET,
        s3_key: str | UnsetType = UNSET,
        content_sha256: str | None | UnsetType = UNSET,
        content_type: str | UnsetType = UNSET,
        now: datetime | None = None,
    ) -> IndexLink | None:
        model = await self._session.get(FileIndexLink, link_id)
        if model is None:
            logger.warning("Index link {} not found for transition", link_id)
            return None
        self._refuse_model(model)
        state_changed, payload_changed = _assign_transition(
            model,
            state=state,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            rag_file_id=rag_file_id,
            last_error_code=last_error_code,
            s3_key=s3_key,
            content_sha256=content_sha256,
            content_type=content_type,
        )
        if state_changed or payload_changed:
            model.updated_at = _moment(now)
        await self._session.flush()
        await self._session.refresh(model)
        if state_changed:
            _log_state(model)
        elif payload_changed:
            _log_payload(model)
        return self._to_link(model)

    async def backfill_candidates(
        self,
        limit: int,
        after: BackfillCursor | None = None,
    ) -> list[FileRecordEntity]:
        _require_limit(limit)
        stmt = (
            self._backfill_select(after)
            .order_by(FileRecordModel.created_at.asc(), FileRecordModel.id.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        records = [FileRepository._to_entity(model) for model in result.scalars().all()]
        if not records:
            logger.warning("Backfill found no file records without an index link")
            return []
        logger.info(
            "Backfill selected {} file records without an index link",
            len(records),
        )
        return records

    async def count_remaining(self, after_enqueued: BackfillCursor | None = None) -> int:
        subq = self._backfill_select(after_enqueued).subquery()
        result = await self._session.execute(select(func.count()).select_from(subq))
        return int(result.scalar_one())

    async def counts_for_user(self, user_id: UUID) -> UserIndexCounts:
        result = await self._session.execute(
            select(FileIndexLink.state, func.count())
            .where(FileIndexLink.user_id == user_id)
            .group_by(FileIndexLink.state)
        )
        grouped = {state: int(count) for state, count in result.all()}
        # Everything that is still on its way to the index counts as pending, not
        # only the row that has not been sent yet: SUBMITTED waits for RAG to say
        # `ready`, REINDEX_PENDING waits for the old entry to be deleted.
        pending = sum(
            grouped.get(state.value, 0)
            for state in (
                IndexLinkState.PENDING,
                IndexLinkState.SUBMITTED,
                IndexLinkState.REINDEX_PENDING,
            )
        )
        return UserIndexCounts(
            indexed=grouped.get(IndexLinkState.INDEXED.value, 0),
            pending=pending,
            failed=grouped.get(IndexLinkState.FAILED.value, 0),
            skipped=grouped.get(IndexLinkState.SKIPPED.value, 0),
            total=sum(grouped.values()),
        )

    async def admin_snapshot(self) -> AdminIndexSnapshot:
        state_rows = await self._session.execute(
            select(FileIndexLink.state, func.count()).group_by(FileIndexLink.state)
        )
        by_state = {state.value: 0 for state in IndexLinkState}
        for state, count in state_rows.all():
            by_state[str(state)] = int(count)

        oldest_row = await self._session.execute(
            select(func.min(FileIndexLink.created_at)).where(
                FileIndexLink.state == IndexLinkState.PENDING.value
            )
        )
        error_count = func.count().label("error_count")
        error_rows = await self._session.execute(
            select(FileIndexLink.last_error_code, error_count)
            .where(FileIndexLink.last_error_code.is_not(None))
            .group_by(FileIndexLink.last_error_code)
            .order_by(error_count.desc(), FileIndexLink.last_error_code.asc())
        )
        last_errors = tuple(
            IndexErrorCount(code=str(code), count=int(count))
            for code, count in error_rows.all()
            if code is not None
        )
        return AdminIndexSnapshot(
            by_state=by_state,
            oldest_pending_at=_as_utc_optional(oldest_row.scalar_one()),
            last_errors=last_errors,
        )

    async def retry_failed(self, limit: int, *, now: datetime | None = None) -> int:
        _require_limit(limit)
        result = await self._session.execute(
            select(FileIndexLink)
            .where(FileIndexLink.state == IndexLinkState.FAILED.value)
            .order_by(FileIndexLink.created_at.asc(), FileIndexLink.id.asc())
            .limit(limit)
        )
        models = list(result.scalars().all())
        if not models:
            logger.warning("No failed index links to retry")
            return 0
        moment = _moment(now)
        requeued = 0
        for model in models:
            state = (
                IndexLinkState.SUBMITTED
                if model.rag_file_id is not None
                else IndexLinkState.PENDING
            )
            link = await self.apply_transition(
                model.id,
                state=state,
                attempts=0,
                next_attempt_at=None,
                last_error_code=None,
                now=moment,
            )
            if link is not None:
                requeued += 1
        logger.info("Requeued {} failed index links", requeued)
        return requeued

    async def indexed_photos_by_rag_file_id(
        self,
        user_id: UUID,
        rag_file_ids: Sequence[UUID],
    ) -> dict[UUID, IndexLink]:
        if not rag_file_ids:
            return {}
        result = await self._session.execute(
            select(FileIndexLink).where(
                FileIndexLink.user_id == user_id,
                FileIndexLink.rag_file_id.in_(tuple(rag_file_ids)),
                FileIndexLink.section == FileSectionModel.PHOTOS,
                FileIndexLink.state == IndexLinkState.INDEXED.value,
            )
        )
        found: dict[UUID, IndexLink] = {}
        for model in result.scalars().all():
            if model.rag_file_id is None:
                continue
            found[model.rag_file_id] = self._to_link(model)
        return found

    async def original_names(self, file_record_ids: Sequence[UUID]) -> dict[UUID, str]:
        unique_ids = list(dict.fromkeys(file_record_ids))
        if not unique_ids:
            return {}
        result = await self._session.execute(
            select(FileRecordModel.id, FileRecordModel.original_name).where(
                FileRecordModel.id.in_(unique_ids)
            )
        )
        names = {file_id: name for file_id, name in result.all() if name}
        missing = len(unique_ids) - len(names)
        if missing:
            logger.warning(
                "Display name missing for {} of {} file records",
                missing,
                len(unique_ids),
            )
        if names:
            logger.info("Resolved display names for {} file records", len(names))
        return names

    def _backfill_select(self, after: BackfillCursor | None):
        stmt = (
            select(FileRecordModel)
            .outerjoin(
                FileIndexLink,
                FileIndexLink.file_record_id == FileRecordModel.id,
            )
            .where(
                FileIndexLink.id.is_(None),
                FileRecordModel.status.in_(
                    (FileStatusModel.COMMITTED, FileStatusModel.ARCHIVED)
                ),
                FileRecordModel.section.in_(
                    (FileSectionModel.FILES, FileSectionModel.PHOTOS)
                ),
            )
        )
        if after is None:
            return stmt
        created_at = _as_utc(after.created_at)
        return stmt.where(
            or_(
                FileRecordModel.created_at > created_at,
                and_(
                    FileRecordModel.created_at == created_at,
                    FileRecordModel.id > after.record_id,
                ),
            )
        )

    async def _model_by_file_record_id(self, file_record_id: UUID) -> FileIndexLink | None:
        result = await self._session.execute(
            select(FileIndexLink).where(FileIndexLink.file_record_id == file_record_id)
        )
        return result.scalar_one_or_none()

    async def _require_file_link(self, file_record_id: UUID, action: str) -> FileIndexLink | None:
        model = await self._model_by_file_record_id(file_record_id)
        if model is None:
            logger.warning(
                "Index link for file_record_id {} not found for {}",
                file_record_id,
                action,
            )
            return None
        self._refuse_model(model)
        return model

    @staticmethod
    def _refuse_record(record: FileRecordEntity) -> None:
        if record.section is FileSection.PRIVATE or record.is_encrypted:
            logger.warning(
                "Refused private index link for file_record_id {} user_id {}",
                record.id,
                record.user_id,
            )
            raise PrivateIndexRefused(_PRIVATE_REFUSAL)

    @staticmethod
    def _refuse_model(model: FileIndexLink) -> None:
        if _section_value(model.section) != FileSection.PRIVATE.value:
            return
        logger.warning(
            "Refused private index link {} for file_record_id {} user_id {}",
            model.id,
            model.file_record_id,
            model.user_id,
        )
        raise PrivateIndexRefused(_PRIVATE_REFUSAL)

    @staticmethod
    def _is_excluded(record: FileRecordEntity, decision: RegistrationDecision) -> bool:
        if record.section not in _INDEXED_SECTIONS:
            return True
        return decision.skip_code is IndexSkipCode.EXCLUDED_SECTION

    @staticmethod
    def _to_link(model: FileIndexLink) -> IndexLink:
        return IndexLink(
            id=model.id,
            file_record_id=model.file_record_id,
            user_id=model.user_id,
            section=FileSection(_section_value(model.section)),
            s3_key=model.s3_key,
            content_sha256=_clean_hash(model.content_sha256),
            content_type=model.content_type,
            rag_file_id=model.rag_file_id,
            state=IndexLinkState(model.state),
            attempts=model.attempts,
            next_attempt_at=_as_utc_optional(model.next_attempt_at),
            last_error_code=model.last_error_code,
            created_at=_as_utc(model.created_at),
            updated_at=_as_utc(model.updated_at),
        )


def _insert_state(decision: RegistrationDecision) -> tuple[IndexLinkState, str | None]:
    if decision.kind is RegistrationKind.SKIP:
        code = None if decision.skip_code is None else decision.skip_code.value
        return IndexLinkState.SKIPPED, code
    return IndexLinkState.PENDING, None


def _assign_transition(
    model: FileIndexLink,
    *,
    state: IndexLinkState | UnsetType,
    attempts: int | UnsetType,
    next_attempt_at: datetime | None | UnsetType,
    rag_file_id: UUID | None | UnsetType,
    last_error_code: str | None | UnsetType,
    s3_key: str | UnsetType,
    content_sha256: str | None | UnsetType,
    content_type: str | UnsetType,
) -> tuple[bool, bool]:
    state_changed = False
    payload_changed = False
    if not isinstance(state, UnsetType):
        new_state = IndexLinkState(state).value
        if model.state != new_state:
            model.state = new_state
            state_changed = True
    if not isinstance(attempts, UnsetType) and model.attempts != attempts:
        model.attempts = attempts
    if not isinstance(next_attempt_at, UnsetType):
        scheduled = _as_utc_optional(next_attempt_at)
        if _as_utc_optional(model.next_attempt_at) != scheduled:
            model.next_attempt_at = scheduled
    if not isinstance(rag_file_id, UnsetType) and model.rag_file_id != rag_file_id:
        model.rag_file_id = rag_file_id
        payload_changed = True
    if not isinstance(last_error_code, UnsetType) and model.last_error_code != last_error_code:
        model.last_error_code = last_error_code
        payload_changed = True
    if not isinstance(s3_key, UnsetType) and model.s3_key != s3_key:
        model.s3_key = s3_key
        payload_changed = True
    if not isinstance(content_sha256, UnsetType):
        cleaned = _clean_hash(content_sha256)
        if _clean_hash(model.content_sha256) != cleaned:
            model.content_sha256 = cleaned
            payload_changed = True
    if not isinstance(content_type, UnsetType) and model.content_type != content_type:
        model.content_type = content_type
        payload_changed = True
    return state_changed, payload_changed


def _is_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "23505":
        return True
    return "unique" in str(orig or exc).lower()


def _require_limit(limit: int) -> None:
    if limit < 1:
        logger.error("Index batch limit must be positive, got {}", limit)
        raise ValueError("Index batch limit must be positive")


def _moment(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return _as_utc(now)


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return _as_utc(parsed)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_utc_optional(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    return _as_utc(value)


def _clean_hash(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _section_value(section: object) -> str:
    raw = getattr(section, "value", section)
    return str(raw)


def _log_state(model: FileIndexLink) -> None:
    logger.info(
        "Index link {} user_id={} file_record_id={} rag_file_id={} state={} error_code={}",
        model.id,
        model.user_id,
        model.file_record_id,
        model.rag_file_id,
        model.state,
        model.last_error_code,
    )


def _log_payload(model: FileIndexLink) -> None:
    logger.info(
        "Index link payload updated {} user_id={} file_record_id={} rag_file_id={} state={}",
        model.id,
        model.user_id,
        model.file_record_id,
        model.rag_file_id,
        model.state,
    )
