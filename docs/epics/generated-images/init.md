# Change Request: Generated Images

> **ID:** E-GENERATED
> **Status:** Implemented
> **Repos:** WebStorage (this document). The generator is Qwen: [qwen-image-service `docs/user-story-webstorage-generate.md`](../../../../qwen-image-service/docs/user-story-webstorage-generate.md).
> **Specs to update when this ships:** [API Contract](../../API%20Contract.md), [Data Models](../../Data%20Models.md), [Flow Spec](../../Flow%20Spec.md), [FRONTEND_DOCS](../../FRONTEND_DOCS.md), [BACKEND_DOCS](../../BACKEND_DOCS.md), [epics index](../init.md). An ADR is required only if the layout below is rejected.

**Actor:** A signed-in person looking at their own runs. They created those runs on `https://image.filenkov.store`. This service stores them and shows the history.

**Goal:** An API that Qwen calls, on the docker network, to store one run under the user id in `X-Auth-User-Id`. A sidebar page **Generated Images** lists those runs by datetime, opens one to show the prompt, the reference images, and the result, and deletes a run from the list.

**Value:** Storage is the archive. The image site stays the only place that runs the model. Each person sees only their own history, and delete frees their quota.

---

## 1. Background

Qwen is the master UI. After it generates, it `POST`s the bundle to this service at `http://app:8000` from `auth_network`. It forwards the gateway header `X-Auth-User-Id` (JWT `sub`) and, when it has it, `X-Auth-Email`. It does not send cookies.

This service already resolves the caller from `X-User-Id`, then `X-Auth-User-Id`, and looks up the storage role in User-Service when `X-Storage-Role` is absent. Qwen’s save call has no `X-Storage-Role`, so that lookup is the role for the save. The history pages are normal browser calls through `storage.filenkov.store`, which already arrive with `X-User-Id` and `X-Storage-Role`.

This service does not join `qwen_image_gen_network` and does not call Qwen.

## 2. Locked decisions

| # | Decision |
|---|---|
| D1 | One new section prefix `users/{user_id}/generated` and `FileSection.GENERATED` (Alembic adds the enum value). These objects are absent from `/files`, `/photos`, `/private`, and `/shared`. |
| D2 | No new table. A run is a directory plus `meta.yaml`, written through `FileService`, same shape as Resumes. |
| D3 | Plain bytes. No private-vault unlock. |
| D4 | There is no generate form in this UI. Create is only `POST /api/generated`, called by Qwen. |
| D5 | History routes (`GET`, file `GET`, `DELETE`) use the signed-in user. Any storage role except `BLOCKED` may open the page, same as Photos. `BLOCKED` is already rejected by auth. |
| D6 | `POST /api/generated` uses the same auth dependency. The user id is `X-Auth-User-Id` forwarded by Qwen. A storage role of `BLOCKED` is `403`. `STRANGER`, `FAMILY`, and `ADMIN` can have runs stored. |
| D7 | Each user sees only their own prefix. Another user’s id is `404`. |
| D8 | Quota is the user’s total `limit_bytes`. `meta.yaml`, every reference, and the result count. If they do not fit, `413 QUOTA_EXCEEDED` and no directory. Delete releases blobs, `file_records`, and quota, including archived objects. |
| D9 | One result image. References stay in upload order as `ref-1.png`, `ref-2.png`, … |
| D10 | The list label is the run’s local datetime, with the start of the prompt on the second line. |
| D11 | UI copy is English. |

## 3. Storage layout

```text
users/{user_id}/generated/{id}/
  meta.yaml
  result.png
  ref-1.png
  ref-2.png
```

`{id}` is UTC `YYYYmmddTHHMMSSZ` plus `-` and 8 hex chars (`20260927T115012Z-3b47ec64`).

### `meta.yaml`

```yaml
prompt: A cinematic portrait of the person, soft rim light, 85mm lens
negative_prompt: ""
seed: 1823486689
steps: 40
true_cfg_scale: 1.0
width: 2048
height: 2048
duration: 96.4
created_at: "2026-09-27T11:50:12Z"
references:
  - ref-1.png
result: result.png
```

A directory whose `meta.yaml` is missing or not this shape is omitted from the list. `GET` of that id is `409 GENERATED_META_INVALID` and does not rewrite the file.

## 4. API

### `POST /api/generated`

Called by Qwen on `http://app:8000`, not by the Storage page. `multipart/form-data`.

| Part | |
|---|---|
| `prompt` | required |
| `negative_prompt` | optional |
| `seed`, `steps`, `true_cfg_scale`, `width`, `height`, `duration` | as Qwen sends them |
| `images` | zero or more reference files, in order |
| `result` | the PNG, required |

Identity headers: `X-Auth-User-Id` (required UUID), `X-Auth-Email` (optional). No cookie.

`201`:

```json
{"id": "20260927T115012Z-3b47ec64", "created_at": "2026-09-27T11:50:12Z"}
```

