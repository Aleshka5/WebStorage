"""Regenerate checked-in Auth-Service Python gRPC stubs (US-AUTHZ-04).

Copies proto from the sibling Auth-Service repo when present, otherwise uses
the checked-in copy under backend/app/infrastructure/auth_grpc/auth.proto.

Run from the WebStorage repo root:

    uv run python backend/scripts/generate_auth_grpc_stubs.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "backend" / "app" / "infrastructure" / "auth_grpc"
LOCAL_PROTO = PKG_DIR / "auth.proto"
AUTH_SERVICE_PROTO = REPO_ROOT.parent / "Auth-Service" / "proto" / "auth.proto"
_ABSOLUTE_PB2_IMPORT = "import auth_pb2 as auth__pb2"
_RELATIVE_PB2_IMPORT = "from . import auth_pb2 as auth__pb2"


def _sync_proto() -> None:
    if AUTH_SERVICE_PROTO.is_file():
        shutil.copyfile(AUTH_SERVICE_PROTO, LOCAL_PROTO)


def _generate() -> None:
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{PKG_DIR}",
            f"--python_out={PKG_DIR}",
            f"--grpc_python_out={PKG_DIR}",
            f"--pyi_out={PKG_DIR}",
            str(LOCAL_PROTO),
        ]
    )


def _patch_package_imports() -> None:
    grpc_path = PKG_DIR / "auth_pb2_grpc.py"
    text = grpc_path.read_text(encoding="utf-8")
    if _RELATIVE_PB2_IMPORT not in text:
        if _ABSOLUTE_PB2_IMPORT not in text:
            raise RuntimeError(
                f"Unexpected generated import in {grpc_path}; cannot patch for package use"
            )
        text = text.replace(_ABSOLUTE_PB2_IMPORT, _RELATIVE_PB2_IMPORT, 1)
        grpc_path.write_text(text, encoding="utf-8")


def main() -> None:
    PKG_DIR.mkdir(parents=True, exist_ok=True)
    _sync_proto()
    if not LOCAL_PROTO.is_file():
        raise FileNotFoundError(f"Auth proto not found at {LOCAL_PROTO}")
    _generate()
    _patch_package_imports()


if __name__ == "__main__":
    main()
