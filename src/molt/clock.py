"""The one place molt reads the wall clock.

A seam, not a utility. Upstream calls ``new Date()`` inline inside ``getSnapshotSuffix``
(``assemble-release-plan/src/index.ts:38``), which makes "every snapshot version in one plan shares
one timestamp" (research doc 01 section 14.16, README section 3.3) untestable: a plan assembled
inside a single second produces one suffix whether or not the timestamp was computed once. Routing
every read through :func:`now` lets a test freeze it and assert the exact value.

Two rules keep the seam load-bearing:

1. **Call it as ``clock.now()``, never ``from molt.clock import now``.** The test fixture
   (``tests/conftest.py::FrozenClock.freeze``) patches the *module attribute*; a name imported into
   another module's globals keeps pointing at the original function and the freeze silently no-ops.
2. **Nothing else in molt may call ``datetime.now``.** One reader means one timestamp per run is a
   property of the code rather than of how fast the code happens to be.

Standard library only, and it must stay that way -- this is imported wherever a timestamp is needed,
including paths that must not pay pydantic's or rich's import cost.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["now"]


def now() -> datetime:
    """Return the current moment, timezone-aware and in UTC.

    UTC rather than local time because the value ends up in a published version number: a snapshot
    built in two timezones must sort the same way for everyone who installs it.
    """
    return datetime.now(UTC)
