# HomeCloud Frontend Documentation

## Overview

**HomeCloud Frontend** — React 18 + TypeScript SPA (Single Page Application) с
тёмной темой (zinc palette), стилизованная через Tailwind CSS. Собирается через Vite
и разворачивается как статика на Nginx.

### Стек технологий

- Framework: React 18 (Functional components + Hooks)
- Language: TypeScript
- Routing: React Router DOM (BrowserRouter)
- State management: Zustand (create)
- HTTP client: Axios (withCredentials: true)
- Styling: Tailwind CSS (zinc color palette)
- Icons: lucide-react
- Build: Vite
- Toast notifications: react-hot-toast

---

## Project Structure

```
frontend/
├── public/                         ← статика (favicon и т.д.)
├── src/
│   ├── main.tsx                    ← точка входа
│   ├── router.tsx                  ← маршрутизация
│   ├── index.css                   ← глобальные стили
│   ├── components/
│   │   ├── Layout/                 ← AppLayout, Header, Sidebar, StorageUsageBar
│   │   ├── FileManager/            ← FileManager, FileList, FileItem, DropZone, CreateFolderDialog
│   │   ├── PhotoGrid/              ← PhotoGrid, PhotoItem, Lightbox, PhotoUploadFab
│   │   ├── ui/                     ← Button, Input, Modal, ErrorMessage
│   │   ├── PrivateUnlockModal.tsx  ← модальное окно разблокировки
│   │   └── ProtectedRoute.tsx      ← обёртка для защищённых маршрутов
│   ├── pages/
│   │   ├── PhotosPage.tsx          ← сетка фото + lightbox
│   │   ├── FilesPage.tsx           ← файловый менеджер (plain)
│   │   ├── PrivatePage.tsx         ← приватный раздел (encrypted)
│   │   ├── SharedPage.tsx          ← общая папка (plain)
│   │   └── AdminPage.tsx           ← админ-панель
│   ├── hooks/
│   │   ├── useFileUpload.ts        ← загрузка файлов с прогрессом
│   │   ├── usePhotoUpload.ts       ← загрузка фото с прогрессом
│   │   ├── useInfiniteScroll.ts    ← IntersectionObserver infinite scroll
│   │   └── usePrivateSession.ts    ← управление приватной сессией
│   ├── services/
│   │   ├── api.ts                  ← axios instance + interceptor
│   │   ├── filesApi.ts             ← CRUD файлов
│   │   ├── photosApi.ts            ← операции с фото
│   │   ├── privateApi.ts           ← unlock / lock / quota
│   │   └── adminApi.ts             ← пользователи, диски, архивы
│   ├── store/
│   │   ├── auth.ts                 ← auth state (user, logout, fetchMe)
│   │   └── quota.ts                ← quota state (used_bytes, limit_bytes)
│   ├── types/
│   │   ├── files.ts                ← FileNode, FileManagerMode, SortField
│   │   └── photos.ts               ← PhotoItem, PhotoListResponse
│   └── utils/
│       ├── format.ts               ← formatBytes, formatDateTime
│       ├── validation.ts           ← validateEmail, validatePasswordMatch, validateFileName
│       ├── photoUpload.ts          ← normalizePhotoFiles
│       ├── id.ts                   ← generateId
│       ├── toast.ts                ← showSuccessToast, showErrorToast, showUploadProgressToast
│       └── authLogin.ts            ← VITE_AUTH_LOGIN_URL + однократный редирект на хаб
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
├── Dockerfile
└── nginx.conf
```

---

## Routing

Файл: `src/router.tsx`

### Маршруты

| Path | Component | Auth | Role |
|---|---|---|---|
| `/` | RootRedirect -> `/files` или login URL хаба | auto | — |
| `/auth` | Сразу login URL хаба (авторизованный → `/files`) | no | — |
| `/files` | FilesPage (`FileManager mode=plain`) | yes | all |
| `/photos` | PhotosPage | yes | all |
| `/private` | PrivatePage (`FileManager mode=encrypted`) | yes | all |
| `/shared` | SharedPage (`FileManager mode=plain`) | yes | FAMILY, ADMIN |
| `/admin` | AdminPage | yes | ADMIN |