| Condition | Response |
|---|---|
| Missing or non-UUID user id | `401` |
| Storage role `BLOCKED` | `403 ACCESS_DENIED` |
| Bad or missing prompt, missing result, more than 10 references | `400 GENERATED_INVALID`. No directory |
| Bytes do not fit quota | `413 QUOTA_EXCEEDED`. No directory |
| User-Service down while resolving the role | `503`, same as other routes that look up the role |

The directory is created only after every byte is accepted. A failed request leaves no partial folder.

### `GET /api/generated`

The signed-in user’s runs, newest `created_at` first.

```json
{
  "items": [
    {
      "id": "20260927T115012Z-3b47ec64",
      "created_at": "2026-09-27T11:50:12Z",
      "prompt": "A cinematic portrait of the person, soft rim light, 85mm lens"
    }
  ]
}
```

### `GET /api/generated/{id}`

Prompt, negative prompt, seed, steps, CFG, width, height, duration, reference file URLs in order, and the result URL. URLs point at this origin: `/api/generated/{id}/files/{name}`.

`GET /api/generated/{id}/files/{name}` streams `result.png` or a `ref-N.png` listed in `meta.yaml`. Anything else is `404`.

### `DELETE /api/generated/{id}`

`204`. Removes the directory, its `file_records`, and the quota. Unknown id is `404`. Does not call Qwen.

## 5. User stories

### US-GEN-01 — Store a run from Qwen

**As** Qwen, **I POST a finished run with the gateway user id**, and it is stored under that user.

**Acceptance**

- `POST /api/generated` with `X-Auth-User-Id` set to a UUID writes `users/{id}/generated/{id}/` with `meta.yaml`, `result.png`, and `ref-N.png` in the order of the `images` parts.
- The `201` body is `{id, created_at}`.
- No user id → `401` and no directory. `BLOCKED` → `403` and no directory. Quota miss → `413` and no directory.
- The call is satisfied on `http://app:8000` from `auth_network`. This compose does not attach `qwen_image_gen_network`.

### US-GEN-02 — Open the history

**As** a signed-in user, **I click “Generated Images”** and see my runs, newest first, each named by its datetime.

**Acceptance**

- Sidebar item `Generated Images` sits directly under `Photos` for every role that can open Photos (`STRANGER`, `FAMILY`, `ADMIN`). Hidden for `BLOCKED`, who cannot sign in here.
- Route `/generated`. Empty state: “No generated images yet.” There is no **New** button and no prompt form.
- Each row’s title is the local datetime. Under it, the prompt clipped to one line.
- A directory with unreadable `meta.yaml` is skipped. The rest of the list still loads.
- Rows are only the signed-in user’s.

### US-GEN-03 — Open one run

**As** a user, **I open a row** and see the prompt, the reference images in order, and the result.

**Acceptance**

- Route `/generated/:id`.
- Full prompt, negative prompt when it is non-empty, references labeled `image 1`, `image 2`, …, and the result.
- A secondary line shows resolution, steps, CFG, seed, and duration.
- Unknown id → `404` and a link back to `/generated`.
- Unreadable `meta.yaml` → `409 GENERATED_META_INVALID`. The file is left as it is.
- Image bytes come from `/api/generated/{id}/files/...` on this origin.

### US-GEN-04 — Delete from the list

**As** a user, **I delete a run from the list** so it no longer uses my quota.

**Acceptance**

- Each row has a delete control. A confirm dialog names the datetime. Confirm sends `DELETE /api/generated/{id}`.
- On `204` the row disappears without reloading the whole page. The sidebar quota bar refreshes.
- The detail page has the same delete and then returns to `/generated`.
- Blobs, `file_records`, and quota are released. A second delete is `404`.
- Delete does not call Qwen.

## 6. Out of scope

- A generate form, a proxy to Qwen, or joining `qwen_image_gen_network`.
- Saving a run that Qwen did not POST (no browser upload of a finished image from this page).
- A household-shared gallery.
- Editing the prompt after the fact.

## 7. Definition of done

- [x] `FileSection.GENERATED` and the Alembic enum value.
- [x] `POST /api/generated` accepts Qwen’s multipart body and `X-Auth-User-Id`, and writes the directory only when quota allows.
- [x] `GET` list, `GET` one, `GET` file, `DELETE` — owner scope, quota released on delete, corrupt `meta.yaml` skipped in the list.
- [x] Sidebar item, `/generated`, `/generated/:id`, delete from the list and from the detail page. No create form.
- [x] Backend tests: save under the header user id, 401 without it, 403 when `BLOCKED`, 413 writes nothing, list isolation, delete releases quota.
- [x] API Contract, Data Models, Flow Spec, frontend and backend docs, and the epics index row updated to Implemented.

## 8. Operator order

1. This service is up as `app` on `auth_network` with the new route.
2. Qwen is recreated on `auth_network` as well as `qwen_image_gen_network`, with `WEBSTORAGE_URL=http://app:8000`.
3. Generate on `https://image.filenkov.store`. Open **Generated Images** on `https://storage.filenkov.store`.
