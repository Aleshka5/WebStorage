# Epic: Authentication & Sessions

> **ID:** E-AUTH  
> **Status:** Implemented (gap: password reset)  
> **Specs:** [Flow Spec §2](../../Flow%20Spec.md), [API Contract §3](../../API%20Contract.md), [ADR-003](../../adr/ADR-003-jwt-httponly-cookie.md)

## Overview

Enable users to register, log in (password or Google), maintain JWT cookie sessions, and log out. Bootstrap first ADMIN via `init_db.py`.

## Goals

- Secure session via httpOnly JWT cookie.
- Role assigned `STRANGER` on self-registration / first OAuth.
- Rate-limit brute force on register/login.

## User Stories

### US-AUTH-01 — Register with email/password
**As a** visitor, **I want** to create an account, **so that** I can store my files.  
**Acceptance:** validation on client+server; `201` then auto-login; duplicate email → `EMAIL_ALREADY_EXISTS`.

### US-AUTH-02 — Login
**As a** user, **I want** to log in, **so that** I reach `/files` with a session cookie.  
**Acceptance:** bad creds → `INVALID_CREDENTIALS`; success sets cookie; `GET /api/auth/me` works.

### US-AUTH-03 — Google OAuth
**As a** user, **I want** to sign in with Google, **so that** I skip password setup.  
**Acceptance:** redirect chain completes to `/files`; OAuth misconfig → safe error.

### US-AUTH-04 — Logout
**As a** user, **I want** to log out, **so that** the cookie is cleared and UI returns to `/auth`.

### US-AUTH-05 — Password reset (backlog)
**As a** user, **I want** a temporary reset link, **so that** I can recover access.  
**Acceptance:** per TZ admin/user flows — **not implemented**.

## Definition of Done

- [x] Register/login/logout/me + Google bridge work.
- [x] Rate limits return `TOO_MANY_ATTEMPTS`.
- [x] FE AuthPage field errors mapped.
- [ ] Password reset per TZ.
- [ ] Tests per [Test Spec](../../Test%20Spec.md) for auth happy/negative paths.
