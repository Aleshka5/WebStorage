# Flow Spec — HomeCloud

> **Status:** Active  
> **Related:** [Product Brief.md](./Product%20Brief.md), [API Contract.md](./API%20Contract.md)

Describes user-visible flows, UI states, and interaction rules for Spec Driven implementation.

---

## 1. Global App Bootstrap

```
App mount
  → fetchMe()
  → loading spinner
  → authenticated? → AppLayout + requested route
  → else → /auth (ProtectedRoute redirect)
```

- Authed user visiting `/auth` → redirect `/files`.
- Root `/` → `/files` if authed else `/auth`.

### Layout states

| Element | Behavior |
|---|---|
| Header | Brand + profile menu (logout) |
| Sidebar expanded | Icons + labels + `StorageUsageBar` |
| Sidebar collapsed | Icons only; quota bar hidden |
| Nav Shared | Visible FAMILY/ADMIN |
| Nav Admin | Visible ADMIN |

Quota bar: `used_bytes` / `limit_bytes` from `GET /api/quota/me`. FAMILY/ADMIN limits reflect free disk capacity.

---

## 2. Authentication Flows

### 2.1 Register

1. User opens `/auth` → Register tab.
2. Client validates email, password, password match.
3. `POST /api/auth/register` → on success auto `POST /api/auth/login`.
4. Navigate `/files`; load quota.
5. Errors: `EMAIL_ALREADY_EXISTS` under email field; rate limit toast/message.

### 2.2 Login

1. Login tab → email/password.
2. `POST /api/auth/login` → cookie set → `/files`.
3. `INVALID_CREDENTIALS` on password field.

### 2.3 Google OAuth

```
UI → GET /api/auth/google
  → Google consent
  → GET /api/auth/google/callback
  → GET /api/auth/google/session?ticket=
  → /files + cookie
```

First Google login creates `STRANGER` account (or links existing email per service rules).

### 2.4 Logout

Profile menu → `POST /api/auth/logout` → clear local auth store → `/auth`.

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

- Keep main JWT session.
- Re-open unlock modal (window event from axios interceptor).
- Do not redirect to `/auth`.

### Lock

Backend `POST /api/private/lock` clears Redis key; FE may not expose a dedicated button today — treat as available API for future UX.

Private header shows `private_bytes` / `private_limit_bytes`.

---

## 7. Admin Flow (`/admin`)

**Actor:** ADMIN. Others → redirect `/files`.

### Users tab

| Action | Flow |
|---|---|
| List | Paginated; filter role; debounce email search |
| Change role | Select → `PATCH .../role` |
| Private quota | Edit GB → blur → `PATCH .../quota` |
| Block | Confirm → `POST .../block` |
| Delete | Confirm → `DELETE ...` |

Not in UI (TZ backlog): unblock, password-reset link.

### Storage tab

Cards from `GET /api/admin/storage` (total/used/free/status). Health endpoint available for ops.

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

`anonymous` → (login/register/oauth) → `authenticated` → (logout / invalid cookie) → `anonymous`

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
