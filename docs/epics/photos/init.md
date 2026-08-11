# Epic: Photos

> **ID:** E-PHOTOS  
> **Status:** Implemented  
> **Specs:** [Flow Spec §5](../../Flow%20Spec.md), [API Contract §6](../../API%20Contract.md)

## Overview

Photo library with responsive grid, infinite scroll, FAB multi-upload, thumbnails, lightbox originals, and multi-delete.

## Goals

- Fast first paint via preview images (`THUMBNAIL_MAX_PX`).
- Support JPEG/PNG/WEBP/GIF/HEIC.
- Count against user quota.

## User Stories

### US-PHOTO-01 — Grid + infinite scroll
Paginated `GET /api/photos`; `has_next` drives load-more.

### US-PHOTO-02 — Upload via FAB
Multi-select; per-item progress; grid updates without full reload.

### US-PHOTO-03 — Lightbox
Open original; close returns to grid position.

### US-PHOTO-04 — Multi-delete
Selection mode → confirm → `DELETE` each / batch UX.

### US-PHOTO-05 — Reject bad formats
Non-image → `UNSUPPORTED_FORMAT`.

## Definition of Done

- [x] List/upload/preview/original/delete API + UI.
- [x] Thumbnail generation path exists.
- [x] Responsive column rules honored.
- [ ] Performance check: first batch UX target ≤2s under NFR on reference hardware.
- [ ] Component/E2E coverage for upload + lightbox.
