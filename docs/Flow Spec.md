# Flow Spec — HomeCloud

> **Status:** Active  
> **Related:** [Product Brief.md](./Product%20Brief.md), [API Contract.md](./API%20Contract.md)  
> **Auth (E-GWUS):** gateway handles anonymous HTML. The SPA does not redirect to Google. See [gateway-user-service](./epics/gateway-user-service/init.md).

Describes user-visible flows, UI states, and interaction rules for Spec Driven implementation.

---

## 1. Global App Bootstrap

```
App mount
  → fetchMe()
  → loading spinner
  → 503 USER_SERVICE_UNAVAILABLE → error + retry (no login loop)
  → 401 → visible error (“Open this site through the hub”)
  → authenticated? → AppLayout + requested route
```

- Authed user visiting `/` or `/auth` → `/files`.
- Missing identity → error, not OAuth.

### Layout states

| Element | Behavior |
|---|---|
| Header | Brand + profile menu (email only; no logout) |
| Sidebar expanded | Icons + labels + `StorageUsageBar` |
| Sidebar collapsed | Icons only; quota bar hidden |
| Nav Shared | Visible FAMILY/ADMIN |
| Nav Admin | Visible ADMIN |

Quota bar: `used_bytes` / `limit_bytes` from `GET /api/quota/me`. `limit_bytes` is the per-user admin-set cap (default 100 MB), not free disk capacity.

---

## 2. Authentication Flows

HomeCloud does not register or password-login. Session cookie `auth_session` is set by the Auth hub.

### 2.1 Hub Google OAuth

```
UI (ProtectedRoute / `/auth`)
  → VITE_AUTH_LOGIN_URL
    (default https://filenkov.store/oauth/google?return_to=https://storage.filenkov.store/)
  → Google consent on hub
  → cookie auth_session on .filenkov.store
  → return_to storage
  → GET /api/auth/me (Validate)
  → /files
```

First Google login on the hub creates a `STRANGER` storage role (or links existing email per Auth-Service rules).

### 2.2 Session errors

| Response | UI |
|---|---|
| 401 `UNAUTHORIZED` | Redirect to hub login URL once (not a local password form). Bootstrap `/me` 401 is handled by ProtectedRoute, not an interceptor loop. |
| 401 `PRIVATE_SESSION_EXPIRED` | Re-open passphrase modal. **No** hub redirect. |
| 503 `AUTH_UNAVAILABLE` | Explicit error + retry. **No** logout, **no** OAuth loop. |

### 2.3 Logout

Profile menu → `POST /api/auth/logout` (BFF) → clear local auth store → hub login URL.

---

## 3. Files Flow (`/files`)

**Actor:** any authenticated user.  
**Component:** `FileManager` `mode=plain` `apiPrefix=/api/files`.

| Action | Interaction |
|---|---|
| Browse | Click folder / breadcrumbs |
| Sort | name \| size \| modified_at |
| Upload | Button or drag-drop; progress toast |
| Upload ZIP | Select `.zip`; extract into path |
| Download file | Stream download |
| Download folder | ZIP stream |
| New folder | Dialog; validate name |
| Rename | Inline / dialog |
| Delete | Confirm modal |

States: loading list, empty folder, error banner (`ErrorMessage`), uploading.

Quota exceeded → surface `QUOTA_EXCEEDED` without navigation change.

---

## 4. Shared Flow (`/shared`)

**Actor:** FAMILY, ADMIN. STRANGER → redirect `/files`.

Same FileManager behaviors as Files with `apiPrefix=/api/shared`.  
List may show `uploaded_by`. Delete forbidden for non-owner non-admin → error state.

---

## 5. Photos Flow (`/photos`)

| Step | Detail |
|---|---|
| Initial load | Page 1, batch `PHOTO_BATCH_SIZE` |
| Scroll | Infinite scroll when `has_next` |
| Upload | FAB → multi file picker / camera (mobile) |
| Progress | Per-item spinner/progress |
| Open | Click → Lightbox with original |
| Select | Long-press / checkbox → multi-delete |

Grid breakpoints: 2 cols &lt;640px; 3–4 tablet; auto-fill ≥200px desktop.  
Formats: JPEG, PNG, HEIC, WEBP, GIF.

