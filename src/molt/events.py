"""The NDJSON event stream molt commands write for a CI job to read.

One line of JSON per event, LF-terminated, and the file is created **even when there are no
events**: an empty file says "the command ran and had nothing to do", where a missing file cannot
be told apart from a crash before the command started.

Why this module exists
----------------------
Two commands produce ``git-tag`` events -- ``molt git-tag`` and ``molt publish`` -- and two
conformance suites assert their bytes are identical, because a consumer reading the stream cannot
tell which command wrote it. That agreement was previously kept by two copies of the same eight
lines (gap ``PY-8``). It is one copy now, so a third producer inherits the format instead of
re-deriving it.

Keys are **snake_case** (``molt git-tag`` design D6). Upstream's ``utils/output.ts:6-10`` spells
the field ``packageName``; every machine-readable surface in molt is snake_case, and a stream that
switched convention with its producer would be worse than either choice alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["GIT_TAG_EVENT", "git_tag_event", "write_ndjson"]

#: The ``type`` discriminator every git-tag event carries.
GIT_TAG_EVENT = "git-tag"


def git_tag_event(tag: str, package_name: str) -> dict[str, Any]:
    """One ``git-tag`` event: the tag that was created and the package it belongs to."""
    return {"type": GIT_TAG_EVENT, "tag": tag, "package_name": package_name}


def write_ndjson(path: str | Path, events: Iterable[Mapping[str, Any]]) -> None:
    """Write ``events`` to ``path``, one compact JSON object per LF-terminated line.

    Written as **bytes** so no platform newline translation can turn the LF-only stream a
    line-oriented consumer expects into CRLF. Parent directories are created, because the usual
    ``--output`` value in a workflow names a directory the job has not made yet.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(dict(event), separators=(",", ":")) + "\n" for event in events)
    target.write_bytes(lines.encode("utf-8"))