### Flow

1. `AppRouter()` вызывает `useAuthStore.fetchMe()` при монтировании.
2. Пока fetchMe не завершён — показывается лоадер (спиннер).
3. `GET /api/auth/me` 503 `AUTH_UNAVAILABLE` — экран ошибки с кнопкой «Повторить», без редиректа на OAuth.
4. Если пользователь авторизован: `ProtectedRoute` пропускает в `AppLayout`.
5. Если не авторизован: `ProtectedRoute` / `RootRedirect` делают `window.location` на login URL хаба (не форма пароля).
6. Авторизованный пользователь на `/auth` редиректится на `/files`.
7. Гость на `/auth` сразу уходит на `VITE_AUTH_LOGIN_URL` (локальной страницы входа нет).

Cookie сессии — HttpOnly `auth_session`; фронт её не читает. Axios `withCredentials: true`.

### Google OAuth flow (Auth-Service hub)

```
Неавторизованный пользователь
  → ProtectedRoute / `/auth`
  → VITE_AUTH_LOGIN_URL (по умолчанию
     https://filenkov.store/oauth/google?return_to=https://storage.filenkov.store/)
  → Google consent на хабе
  → cookie auth_session (.filenkov.store)
  → return_to → storage → GET /api/auth/me
```

Фронт не хранит JWT и не вызывает `/api/auth/login` / `/api/auth/register`.

---

## State Management

### Auth Store

Файл: `src/store/auth.ts`

```typescript
interface User {
  user_id: string;
  email: string;
  role: string; // "STRANGER" | "FAMILY" | "ADMIN"
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  authUnavailable: boolean;
  logout: () => Promise<void>;
  fetchMe: () => Promise<void>;
}
```

`fetchMe` — bootstrap через `GET /api/auth/me` (`user_id`, `email`, `role` = HomeCloud `storage_roles`).
401 → `user = null` (далее ProtectedRoute отправит на хаб). 503 → `authUnavailable`, user не сбрасывается в OAuth-цикл.

`logout` — `POST /api/auth/logout` (BFF), затем редирект на login URL хаба.

### Quota Store

Файл: `src/store/quota.ts`

```typescript
interface Quota {
  used_bytes: number;
  limit_bytes: number;
  private_bytes: number;
  private_limit_bytes: number;
}

interface QuotaState {
  quota: Quota | null;
  isLoading: boolean;
  fetchQuota: () => Promise<void>;
}
```

---

## Services (API Layer)

### api.ts — Базовый axios instance

- `baseURL` из `import.meta.env.VITE_API_BASE_URL` (пусто -> относительные пути)
- `withCredentials: true` — cookie (`auth_session`) передаются автоматически
- **Interceptor**:
  - 401 `PRIVATE_SESSION_EXPIRED` — только событие `homecloud:private-session-expired` (без редиректа на хаб)
  - 401 `UNAUTHORIZED` (прочие) — не трогает bootstrap `GET /api/auth/me`; на защищённой странице однократный редирект на login URL
  - 503 `AUTH_UNAVAILABLE` — `Promise.reject`, без logout и без OAuth-редиректа

```typescript
// API Error detail
interface ApiErrorDetail {
  error_code?: string;
  message?: string;
  available_bytes?: number;
  retry_after?: number;
}
```

### filesApi.ts

```typescript
listFiles(apiPrefix: string, path: string) -> Promise<FileNode[]>
uploadFile(apiPrefix, path, file, onProgress?) -> Promise<void>
downloadFile(apiPrefix, path, filename) -> void // blob download
deleteFile(apiPrefix, path) -> Promise<void>
createDirectory(apiPrefix, path, name) -> Promise<void>
renameEntry(apiPrefix, path, newName) -> Promise<void>
downloadFolder(apiPrefix, path, folderName) -> void // ZIP download
uploadZipFolder(apiPrefix, path, file, onProgress?) -> Promise<{files, dirs, total_bytes}>
```

### photosApi.ts