---

## 6. Private Flow (`/private`)

```
Enter /private
  → GET /api/private/session
  → active?
       yes → show private quota + FileManager encrypted
       no  → PrivateUnlockModal
```

### Unlock

1. User enters passphrase → `POST /api/private/unlock`.
2. Success → session active; FileManager `mode=encrypted` `apiPrefix=/api/private`.
3. Failure → field error; after rate limit → offer **reset** (`POST /api/private/reset`) with strong confirmation (destroys private data).
4. Cancel modal → navigate `/files`.

### Session expiry

Any private API returns `401 PRIVATE_SESSION_EXPIRED`:

- Keep product session (`auth_session`).
- Re-open unlock modal (window event from axios interceptor).
- Do not redirect to hub OAuth / login URL.

### Lock

Backend `POST /api/private/lock` clears Redis key; FE may not expose a dedicated button today — treat as available API for future UX.

Private header shows `private_bytes` / `private_limit_bytes`.

---

## 6a. Keys Registry Flow (`/keys`)

Same private vault as §6. Sidebar item is a **top-level** root (not nested under Private).

```
Enter /keys
  → GET /api/private/session
  → active?
       yes → GET /api/private/keys (bootstrap Keys/ + keys.yaml) → list
       no  → PrivateUnlockModal
```

### Unlock

1. Same modal and passphrase as Private (`POST /api/private/unlock`).
2. Cancel → `/files`.
3. Success → `GET /api/private/keys`. First visit creates `Keys/` and empty `keys.yaml` in the private vault.

### Session expiry

`401 PRIVATE_SESSION_EXPIRED` on keys APIs: keep product session, re-open unlock modal (same `homecloud:private-session-expired` event). Do not redirect to hub login.

### List / add / delete / copy / save

| Action | Behavior |
|---|---|
| List | Name + masked value (`abcd...wxyz`, or `••••` if length ≤ 8). API returns full values. |
| Copy | Clipboard gets the full in-memory value. |
| Add | Modal/form for name + value. Reject empty (trimmed) and duplicate names. No edit of existing rows. |
| Delete | Local until Save. |
| Save | Explicit button only. Disabled when pristine. `PUT /api/private/keys` with the full list. Last write wins. |

Invalid `keys.yaml` on disk → show error; do not overwrite on GET. User may fix or replace the file in Private FileManager.

Private reset (§6) wipes `Keys/keys.yaml` with the rest of the vault.

---

## 6b. Resumes Flow (`/resumes`)

**Actor:** FAMILY, ADMIN. STRANGER: no sidebar item; direct navigation shows the access-denied state
(`403 ACCESS_DENIED`). No passphrase — resumes are plain storage. See
[E-RESUMES](./epics/resumes/init.md).

### Navigation

```
/resumes                                   countries
  → /resumes/:country                      companies
    → /resumes/:country/:company           vacancies (+ status filter, status editor)
      → /resumes/:country/:company/:vacancy   leaf: url + fields + FileManager
```

URL segments are encoded names. Breadcrumbs at every level navigate back up. An unknown segment
renders a "not found" state with a link back to `/resumes`.

### Tree pages (countries / companies / vacancies)

| Element | Behavior |
|---|---|
| Primary CTA | "Add new country" / "Add a new company" / "Add a new vacancy" — centred at the top while the list is empty, moved to the end of the list once it has entries |
| Create dialog | Name field; vacancy dialog also offers a status (may be left unset) |
| Inline errors | `RESUME_NODE_EXISTS` (duplicate sibling) and `RESUME_NAME_INVALID` appear in the dialog, not only as a toast |
| Row click | Navigate one level deeper |
| Rename | Dialog prefilled with the current name → `PATCH /api/resumes/tree` |
| Delete | Confirm dialog naming the node and its descendant count → recursive hard delete; quota bar refreshes |
| Empty state | Friendly copy + the primary CTA |

### Vacancy rows and statuses

Each vacancy row shows a coloured dot + status label. An unset or deleted (dangling) `status_id`
renders as a neutral "No status". Filtering by status lives on the **All vacancies** page, not here
(D10); this page links to it.

