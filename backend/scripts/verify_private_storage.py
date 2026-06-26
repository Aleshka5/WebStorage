"""Verification script for encrypted private storage (run inside app container)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE_URL = os.environ.get("VERIFY_BASE_URL", "http://127.0.0.1:8000")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "fad80223@gmail.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "fsa80223")
PASSPHRASE = os.environ.get("VERIFY_PASSPHRASE", "test-secret-phrase")
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "/storage"))


class CheckResult:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.errors: list[str] = []

    def ok(self, name: str) -> None:
        self.passed += 1
        print(f"[PASS] {name}")

    def fail(self, name: str, detail: str) -> None:
        self.failed += 1
        self.errors.append(f"{name}: {detail}")
        print(f"[FAIL] {name}: {detail}")


def main() -> int:
    results = CheckResult()
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    test_ip = f"verify-{uuid.uuid4().hex[:8]}"

    def request(
        method: str,
        path: str,
        *,
        data: dict | None = None,
        files: dict | None = None,
        expected_status: int | None = None,
    ) -> tuple[int, dict | str | None]:
        url = f"{BASE_URL}{path}"
        headers: dict[str, str] = {"X-Forwarded-For": test_ip}

        if files is not None:
            boundary = f"----WebStorageVerify{uuid.uuid4().hex}"
            body_parts: list[bytes] = []
            for field_name, (filename, content, content_type) in files.items():
                body_parts.append(f"--{boundary}\r\n".encode())
                body_parts.append(
                    (
                        f'Content-Disposition: form-data; name="{field_name}"; '
                        f'filename="{filename}"\r\n'
                        f"Content-Type: {content_type}\r\n\r\n"
                    ).encode()
                )
                body_parts.append(content)
                body_parts.append(b"\r\n")
            body_parts.append(f"--{boundary}--\r\n".encode())
            body = b"".join(body_parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            req = Request(url, data=body, headers=headers, method=method)
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
            req = Request(url, data=body, headers=headers, method=method)
        else:
            req = Request(url, headers=headers, method=method)

        try:
            with opener.open(req, timeout=30) as response:
                status = response.status
                raw = response.read()
        except HTTPError as exc:
            status = exc.code
            raw = exc.read()

        if expected_status is not None and status != expected_status:
            raise AssertionError(f"expected HTTP {expected_status}, got {status}: {raw[:300]!r}")

        if not raw:
            return status, None
        try:
            parsed = json.loads(raw.decode())
            if isinstance(parsed, dict) and "detail" in parsed and isinstance(parsed["detail"], dict):
                return status, parsed["detail"]
            return status, parsed
        except json.JSONDecodeError:
            return status, raw.decode(errors="replace")

    try:
        status, body = request(
            "POST",
            "/api/auth/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            expected_status=200,
        )
        assert isinstance(body, dict)
        user_id = str(body["user_id"])
        results.ok("login")
    except Exception as exc:
        results.fail("login", str(exc))
        return _report(results)

    try:
        status, body = request("GET", "/api/private/session", expected_status=200)
        assert isinstance(body, dict)
        assert body["active"] is False
        results.ok("session inactive before unlock")
    except Exception as exc:
        results.fail("session inactive before unlock", str(exc))

    try:
        status, body = request(
            "POST",
            "/api/private/unlock",
            data={"passphrase": PASSPHRASE},
            expected_status=200,
        )
        assert isinstance(body, dict)
        assert body["success"] is True
        results.ok("unlock success")
    except Exception as exc:
        results.fail("unlock success", str(exc))

    try:
        status, body = request("GET", "/api/private/session", expected_status=200)
        assert isinstance(body, dict)
        assert body["active"] is True
        assert body["expires_in_seconds"] > 0
        results.ok("session active after unlock")
    except Exception as exc:
        results.fail("session active after unlock", str(exc))

    secret_content = b"HomeCloud private verification content"
    uploaded_name = "secret.txt"
    try:
        status, body = request(
            "POST",
            "/api/private/upload?path=/",
            files={"file": (uploaded_name, secret_content, "text/plain")},
            expected_status=201,
        )
        assert isinstance(body, dict)
        assert body["name"] == uploaded_name
        results.ok("upload private file")
    except Exception as exc:
        results.fail("upload private file", str(exc))

    try:
        status, body = request("GET", "/api/private?path=/", expected_status=200)
        assert isinstance(body, list)
        names = [item["name"] for item in body]
        assert uploaded_name in names
        results.ok("list shows decrypted filename")
    except Exception as exc:
        results.fail("list shows decrypted filename", str(exc))

    try:
        status, body = request(
            "GET",
            f"/api/private/download?path=/{uploaded_name}",
            expected_status=200,
        )
        assert isinstance(body, str)
        assert body.encode() == secret_content
        results.ok("download returns decrypted content")
    except Exception as exc:
        results.fail("download returns decrypted content", str(exc))

    try:
        private_dir = STORAGE_ROOT / "disk1" / "users" / user_id / "private"
        on_disk_files = [
            path
            for path in private_dir.iterdir()
            if path.is_file() and path.name not in {".marker"} and not path.name.startswith(".tmp")
        ]
        if not on_disk_files:
            raise AssertionError(f"no encrypted files found in {private_dir}")
        raw = on_disk_files[0].read_bytes()
        if secret_content in raw:
            raise AssertionError("plaintext found on disk")
        results.ok("file stored encrypted on disk")
    except Exception as exc:
        results.fail("file stored encrypted on disk", str(exc))

    try:
        request("POST", "/api/private/lock", expected_status=204)
        status, body = request("GET", "/api/private/session", expected_status=200)
        assert isinstance(body, dict)
        assert body["active"] is False
        results.ok("lock clears private session")
    except Exception as exc:
        results.fail("lock clears private session", str(exc))

    try:
        status, body = request("GET", "/api/private?path=/")
        if status == 401 and isinstance(body, dict) and body.get("error_code") == "PRIVATE_SESSION_EXPIRED":
            results.ok("expired session returns 401 PRIVATE_SESSION_EXPIRED")
        else:
            results.fail("expired session returns 401", f"got HTTP {status}: {body!r}")
    except Exception as exc:
        results.fail("expired session returns 401", str(exc))

    wrong_pass = "definitely-wrong-passphrase"
    blocked = False
    for attempt in range(1, 7):
        try:
            status, body = request(
                "POST",
                "/api/private/unlock",
                data={"passphrase": wrong_pass},
            )
            if status == 429:
                detail = body if isinstance(body, dict) else {}
                if detail.get("error_code") == "TOO_MANY_ATTEMPTS":
                    blocked = True
                    break
            elif status == 200 and isinstance(body, dict) and body.get("success") is False:
                continue
            else:
                raise AssertionError(f"unexpected response on attempt {attempt}: {status} {body!r}")
        except HTTPError as exc:
            if exc.code == 429:
                blocked = True
                break
            raise

    if blocked:
        results.ok("rate limit after failed unlock attempts")
    else:
        results.fail("rate limit after failed unlock attempts", "429 not received after 6 attempts")

    return _report(results)


def _report(results: CheckResult) -> int:
    print("\n=== Summary ===")
    print(f"Passed: {results.passed}")
    print(f"Failed: {results.failed}")
    if results.errors:
        print("\nFailures:")
        for error in results.errors:
            print(f"  - {error}")
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