```typescript
listPhotos(page, limit) -> Promise<PhotoListResponse>
uploadPhoto(file, onProgress?) -> Promise<PhotoItem>
deletePhoto(id) -> Promise<void>
downloadPhotoOriginal(originalUrl, filename) -> void
```

### privateApi.ts

```typescript
getPrivateSession() -> Promise<{active, expires_in_seconds}>
unlockPrivate(passphrase) -> Promise<{success}>
getPrivateQuota() -> Promise<{private_bytes, private_limit_bytes}>
resetPrivateStorage() -> Promise<void>
```

### adminApi.ts

```typescript
listUsers(params) -> Promise<UserListResponse>
updateUserRole(userId, role) -> Promise<void>
updateUserPrivateQuota(userId, privateLimitGb) -> Promise<void>
blockUser(userId) -> Promise<void>
deleteUser(userId) -> Promise<void>
getStorageStats() -> Promise<{disks: DiskStat[]}>
```

---

## Components

### Layout

#### AppLayout

Файл: `src/components/Layout/AppLayout.tsx`

Основной layout-контейнер: sidebar слева, header + main content справа.
CSS Grid/Flex: `h-screen`, `bg-zinc-950`. Outlet рендерит дочерние роуты.

#### Header

Файл: `src/components/Layout/Header.tsx`

- Слева: логотип "HomeCloud"
- Справа: иконка пользователя (UserCircle) -> dropdown с email и кнопкой "Выйти"
- Dropdown закрывается по клику вне области (mousedown listener)
- Logout -> POST /api/auth/logout -> redirect на login URL хаба

#### Sidebar

Файл: `src/components/Layout/Sidebar.tsx`

Меню-навигация с иконками (lucide-react):

| Иконка | Label | Path | Видимость |
|---|---|---|---|
| Camera | Фото | /photos | all |
| Folder | Файлы | /files | all |
| Lock | Приватное | /private | all |
| Users | Общее | /shared | FAMILY, ADMIN |
| Settings | Админка | /admin | ADMIN |

- Сворачивается/разворачивается кнопкой-стрелкой
- В свёрнутом: иконки + tooltip при hover
- Expanded state сохраняется в localStorage ("homecloud-sidebar-expanded")
- Внизу: `StorageUsageBar` (только в развёрнутом состоянии)

#### StorageUsageBar

Файл: `src/components/Layout/StorageUsageBar.tsx`

Прогресс-бар использования хранилища:
- `formatUsedBytes`: bytes -> MB ("{N} МБ")
- `formatLimitBytes`: STRANGER -> MB, FAMILY/ADMIN -> GB ("{N} ГБ")
- Fetches quota from store на mount

### FileManager

Файл: `src/components/FileManager/FileManager.tsx`

**Ключевой компонент.** Универсальный файловый менеджер с двумя режимами:

```typescript
interface FileManagerProps {
  apiPrefix: string;   // "/api/files" | "/api/private" | "/api/shared"
  mode: "plain" | "encrypted";
}
```

#### Функционал

- **Breadcrumb навигация**: корень -> папки. Клик на breadcrumb -> переход в папку.
- **Сортировка**: по имени / размеру / дате (asc/desc)
- **Загрузка файлов**: кнопка "Загрузить" (multiple) или drag & drop
- **Загрузка ZIP**: кнопка "Загрузить папку" (ZIP only)
- **Создание папок**: кнопка "Новая папка" -> CreateFolderDialog
- **Скачивание**: файл -> blob download, папка -> ZIP download
- **Переименование**: inline editing (Pencil icon)
- **Удаление**: кнопка Trash -> Modal подтверждения
- **Drag & Drop**: DropZone обёртка (files + ZIP)
- **Прогресс загрузки**: панель с upload items (progress bars)
- **Обработка ошибок**: ErrorMessage при failed list

#### Состояние

