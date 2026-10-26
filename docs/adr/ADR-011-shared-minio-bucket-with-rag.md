# ADR-011: Shared MinIO bucket `storage` with RAG

- **Status:** Accepted
- **Date:** 2026-10-26
- **Deciders:** team
- **Related:** Epic [E-SIDX](../epics/storage-indexing/init.md), [ADR-007](./ADR-007-minio-blob-backend.md)

## Context

Files and Photos already live as plain objects in the single MinIO bucket `storage` (`S3_BUCKET`, default `storage`). Keys are `users/{user_id}/files/…` and `users/{user_id}/photos/…`.

RAG indexes those objects in place. A second bucket would mean a copy of every byte and a second owner of the same file. This service must keep upload, rename, delete, and archive working when RAG is down, and must never delete an object because a vector was removed.

The `app` container can reach RAG only on the external Docker network `rag_edge`, at `http://rag-edge:8080`. It does not join `rag_internal`.

## Decision

1. **One bucket.** Storage and RAG both use MinIO bucket `storage`. This service does not create a second bucket and does not configure one.
2. **Storage owns the objects.** Upload, overwrite, delete, and archive remain the only writers and deleters of `users/…` bytes. Indexing never calls MinIO delete or write. Removing a file from search deletes RAG vectors only (`DELETE /api/files/{rag_file_id}`).
3. **RAG reads by key.** The index link stores the object key already kept on `file_records.relative_path` (or the Photos JPEG preview key). The manifest sends that key. The bucket name is not prefixed onto it. RAG HEADs and reads the object in `storage`.
4. **Identity.** Calls to RAG send `X-Auth-User-Id` of the file owner and an optional `X-Request-Id`. They do not send a cookie, `Authorization`, or `X-Auth-Role`.
5. **Network.** The `app` service joins external network `rag_edge`. `RAG_BASE_URL` defaults to `http://rag-edge:8080`. `RAG_ENABLED` defaults to `false`. While it is false, no link rows are written and no call leaves the process.

Private and encrypted bytes are refused before a link row exists. Shared, Resumes, and Generated are not indexed.

## Consequences

### Positive

- One object, one owner. Search does not depend on a copied blob.
- An RAG outage does not fail an upload: the request only inserts `file_index_links`, and the job retries later.
- No new bucket, no new container. The sync job runs inside `app`.

### Negative / Trade-offs

- RAG and Storage share a failure domain for that bucket. A missing object becomes `FAILED` / `object_not_found` on the link and is not retried forever. The bytes are still Storage’s to restore or delete.
- Operators must attach `app` to `rag_edge` before `RAG_ENABLED=true`. A wrong base URL looks like `503 INDEXING_UNAVAILABLE` on photo search and `rag_reachable: false` on the admin snapshot.
- Key layout is a contract. Storage must keep writing the keys it already writes. RAG must read those keys and must not delete them.

### Follow-ups

- `RAG_*` in `.env.example` and README. Alembic `008_add_file_index_links`.
- RAG-Chat must use the same bucket `storage` and the edge on `rag_edge`. That side is outside this repository.

## Alternatives Considered

| Option | Why not |
|---|---|
| A second bucket, and copy each file into it | Two copies, two deleters, and a window where search and the library disagree |
| RAG owns `users/…` objects | Breaks the rule that deleting vectors never deletes bytes |
| Call RAG on the upload request | An outage would fail a successful upload. Registration stays on `index_sync` |
| Browser calls RAG directly | Second origin, CORS, and a second cookie. The SPA calls only this service |

## Notes

- Client: `backend/app/infrastructure/rag/client.py`. Queue: `file_index_links`. Job id: `index_sync`.
- Compose: `rag_edge` is `external: true`, `name: rag_edge`, on `app` only.
