"""Crash-safe filesystem writes for execution artifacts."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from job_harness.v2.contracts import ExecutionArtifact


def artifact_for_bytes(
    *,
    name: str,
    path: Path,
    schema_version: int,
    content: bytes,
) -> ExecutionArtifact:
    return ExecutionArtifact(
        name=name,
        path=str(Path(path).resolve()),
        schema_version=schema_version,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_count=len(content),
    )


def atomic_write_bytes(path: Path, content: bytes) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def verify_artifact(expected: ExecutionArtifact) -> ExecutionArtifact:
    path = Path(expected.path)
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            byte_count += len(chunk)
            digest.update(chunk)
    if digest.hexdigest() != expected.sha256:
        raise ValueError(f"artifact digest mismatch: {expected.name}")
    if byte_count != expected.byte_count:
        raise ValueError(f"artifact byte count mismatch: {expected.name}")
    return expected


__all__ = ["artifact_for_bytes", "atomic_write_bytes", "verify_artifact"]