```typescript
currentPath: string           // "/"" | "/folder" | "/folder/sub"
items: FileNode[]             // результат listFiles
isLoading: boolean
listErrorCode: string | null
sortField: SortField          // "name" | "size" | "modified_at"
sortDirection: SortDirection  // "asc" | "desc"
isCreateFolderOpen: boolean
itemToDelete: FileNode | null
isDeleting: boolean
uploads: UploadFileState[]    // from useFileUpload hook
zipUploadProgress: {name, progress, status, files?, dirs?, error?}
```

#### Хуки

- `useFileUpload(apiPrefix)` — состояние файловых загрузок
- `refreshDirectory` — refactored через useCallback, fetches listFiles + fetchQuota

#### Сортировка

sortItems() — локальная сортировка:
1. Папки всегда первыми (is_dir sort key)
2. Затем по выбранному полю (name localeCompare "ru", size number, modified_at date)
3. Direction toggle

### FileManager subcomponents

#### FileList

Файл: `src/components/FileManager/FileList.tsx`

Таблица: columns (Type, Name, Size, Modified, Actions).
Sortable headers с иконками (ArrowUpDown / ArrowUp / ArrowDown).
Responsive: Size (sm+), Modified (md+) скрыты на мобильных.

#### FileItem

Файл: `src/components/FileManager/FileItem.tsx`

Строка таблицы:
- Иконка: Folder (amber) / File (sky)
- Имя: кликабельно (folder open) или inline-edit (rename mode)
- Размер: formatBytes(item.size, is_dir) — для папок показывает "—"
- Дата: formatDateTime(modified_at)
- Actions: Download (file) / Download ZIP (folder), Rename, Delete
- showUploader: если true, показывает email загрузчика под именем (shared section)

#### DropZone

Файл: `src/components/FileManager/DropZone.tsx`

Обёртка с drag & drop обработчиками:
- Счётчик dragenter/dragleave через dragCounterRef для корректного отслеживания
- Определяет: ZIP файлы -> onDropZip, обычные -> onDrop
- Визуальный overlay при drag: dashed border + "Отпустите файлы для загрузки"

#### CreateFolderDialog

Файл: `src/components/FileManager/CreateFolderDialog.tsx`

Modal с Input для названия папки. Валидация через validateFileName().

### PhotoGrid

Файл: `src/components/PhotoGrid/PhotoGrid.tsx`

Сетка изображений через CSS grid class `.photo-grid`.
Renders: PhotoItemComponent для каждой фотографии, Lightbox при selected index.

#### PhotoItemComponent

Файл: `src/components/PhotoGrid/PhotoItem.tsx`

- Aspect-square карточка с превью
- Long-press (500ms) -> entry в selection mode
- Checkbox overlay в selection mode
- Lazy image loading с placeholder pulse
- Click: selection mode -> toggle select, otherwise -> open lightbox

#### Lightbox

Файл: `src/components/PhotoGrid/Lightbox.tsx`

Модальное окно просмотра фото в полном разрешении:
- Arrow navigation (left/right)
- Keyboard: Escape (close), ArrowLeft/ArrowRight (navigate)
- Download button (original)
- Counter: "1 / N"
- Preserves scroll position on close

#### PhotoUploadFab

Файл: `src/components/PhotoGrid/PhotoUploadFab.tsx`

Floating Action Button (+) в правом нижнем углу.
Input type="file" accept="image/*" multiple overlayed on the FAB.
Upload progress panel below the FAB (fixed position).

### PrivateUnlockModal

Файл: `src/components/PrivateUnlockModal.tsx`

Модальное окно входа в приватный раздел:
- Input для passphrase
- Submit -> unlockPrivate(passphrase)
- Обработка 429 (TOO_MANY_ATTEMPTS) -> показывает кнопку "Сбросить приватное хранилище"
- Обработка 401 (PrivateSessionExpiredError)
- Блокировка body scroll при открытии

### ProtectedRoute

Файл: `src/components/ProtectedRoute.tsx`

Simple guard: если user === null -> однократный `window.location` на login URL хаба, иначе children.

### UI Components

#### Button

Файл: `src/components/ui/Button.tsx`

```typescript
type ButtonVariant = "primary" | "secondary" | "danger";
```

Props: variant, isLoading, disabled, className, children.
isLoading -> spinner вместо children.

#### Input

