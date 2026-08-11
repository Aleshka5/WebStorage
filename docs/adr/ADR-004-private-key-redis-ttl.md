# ADR-004: Private encryption key in Redis with separate TTL

- **Status:** Accepted
- **Date:** 2026-06-01
- **Related:** `PrivateService`, `PRIVATE_SESSION_TTL_HOURS`, error `PRIVATE_SESSION_EXPIRED`

## Context

Passphrase must never be stored in DB/cookies. Users need vault access for a limited time without re-entering passphrase every request, without logging out of the main app when vault TTL ends.

## Decision

1. Derive AES key via PBKDF2 (100k, salt=`user_id`).
2. Validate by decrypting `.marker` (`HOMECLOUD_MARKER_V1`).
3. Store derived key in Redis keyed by auth cookie value; TTL = `PRIVATE_SESSION_TTL_HOURS` (default 4), sliding on use.
4. On expiry, return `401` with `PRIVATE_SESSION_EXPIRED` and keep main JWT valid.
5. Support lock + rate-limited reset that wipes private data.

## Consequences

### Positive

- Matches threat model for home NAS vault.
- UX can re-prompt unlock independently.

### Negative / Trade-offs

- Redis becomes required for private features.
- Server can decrypt while session active (trusted-server model).

## Alternatives Considered

| Option | Why not |
|---|---|
| Store passphrase in JWT claims | Larger token; harder revoke; sensitive in logs risk |
| Client-only key in memory | Breaks multi-tab server streaming model of this app |
