"""Unit tests for US-AUTHZ-11 local → Auth-Service UUID remapping."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.infrastructure.user_uuid_migration import (
    AuthUserRow,
    LocalUserRow,
    MigrationPlan,
    UserIdRemap,
    apply_plan,
    build_plan,
    hash_email,
    parse_auth_users_file,
    rewrite_user_prefix,
)

MATCHED_LOCAL_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
UNMATCHED_LOCAL_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
AUTH_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def test_matching_email_remaps_and_reports_unmatched() -> None:
    local_users = [
        LocalUserRow(
            id=MATCHED_LOCAL_ID,
            email="Family@Example.test",
            file_record_count=3,
            quota_row_count=1,
            upload_session_count=0,
        ),
        LocalUserRow(id=UNMATCHED_LOCAL_ID, email="password-only@example.test"),
    ]
    auth_users = [AuthUserRow(id=AUTH_ID, google_email="family@example.test")]

    plan = build_plan(local_users, auth_users)

    assert len(plan.remaps) == 1
    remap = plan.remaps[0]
    assert remap.local_id == MATCHED_LOCAL_ID
    assert remap.auth_id == AUTH_ID
    assert remap.email_hash == hash_email("family@example.test")
    assert remap.file_record_count == 3
    assert remap.quota_row_count == 1

    assert len(plan.unmatched_local) == 1
    unmatched = plan.unmatched_local[0]
    assert unmatched.id == UNMATCHED_LOCAL_ID
    assert unmatched.email == "password-only@example.test"
    assert not hasattr(unmatched, "password_hash")
    assert plan.duplicates == []


def test_unmatched_reported_with_hashed_email() -> None:
    local_users = [LocalUserRow(id=UNMATCHED_LOCAL_ID, email="secret.user@example.test")]
    plan = build_plan(local_users, auth_users=[])

    assert len(plan.unmatched_local) == 1
    assert plan.remaps == []
    assert hash_email(plan.unmatched_local[0].email) == hash_email("Secret.User@example.test")
    assert "password" not in plan.unmatched_local[0].__dict__


@pytest.mark.asyncio
async def test_dry_run_does_not_write_to_session() -> None:
    session = AsyncMock()
    storage_renamer = AsyncMock()
    plan = MigrationPlan(
        remaps=[
            UserIdRemap(
                local_id=MATCHED_LOCAL_ID,
                auth_id=AUTH_ID,
                email_hash=hash_email("family@example.test"),
                file_record_count=2,
            )
        ],
        unmatched_local=[LocalUserRow(id=UNMATCHED_LOCAL_ID, email="password-only@example.test")],
    )

    await apply_plan(
        plan,
        apply=False,
        session=session,
        storage_renamer=storage_renamer,
    )

    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.delete.assert_not_called()
    session.get.assert_not_called()
    session.flush.assert_not_called()
    storage_renamer.assert_not_called()


def test_duplicate_matches_are_skipped() -> None:
    local_a = LocalUserRow(id=uuid4(), email="dup@example.test")
    local_b = LocalUserRow(id=uuid4(), email="DUP@example.test")
    auth = AuthUserRow(id=AUTH_ID, google_email="dup@example.test")

    plan = build_plan([local_a, local_b], [auth])

    assert plan.remaps == []
    assert plan.unmatched_local == []
    assert plan.duplicates
    assert plan.duplicates[0].reason == "multiple_local_emails"


def test_rewrite_user_prefix_updates_paths() -> None:
    old_id = MATCHED_LOCAL_ID
    new_id = AUTH_ID
    assert (
        rewrite_user_prefix(f"users/{old_id}/files/doc.txt", old_id, new_id)
        == f"users/{new_id}/files/doc.txt"
    )
    assert rewrite_user_prefix(f"users/{old_id}", old_id, new_id) == f"users/{new_id}"
    assert rewrite_user_prefix("shared/notes.txt", old_id, new_id) == "shared/notes.txt"
    assert rewrite_user_prefix(None, old_id, new_id) is None


def test_parse_auth_users_json_and_csv(tmp_path) -> None:
    json_path = tmp_path / "auth-users.json"
    json_path.write_text(
        json.dumps([{"id": str(AUTH_ID), "google_email": "family@example.test"}]),
        encoding="utf-8",
    )
    from_json = parse_auth_users_file(json_path)
    assert from_json == [AuthUserRow(id=AUTH_ID, google_email="family@example.test")]

    csv_path = tmp_path / "auth-users.csv"
    csv_path.write_text(
        "id,google_email\n"
        f"{AUTH_ID},family@example.test\n",
        encoding="utf-8",
    )
    from_csv = parse_auth_users_file(csv_path)
    assert from_csv == [AuthUserRow(id=AUTH_ID, google_email="family@example.test")]
