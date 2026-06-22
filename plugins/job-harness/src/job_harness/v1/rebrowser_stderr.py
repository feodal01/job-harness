"""Filter known noisy rebrowser-playwright diagnostic blocks from stderr."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterable

_DISABLED_VALUES = {"0", "false", "no", "off"}
_WORLD_ERROR_PREFIX = b"[rebrowser-patches][frames._context] cannot get world, error:"
_WORLD_ERROR_CONTINUATION_PREFIXES = (
    b"    at ",
    b"  type: ",
    b"  method: ",
    b"  logs: ",
)

_install_lock = threading.Lock()
_installed = False


def install_rebrowser_stderr_filter() -> None:
    """Install a process-wide stderr filter for known rebrowser patch noise.

    `rebrowser-playwright` emits this diagnostic via Node's `console.error`,
    bypassing Python logging. Keep the stealth runtime fix enabled, but remove
    the specific frame-context diagnostic that can dominate CLI/MCP output.
    """
    if not _filter_enabled():
        return

    global _installed
    with _install_lock:
        if _installed:
            return
        sys.stderr.flush()
        read_fd, write_fd = os.pipe()
        original_fd = os.dup(2)
        os.dup2(write_fd, 2)
        os.close(write_fd)
        thread = threading.Thread(
            target=_forward_filtered_stderr,
            args=(read_fd, original_fd),
            name="job-harness-rebrowser-stderr-filter",
            daemon=True,
        )
        thread.start()
        _installed = True


def filter_rebrowser_stderr_lines(lines: Iterable[bytes]) -> list[bytes]:
    """Return stderr lines with known rebrowser frame-context blocks removed."""
    forwarded: list[bytes] = []
    suppressing_world_error = False
    for line in lines:
        filtered, suppressing_world_error = _filter_rebrowser_stderr_line(
            line,
            suppressing_world_error=suppressing_world_error,
        )
        if filtered is not None:
            forwarded.append(filtered)
    return forwarded


def _filter_enabled() -> bool:
    if os.environ.get("REBROWSER_PATCHES_DEBUG"):
        return False
    value = os.environ.get("JOB_HARNESS_FILTER_REBROWSER_STDERR", "1").strip().casefold()
    return value not in _DISABLED_VALUES


def _forward_filtered_stderr(read_fd: int, original_fd: int) -> None:
    pending = b""
    suppressing_world_error = False
    try:
        with os.fdopen(read_fd, "rb", buffering=0) as stream:
            while chunk := stream.read(4096):
                pending += chunk
                while True:
                    newline_index = pending.find(b"\n")
                    if newline_index == -1:
                        break
                    line = pending[: newline_index + 1]
                    pending = pending[newline_index + 1:]
                    filtered, suppressing_world_error = _filter_rebrowser_stderr_line(
                        line,
                        suppressing_world_error=suppressing_world_error,
                    )
                    if filtered is not None:
                        _write_all(original_fd, filtered)
            if pending:
                filtered, _ = _filter_rebrowser_stderr_line(
                    pending,
                    suppressing_world_error=suppressing_world_error,
                )
                if filtered is not None:
                    _write_all(original_fd, filtered)
    finally:
        os.close(original_fd)


def _filter_rebrowser_stderr_line(
    line: bytes,
    *,
    suppressing_world_error: bool,
) -> tuple[bytes | None, bool]:
    if line.startswith(_WORLD_ERROR_PREFIX):
        return None, True

    if not suppressing_world_error:
        return line, False

    stripped = line.strip()
    if stripped == b"}":
        return None, False
    if line.startswith(_WORLD_ERROR_CONTINUATION_PREFIXES):
        return None, True

    return line, False


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            return
        view = view[written:]