The status editor (from the vacancies page) lists every status with a colour swatch and supports
add, rename, recolor and delete. Deleting only removes the entry; no vacancy file is rewritten.
Explicit **Save** → `PUT /api/resumes/statuses`, disabled while pristine. The first
`GET /api/resumes/statuses` seeds Applied / Interview / Offer / Rejected.

### All vacancies (`/vacancies`)

Плоский cross-tree список: таблица Country / Company / Vacancy / Status, отсортированная по стране,
компании и названию вакансии. Каждая из первых трёх колонок — ссылка на соответствующую страницу
дерева; статус рендерится тем же `ResumeStatusBadge`, что и в списке вакансий.

Фильтр по статусу: мультивыбор по всем статусам плюс «No status», два режима — **Show only**
(белый список) и **Hide** (чёрный список). Пустой выбор ничего не фильтрует в обоих режимах.
Висячий `status_id` фильтруется как «No status» — так же, как рендерится. Контрол показывает
«Showing X of Y vacancies»; если под фильтр не попало ничего — пустое состояние с **Clear filter**.

Маршрут верхнего уровня, а не `/resumes/all`: статический дочерний сегмент затенил бы страну с
именем «all». Вход — кнопка **All vacancies** на странице стран; обратно — **Back to countries**.
Тот же FAMILY/ADMIN gate. Пустое дерево → «No vacancies yet» + ссылка на `/resumes`.

### Vacancy leaf

| Element | Behavior |
|---|---|
| Status selector | Any status, or none |
| Website URL | Optional. A non-empty value without a scheme is saved as `https://<value>`; renders as an external link when set |
| Fields | Editable `name: value` list — add, edit, delete, order preserved. Plain text, fully visible, no masking, no encryption (unlike §6a) |
| Save | `PUT /api/resumes/vacancy?path=`; disabled while pristine; unsaved-changes warning on navigate away |
| Files | `<FileManager apiPrefix="/api/resumes/files" basePath="{country}/{company}/{vacancy}" hiddenNames={["meta.yaml"]} />` — full §3 behaviour, breadcrumbs rooted at the vacancy, never above it |

Invalid `meta.yaml` on disk → `409 RESUME_META_INVALID`; the page shows the error and the file is not
overwritten.

---

## 7. Admin Flow (`/admin`)

**Actor:** ADMIN. Others → redirect `/files`.

### Users tab

| Action | Flow |
|---|---|
| List | Paginated after join/filter; filter chips by live `storage_roles`; debounce email search |
| Role | Immutable text (Auth-Service `/admin`); `PATCH .../role` is 410 |
| Private quota | Edit GB → blur → `PATCH .../quota` |
| Block | Confirm → `POST .../block` |
| Delete | Confirm → `DELETE ...` |

Not in UI (TZ backlog): unblock, password-reset link.

### Storage tab

Cards from `GET /api/admin/storage` (`id`, `bucket`, total/used/free/status). Health endpoint available for ops.

Ops endpoints (archive/backup/maintenance) exist on API; FE coverage optional — document as admin API flows for curl/ops until UI ships.

---

## 8. Error & Empty States (cross-cutting)

| State | UX |
|---|---|
| Network / 500 | Toast or inline error; retry where safe |
| 403 | Inline access error; no crash |
| Empty folder / no photos | Friendly empty copy + primary CTA (upload) |
| Validation | Inline under fields (auth, folder name) |

---

## 9. State Machines (concise)

### Auth session

`anonymous` → (hub OAuth) → `authenticated` → (logout / invalid cookie) → `anonymous`  
`authenticated` + 503 `AUTH_UNAVAILABLE` stays authenticated in UI until retry (no OAuth loop).

### Private vault

`locked` → unlock ok → `unlocked` → (TTL/lock) → `locked`  
`locked` + rate limited → `lockout` → reset → `locked` (empty vault) | wait → `locked`

### File record lifecycle

`PENDING` → `COMMITTED` → (`ARCHIVED` ↔ readable) → `DELETED`

---

## 10. Accessibility & Responsive Notes

- Touch: FAB reachable; long-press selection on photos.
- Sidebar collapse for small screens.
- Modals trap focus (unlock, create folder, confirm delete).
- Do not rely on hover-only for primary actions.