Файл: `src/components/ui/Input.tsx`

Стандартный input с label и optional error text.

#### Modal

Файл: `src/components/ui/Modal.tsx`

Backdrop + centered content box. Title + children.

#### ErrorMessage

Файл: `src/components/ui/ErrorMessage.tsx`

Отображение локализованных ошибок по error_code.

```typescript
// ERROR_MESSAGES mapping
QUOTA_EXCEEDED: "Недостаточно места. Освободите {available} для продолжения"
PRIVATE_SESSION_EXPIRED: "Сессия истекла. Введите кодовое слово снова"
DISK_UNAVAILABLE: "Хранилище временно недоступно. Попробуйте позже"
TOO_MANY_ATTEMPTS: "Слишком много попыток. Подождите {retry_after} минут"
ACCESS_DENIED: "Нет доступа к этому разделу"
FILE_NOT_FOUND: "Файл не найден или был удалён"
INTERNAL_ERROR: "Произошла ошибка. Попробуйте позже"
UNSUPPORTED_FORMAT: "Неподдерживаемый формат файла"
PATH_TRAVERSAL_DETECTED: "Недопустимый путь к файлу"
UNAUTHORIZED: "Требуется авторизация"
AUTH_UNAVAILABLE: "Сервис авторизации временно недоступен. Попробуйте позже"
INVALID_CREDENTIALS: "Неверный email или пароль"
EMAIL_ALREADY_EXISTS: "Пользователь с таким email уже существует"
NOT_IMPLEMENTED: "Функция пока недоступна"
```

`getErrorMessage(errorCode, options, fallback)` — resolution logic:
1. error_code -> lookup ERROR_MESSAGES -> apply placeholders
2. fallbackMessage
3. "Ошибка: {errorCode}"
4. ERROR_MESSAGES.INTERNAL_ERROR

### Pages

#### `/auth`

Локальной страницы входа нет (US-AUTHZ-09). Гость сразу уходит на `VITE_AUTH_LOGIN_URL`; авторизованный пользователь — на `/files`.

#### PhotosPage

Файл: `src/pages/PhotosPage.tsx`

- Infinite scroll через IntersectionObserver (useInfiniteScroll)
- Selection mode: long-press enter mode, checkbox toggle, "Select all", "Delete"
- Lightbox with scroll position preservation
- Upload via FAB with progress tracking
- Batch delete: Promise.all(deletePhoto(ids))

#### FilesPage

Файл: `src/pages/FilesPage.tsx`

Простой wrapper: `<FileManager apiPrefix="/api/files" mode="plain" />`.
Flash message через location.state.message.

#### PrivatePage

Файл: `src/pages/PrivatePage.tsx`

- usePrivateSession() hook управляет состоянием сессии
- Если не active -> PrivateUnlockModal
- Если active -> `<FileManager apiPrefix="/api/private" mode="encrypted" />` + PrivateQuotaBar
- PrivateQuotaBar: fetches /api/private/quota, показывает used/limit в GB

#### SharedPage

Файл: `src/pages/SharedPage.tsx`

- Если STRANGER -> Navigate /files (message="Нет доступа")
- Иначе: `<FileManager apiPrefix="/api/shared" mode="plain" />`

#### AdminPage

Файл: `src/pages/AdminPage.tsx`

Два раздела:
1. **Пользователи**: таблица с фильтрами (role buttons, email search debounce 300ms)
   - Role select per user (change triggers confirm dialog -> updateUserRole)
   - PrivateQuotaInput (blur save)
   - Block/Delete buttons per row
   - Pagination controls
2. **Хранилище**: StorageDiskCard grid (total/used/free + status + progress bar)

### Hooks

#### useFileUpload

Файл: `src/hooks/useFileUpload.ts`

```typescript
interface UploadFileState {
  id: string;
  name: string;
  progress: number;
  status: "uploading" | "done" | "error";
  error_code?: string;
}
```

- `uploadFiles(files, path)` — параллельные upload'ы с прогрессом
- Для больших файлов (LARGE_UPLOAD_THRESHOLD_BYTES) показывает toast
- clearFinished() — удаляет done/error upload'ы

#### usePhotoUpload

Файл: `src/hooks/usePhotoUpload.ts`

Аналогичен useFileUpload, но для фото:
- `uploadPhotos(files, onUploaded?)` — параллельные upload'ы
- onUploaded callback — для добавления фото в grid без перезагрузки
- normalizePhotoFiles — обработка файлов перед загрузкой

#### useInfiniteScroll

Файл: `src/hooks/useInfiniteScroll.ts`

IntersectionObserver-based infinite scroll:
- sentinelRef -> observes sentinel div
- rootMargin: "300px" (trigger early)
- loadingRef — защита от дублирования запросов
- loadMore — check hasNext && !isLoading && !loadingRef before calling loadFn

#### usePrivateSession

Файл: `src/hooks/usePrivateSession.ts`

```typescript
interface UsePrivateSessionResult {
  isActive: boolean;
  isLoading: boolean;
  showUnlockModal: boolean;
  refreshSession: () => Promise<void>;
  onUnlockSuccess: () => void;
}
```

- refreshSession() -> GET /api/private/session
- Слушает CUSTOM event `homecloud:private-session-expired` (dispatched by api.ts interceptor)
- onUnlockSuccess() -> set active, hide modal

---

## Utilities

### format.ts

Файл: `src/utils/format.ts`

```typescript
formatBytes(bytes: number, isDirectory: boolean): string
// isDirectory -> "—", bytes -> "X B/KB/MB/GB/TB"

formatDateTime(iso: string): string
// toLocaleString("ru-RU", {day:2, month:2, year:"numeric", hour:"2-digit", minute:"2-digit"})
```

### validation.ts

Файл: `src/utils/validation.ts`

```typescript
validateEmail(email: string): string | undefined  // empty / invalid format
validateRequired(value: string, message: string): string | undefined
validatePasswordMatch(password: string, confirmPassword: string): string | undefined
validateFileName(name: string): string | undefined // invalid characters
```

### photoUpload.ts

Файл: `src/utils/photoUpload.ts`

```typescript
normalizePhotoFiles(files: File[]): File[]
```

### id.ts

Файл: `src/utils/id.ts`

```typescript
generateId(): string
```

### toast.ts

Файл: `src/utils/toast.ts`

```typescript
LARGE_UPLOAD_THRESHOLD_BYTES: number  // порог для показа toast

showSuccessToast(message: string): string
showErrorToast(error: unknown): void
showUploadProgressToast(id, name, progress: number): void
dismissToast(id: string): void
```

### authLogin.ts

Файл: `src/utils/authLogin.ts`

```typescript
DEFAULT_AUTH_LOGIN_URL: string  // совпадает с backend AUTH_LOGIN_URL
getAuthLoginUrl(): string       // import.meta.env.VITE_AUTH_LOGIN_URL ?? default
isProtectedAppPath(pathname: string): boolean
shouldRedirectToHubOnUnauthorized(status, errorCode, requestUrl, pathname): boolean
redirectToAuthLogin(): void     // window.location.assign, один раз за загрузку страницы
```

---

## CSS / Styling

Файл: `src/index.css`

- Tailwind CSS (zinc color palette)
- `.photo-grid` — CSS Grid с responsive columns:
  - < 640px: 2 колонки
  - 640-1024px: 3-4 колонки
  - > 1024px: auto-fill, min 200px
- Тёмная тема: bg-zinc-950, bg-zinc-900, text-zinc-100/200/300/400/500

---

## Build & Deploy

### Vite Config

Файл: `frontend/vite.config.ts`
- React plugin (@vitejs/plugin-react)
- Base path configured for Nginx
- `VITE_AUTH_LOGIN_URL` (optional) — hub OAuth URL; default matches backend `AUTH_LOGIN_URL`

### Nginx Config

Файл: `frontend/nginx.conf`
- Serves static files from /usr/share/nginx/html
- SPA fallback: try files, then index.html

### Dockerfile

Файл: `frontend/Dockerfile`
- Multi-stage build: node for build, nginx for serving
